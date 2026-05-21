"""
BO e1 / BO e2 trade types (5-bar consolidation breakout).

Per spec from client:
- 5 overlapping 5-min candles
- ATR(14) on 5-min; base_range filter = atr_pct% * ATR(14)
- LONG stop = base_low, SHORT stop = base_high
- TP uses existing 1:1 / 1.5:1 / 2:1 / 2.5:1 / 3:1 multipliers (from row's profit)
- Trigger price filter X: LONG only if current price >= X, SHORT only if current price <= X
- Sessions: premarket, RTH, and after-hours with TIF DAY or OTH; NOT overnight (8pm–4am ET)
- Entry order: STP when in regular session; STP LMT when outsideRth / extended (OTH)
- Pattern bars: 5-min candles from IB useRTH feed (historical RTH 5-min bars, not 1-min premarket)
- BO e1: trade the first breakout
- BO e2: wait for first breakout + retest + second breakout, then trade
"""

import asyncio
import datetime
import logging
import random

from ib_insync import Order

import Config
from BreakoutScanner import (
    detect_consolidation_breakout,
    evaluate_breakout,
    evaluate_base_only,
)
from StatusUpdate import StatusUpdate


def _parse_bo_params(entry_points_str):
    """entry_points field for BO holds "<atr_pct>,<trigger_price>" (e.g. "10,200")."""
    try:
        s = str(entry_points_str or "")
        parts = s.split(",")
        atr_pct = float(parts[0]) if parts and parts[0] else 10.0
        trigger_price = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
    except Exception:
        atr_pct, trigger_price = 10.0, 0.0
    return atr_pct / 100.0, trigger_price


def _tp_multiplier(profit_type):
    table = {
        Config.takeProfit[0]: 1.0,
        Config.takeProfit[1]: 1.5,
        Config.takeProfit[2]: 2.0,
        Config.takeProfit[3]: 2.5,
    }
    if len(Config.takeProfit) > 4:
        table[Config.takeProfit[4]] = 3.0
    return table.get(profit_type, 1.0)


def _fetch_5min_bars(connection):
    """Lazy contract not needed - caller passes contract."""
    return None


def _safe_fetch_bars(connection, contract):
    """Fetch 5-min RTH bars for the breakout strategies.

    Uses '2 D' duration so the call always returns the previous trading
    day's bars even when invoked pre-market (before today's RTH opens).
    This avoids IBKR Error 162 (HMDS query returned no data) that would
    otherwise occur with '1 D' before 09:30 ET.
    """
    try:
        bars = connection.ib.reqHistoricalData(
            contract=contract,
            endDateTime='',
            formatDate=1,
            whatToShow='TRADES',
            durationStr='2 D',
            barSizeSetting='5 mins',
            useRTH=True,
            keepUpToDate=False,
        )
        return list(bars) if bars else []
    except Exception as e:
        logging.warning("BO: reqHistoricalData(5 mins) failed: %s", e)
        return []


def _last_price(connection, contract):
    try:
        bars = connection.ib.reqHistoricalData(
            contract=contract, endDateTime='', formatDate=1,
            whatToShow='TRADES', durationStr='60 S', barSizeSetting='5 secs',
            useRTH=False, keepUpToDate=False,
        )
        if bars:
            return float(bars[-1].close)
    except Exception as e:
        logging.debug("BO: last-price fetch failed: %s", e)
    return None


async def _bo_strategy(connection, mode, symbol, timeFrame, profit, stopLoss, risk, tif,
                      barType, buySellType, atrPercentage, quantity, pullBackNo, slValue,
                      breakEven, outsideRth, entry_points):
    """mode='e1' or 'e2'. Loops until a valid signal places an order, then returns."""
    # Lazy import to avoid circular import at module load.
    from SendTrade import getContract, _is_extended_outside_rth, _get_current_session

    # ---- Input guards -------------------------------------------------------
    sym_clean = (symbol or "").strip()
    if not sym_clean:
        logging.error("BO %s: ABORT - symbol is empty. Please fill the Symbol field "
                      "in the row before executing.", mode)
        return
    symbol = sym_clean

    atr_factor, trigger_price = _parse_bo_params(entry_points)
    if atr_factor <= 0:
        logging.error("BO %s %s: ABORT - invalid ATR%% in entry_points='%s'. "
                      "Use the BO modal to set a valid ATR percentage.",
                      mode, symbol, entry_points)
        return

    contract = getContract(symbol, None)
    try:
        connection.ib.qualifyContracts(contract)
    except Exception:
        pass
    if not getattr(contract, 'conId', 0):
        logging.error("BO %s %s: ABORT - could not qualify contract (unknown symbol?).",
                      mode, symbol)
        return

    logging.info("BO %s: starting symbol=%s side=%s atr_factor=%.2f trigger=%s profit=%s risk=%s",
                 mode, symbol, buySellType, atr_factor, trigger_price, profit, risk)

    side = (buySellType or 'BUY').upper()
    side_dir = 'LONG' if side == 'BUY' else 'SHORT'

    # e2 state
    first_break_seen = False
    retest_seen = False
    locked_high = None
    locked_low = None

    poll_secs = 30

    # Throttled diagnostic logger: log INFO when the rejection reason changes,
    # otherwise DEBUG. Keeps the log readable while still showing every failed
    # check whenever the state moves.
    last_status_logged = {'status': None, 'reason': None}

    def _log_eval(eval_res, *, suffix=""):
        st = eval_res.get('status')
        rs = eval_res.get('reason') or ''
        m = eval_res.get('metrics') or {}
        # Compose a compact, structured one-liner.
        line = (f"BO {mode} {symbol} [{st}] {rs} | "
                f"base=[{m.get('base_low')}..{m.get('base_high')}] "
                f"range={m.get('base_range')} ATR(14)={m.get('atr14')} "
                f"ATR_thr={m.get('atr_threshold')} "
                f"body={m.get('avg_body')} body_thr={m.get('body_threshold')} "
                f"close={m.get('breakout_close')}{suffix}")
        if (last_status_logged['status'] != st
                or last_status_logged['reason'] != rs):
            logging.info(line)
            last_status_logged['status'] = st
            last_status_logged['reason'] = rs
        else:
            logging.debug(line)

    while True:
        try:
            session = _get_current_session()
            if session == 'OVERNIGHT':
                if last_status_logged['status'] != 'SESSION_OVERNIGHT':
                    logging.info("BO %s %s: blocked - OVERNIGHT session not allowed", mode, symbol)
                    last_status_logged['status'] = 'SESSION_OVERNIGHT'
                    last_status_logged['reason'] = 'overnight'
                await asyncio.sleep(60)
                continue

            bars = _safe_fetch_bars(connection, contract)
            if len(bars) < 20:
                if last_status_logged['status'] != 'NO_DATA':
                    logging.info("BO %s %s: NO_DATA (got %d bars, need >=20)",
                                 mode, symbol, len(bars))
                    last_status_logged['status'] = 'NO_DATA'
                    last_status_logged['reason'] = 'few bars'
                await asyncio.sleep(poll_secs)
                continue

            # -------- BO e1: detect valid base on the last 5 closed bars --------
            # Option A per client: arm STP at base edge BEFORE the breakout,
            # so the broker fires the entry when price actually crosses
            # base_high (BUY) or base_low (SELL).
            if mode == 'e1':
                eval_res = evaluate_base_only(
                    bars, atr_factor=atr_factor, body_factor=0.5,
                )
                _log_eval(eval_res)
                if eval_res.get('status') != 'OK_BASE':
                    await asyncio.sleep(poll_secs)
                    continue
                m = eval_res['metrics']
                sig = {
                    'direction': side_dir,
                    'base_high': float(m['base_high']),
                    'base_low': float(m['base_low']),
                }

            # -------- BO e2: first breakout + retest, then arm same STP --------
            # Per client: BO e2 is "basically a duplicate order, activated
            # after the trigger, and price goes back down". So once we see
            # first breakout + retest, we arm the SAME entry/SL as e1.
            elif mode == 'e2':
                eval_res = evaluate_breakout(
                    bars,
                    atr_factor=atr_factor,
                    body_factor=0.5,
                    require_volume_decline=False,
                    require_above_vwap=False,
                )
                _log_eval(eval_res)
                sig = eval_res.get('signal')
                last_bar = bars[-1]
                if not first_break_seen:
                    if sig and sig['direction'] == side_dir:
                        first_break_seen = True
                        locked_high = sig['base_high']
                        locked_low = sig['base_low']
                        logging.info(
                            "BO e2 %s: STAGE 1 OK - first breakout. "
                            "base=[%s..%s] dir=%s close=%s",
                            symbol, locked_low, locked_high, sig['direction'],
                            sig.get('breakout_price'),
                        )
                    await asyncio.sleep(poll_secs)
                    continue

                # Retest: price has come back to base_high (long) or base_low (short)
                if not retest_seen:
                    if side == 'BUY' and float(last_bar.low) <= locked_high:
                        retest_seen = True
                        logging.info(
                            "BO e2 %s: STAGE 2 OK - retest (low=%s <= base_high=%s)",
                            symbol, last_bar.low, locked_high,
                        )
                    elif side == 'SELL' and float(last_bar.high) >= locked_low:
                        retest_seen = True
                        logging.info(
                            "BO e2 %s: STAGE 2 OK - retest (high=%s >= base_low=%s)",
                            symbol, last_bar.high, locked_low,
                        )
                    else:
                        if last_status_logged['status'] != 'WAIT_RETEST':
                            logging.info(
                                "BO e2 %s: waiting for retest. last bar low=%s high=%s vs base=[%s..%s]",
                                symbol, last_bar.low, last_bar.high,
                                locked_low, locked_high,
                            )
                            last_status_logged['status'] = 'WAIT_RETEST'
                            last_status_logged['reason'] = 'no retest yet'
                        await asyncio.sleep(poll_secs)
                        continue

                # Stage 3: retest seen -> arm the SAME STP as BO e1 and let
                # the broker fire when price crosses the base edge again.
                logging.info(
                    "BO e2 %s: STAGE 3 - retest seen; arming duplicate STP "
                    "at base edge. base=[%s..%s]",
                    symbol, locked_low, locked_high,
                )
                sig = {
                    'direction': side_dir,
                    'base_high': float(locked_high),
                    'base_low': float(locked_low),
                }

            # -------- common: trigger-price filter --------
            cur_px = _last_price(connection, contract)
            if cur_px is None:
                cur_px = float(sig.get('breakout_price') or sig.get('base_high') or 0)
            if trigger_price and trigger_price > 0:
                if side == 'BUY' and cur_px < trigger_price:
                    if last_status_logged['status'] != 'TRIGGER_BLOCK_BUY':
                        logging.info(
                            "BO %s %s: TRIGGER FILTER BLOCKS BUY (cur=%.2f < X=%.2f)",
                            mode, symbol, cur_px, trigger_price,
                        )
                        last_status_logged['status'] = 'TRIGGER_BLOCK_BUY'
                        last_status_logged['reason'] = 'below X'
                    await asyncio.sleep(60)
                    continue
                if side == 'SELL' and cur_px > trigger_price:
                    if last_status_logged['status'] != 'TRIGGER_BLOCK_SELL':
                        logging.info(
                            "BO %s %s: TRIGGER FILTER BLOCKS SELL (cur=%.2f > X=%.2f)",
                            mode, symbol, cur_px, trigger_price,
                        )
                        last_status_logged['status'] = 'TRIGGER_BLOCK_SELL'
                        last_status_logged['reason'] = 'above X'
                    await asyncio.sleep(60)
                    continue
                logging.info(
                    "BO %s %s: TRIGGER FILTER PASS (cur=%.2f vs X=%.2f, side=%s)",
                    mode, symbol, cur_px, trigger_price, side,
                )

            # -------- compute order levels (Option A per client) --------
            # BUY:  entry STP = base_high + 0.01,  SL = base_low - 0.01
            # SELL: entry STP = base_low  - 0.01,  SL = base_high + 0.01
            base_high = float(sig['base_high'])
            base_low = float(sig['base_low'])
            if side == 'BUY':
                aux_price = round(base_high + 0.01, Config.roundVal)
                stop_loss_price = round(base_low - 0.01, Config.roundVal)
            else:
                aux_price = round(base_low - 0.01, Config.roundVal)
                stop_loss_price = round(base_high + 0.01, Config.roundVal)

            stop_size = abs(aux_price - stop_loss_price)
            if stop_size <= 0:
                logging.warning("BO %s: invalid stop_size=%s, aborting", mode, stop_size)
                return

            try:
                risk_amt = float(risk) if str(risk) else 0
            except Exception:
                risk_amt = 0
            qty = max(1, int(round(risk_amt / stop_size))) if risk_amt > 0 else 1

            mult = _tp_multiplier(profit)
            if side == 'BUY':
                tp_price = round(aux_price + mult * stop_size, Config.roundVal)
            else:
                tp_price = round(aux_price - mult * stop_size, Config.roundVal)

            # Entry order type: STP in RTH; STP LMT in extended hours
            # (limit = aux ± 0.5 * stop_size, mirrors RB/LB extended-hours pattern)
            is_extended, _sess = _is_extended_outside_rth(outsideRth)
            if is_extended:
                entry_type = 'STP LMT'
                limit_off = round(stop_size * 0.5, Config.roundVal)
                entry_lmt = round(aux_price + limit_off, Config.roundVal) if side == 'BUY' \
                    else round(aux_price - limit_off, Config.roundVal)
            else:
                entry_type = 'STP'
                entry_lmt = 0.0

            # Safety: if current price has already crossed the STP trigger
            # before we send the order, log a warning. IBKR may reject the
            # STP - operator can intervene or restart the row.
            if cur_px and (
                (side == 'BUY' and cur_px >= aux_price)
                or (side == 'SELL' and cur_px <= aux_price)
            ):
                logging.warning(
                    "BO %s %s: WARNING - current price %s has already crossed "
                    "STP trigger %s. Order may be rejected by IBKR.",
                    mode, symbol, cur_px, aux_price,
                )

            # -------- build & send bracket --------
            # Use IBKR's monotonically-increasing order id counter so we
            # never collide with an id IBKR already knows about.
            # Falling back to the time+random scheme only if getReqId is
            # unavailable on this ib_insync version.
            try:
                parent_id = connection.ib.client.getReqId()
                tp_id = connection.ib.client.getReqId()
                sl_id = connection.ib.client.getReqId()
            except Exception as _e:
                logging.warning("BO %s: getReqId unavailable (%s), using fallback id", mode, _e)
                now = datetime.datetime.now().time()
                parent_id = (random.randint(3, 500)
                             + now.hour + now.minute + now.second + now.microsecond)
                tp_id = parent_id + 1
                sl_id = parent_id + 2

            action = side
            opp = 'SELL' if action == 'BUY' else 'BUY'

            ent_kwargs = dict(
                orderId=parent_id, orderType=entry_type, action=action,
                totalQuantity=qty, auxPrice=aux_price, tif=tif, transmit=False,
            )
            if entry_type == 'STP LMT':
                ent_kwargs['lmtPrice'] = entry_lmt
            entry_order = Order(**ent_kwargs)

            tp_order = Order(
                orderId=tp_id, orderType='LMT', action=opp,
                totalQuantity=qty, lmtPrice=tp_price,
                tif=tif, parentId=parent_id, transmit=False,
            )
            sl_order = Order(
                orderId=sl_id, orderType='STP', action=opp,
                totalQuantity=qty, auxPrice=stop_loss_price,
                tif=tif, parentId=parent_id, transmit=True,
            )

            last_bar = bars[-1]
            histData = {
                'date': getattr(last_bar, 'date', datetime.datetime.now()),
                'open': float(last_bar.open),
                'high': float(last_bar.high),
                'low': float(last_bar.low),
                'close': float(last_bar.close),
            }
            last_price = float(last_bar.close)

            logging.info(
                "BO %s: placing bracket side=%s entry(%s)=%s SL=%s TP=%s qty=%s stop_size=%s base[%s..%s]",
                mode, side, entry_type, aux_price, stop_loss_price, tp_price, qty,
                round(stop_size, 4), base_low, base_high,
            )

            entry_res = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
            StatusUpdate(entry_res, 'Entry', contract, entry_type, action, qty, histData, last_price,
                         symbol, '5 mins', profit, stopLoss, risk, '', tif, barType, buySellType,
                         atrPercentage, slValue, breakEven, outsideRth, False, entry_points)
            tp_res = connection.placeTrade(contract=contract, order=tp_order, outsideRth=outsideRth)
            StatusUpdate(tp_res, 'TakeProfit', contract, 'LMT', action, qty, histData, last_price,
                         symbol, '5 mins', profit, stopLoss, risk, '', tif, barType, buySellType,
                         atrPercentage, slValue, breakEven, outsideRth)
            sl_res = connection.placeTrade(contract=contract, order=sl_order, outsideRth=outsideRth)
            StatusUpdate(sl_res, 'StopLoss', contract, 'STP', action, qty, histData, last_price,
                         symbol, '5 mins', profit, stopLoss, risk, '', tif, barType, buySellType,
                         atrPercentage, slValue, breakEven, outsideRth)

            logging.info("BO %s: bracket placed, strategy exiting.", mode)
            return
        except asyncio.CancelledError:
            logging.info("BO %s: cancelled", mode)
            raise
        except Exception as e:
            logging.error("BO %s: loop error: %s", mode, e)
            await asyncio.sleep(poll_secs)


async def bo_e1(connection, symbol, timeFrame, profit, stopLoss, risk, tif,
                barType, buySellType, atrPercentage, quantity, pullBackNo, slValue,
                breakEven, outsideRth, entry_points):
    return await _bo_strategy(connection, 'e1', symbol, timeFrame, profit, stopLoss, risk, tif,
                              barType, buySellType, atrPercentage, quantity, pullBackNo, slValue,
                              breakEven, outsideRth, entry_points)


async def bo_e2(connection, symbol, timeFrame, profit, stopLoss, risk, tif,
                barType, buySellType, atrPercentage, quantity, pullBackNo, slValue,
                breakEven, outsideRth, entry_points):
    return await _bo_strategy(connection, 'e2', symbol, timeFrame, profit, stopLoss, risk, tif,
                              barType, buySellType, atrPercentage, quantity, pullBackNo, slValue,
                              breakEven, outsideRth, entry_points)

"""
5-Bar Consolidation + Breakout Detector (Project 1)

Pure, self-contained module. No edits to existing strategy logic.

Public API:
    detect_consolidation_breakout(bars, ...)  -> signal dict | None
    async scan_once(connection, symbol, params, on_signal) -> signal | None
    async scanner_loop(connection, get_state, on_signal)
"""

import asyncio
import datetime
import logging

import numpy as np

import Config

try:
    from SendTrade import getContract
except Exception:
    getContract = None  # falls back at call-site if import order is awkward


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _wilder_atr(highs, lows, closes, period=14):
    n = len(highs)
    if n == 0:
        return np.array([])
    tr = np.empty(n, dtype=float)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = np.full(n, np.nan, dtype=float)
    if n >= period:
        atr[period - 1] = float(np.mean(tr[:period]))
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _session_vwap_up_to(bars, end_idx):
    if end_idx < 0 or end_idx >= len(bars):
        return None
    end_day = bars[end_idx].date.date() if hasattr(bars[end_idx].date, 'date') else None
    if end_day is None:
        return None
    start_idx = end_idx
    while start_idx > 0 and bars[start_idx - 1].date.date() == end_day:
        start_idx -= 1
    typ = []
    vol = []
    for i in range(start_idx, end_idx + 1):
        b = bars[i]
        typ.append((float(b.high) + float(b.low) + float(b.close)) / 3.0)
        vol.append(float(getattr(b, 'volume', 0) or 0))
    typ = np.array(typ, dtype=float)
    vol = np.array(vol, dtype=float)
    if vol.sum() <= 0:
        return None
    return float((typ * vol).sum() / vol.sum())


def evaluate_breakout(
    bars,
    *,
    atr_factor=0.75,
    body_factor=0.5,
    require_volume_decline=False,
    require_above_vwap=False,
):
    """Detailed evaluation of the 5-bar base + breakout for diagnostics.

    Returns a dict with at minimum:
        status:  one of NO_DATA / NO_ATR / RANGE_TOO_WIDE / NO_OVERLAP /
                 BODY_TOO_BIG / VOLUME_NOT_DECLINING / NO_BREAKOUT /
                 BELOW_VWAP / OK_LONG / OK_SHORT
        reason:  human-readable message
        signal:  same dict as detect_consolidation_breakout, or None
        metrics: dict of intermediate numbers (None entries when unavailable)
    """
    if bars is None or len(bars) < 20:
        return {
            'status': 'NO_DATA',
            'reason': f"Need >=20 bars (got {0 if not bars else len(bars)})",
            'signal': None,
            'metrics': {},
        }

    highs = np.array([float(b.high) for b in bars])
    lows = np.array([float(b.low) for b in bars])
    closes = np.array([float(b.close) for b in bars])
    opens = np.array([float(b.open) for b in bars])
    volumes = np.array([float(getattr(b, 'volume', 0) or 0) for b in bars])

    atr = _wilder_atr(highs, lows, closes, period=14)

    n = len(bars)
    br_idx = n - 1
    base_start = br_idx - 5
    base_end = br_idx - 1
    if base_start < 0:
        return {'status': 'NO_DATA', 'reason': 'Not enough bars for 5-bar base',
                'signal': None, 'metrics': {}}

    bh = highs[base_start:base_end + 1]
    bl = lows[base_start:base_end + 1]
    bo_ = opens[base_start:base_end + 1]
    bc = closes[base_start:base_end + 1]
    bv = volumes[base_start:base_end + 1]

    base_high = float(bh.max())
    base_low = float(bl.min())
    base_range = base_high - base_low
    atr_val = float(atr[base_end]) if not np.isnan(atr[base_end]) else 0.0
    br_close = float(closes[br_idx])

    avg_body = float(np.mean(np.abs(bc - bo_)))
    avg_range = float(np.mean(bh - bl))

    metrics = {
        'base_high': round(base_high, 4),
        'base_low': round(base_low, 4),
        'base_range': round(base_range, 4),
        'atr14': round(atr_val, 4),
        'atr_threshold': round(atr_factor * atr_val, 4),
        'avg_body': round(avg_body, 4),
        'avg_range': round(avg_range, 4),
        'body_threshold': round(body_factor * avg_range, 4) if avg_range > 0 else None,
        'breakout_close': round(br_close, 4),
        'overlap_fail_pair': None,
    }

    if atr_val <= 0:
        return {'status': 'NO_ATR', 'reason': 'ATR(14) unavailable / zero',
                'signal': None, 'metrics': metrics}

    # 1) Range vs ATR
    if base_range > atr_factor * atr_val:
        return {
            'status': 'RANGE_TOO_WIDE',
            'reason': (f"base_range={base_range:.4f} > {atr_factor*100:.0f}% * ATR(14)="
                       f"{atr_factor*atr_val:.4f}"),
            'signal': None, 'metrics': metrics,
        }

    # 2) Overlap rule
    for i in range(1, 5):
        if max(bl[i - 1], bl[i]) > min(bh[i - 1], bh[i]):
            metrics['overlap_fail_pair'] = (int(i - 1), int(i))
            return {
                'status': 'NO_OVERLAP',
                'reason': (f"bars {i-1} and {i} do not overlap "
                           f"(low_pair_max={max(bl[i-1], bl[i]):.4f} > "
                           f"high_pair_min={min(bh[i-1], bh[i]):.4f})"),
                'signal': None, 'metrics': metrics,
            }

    # 3) Body filter disabled per client request
    # (Original spec required small bodies, but client asked to remove this
    # check so that trending setups are also eligible.)
    if avg_range <= 0:
        return {'status': 'NO_DATA', 'reason': 'avg_range is zero',
                'signal': None, 'metrics': metrics}

    # 4) Optional declining volume
    if require_volume_decline:
        if bv.sum() <= 0:
            return {'status': 'VOLUME_NOT_DECLINING',
                    'reason': 'No volume data available',
                    'signal': None, 'metrics': metrics}
        deltas = np.diff(bv)
        negs = int(np.sum(deltas < 0))
        if negs < 3:
            return {
                'status': 'VOLUME_NOT_DECLINING',
                'reason': f"Only {negs}/4 base-bar volume deltas are negative (need >=3)",
                'signal': None, 'metrics': metrics,
            }

    # 5) Breakout direction on latest closed bar
    if br_close > base_high:
        direction = 'LONG'
    elif br_close < base_low:
        direction = 'SHORT'
    else:
        return {
            'status': 'NO_BREAKOUT',
            'reason': (f"breakout_close={br_close:.4f} not outside base "
                       f"[{base_low:.4f}..{base_high:.4f}]"),
            'signal': None, 'metrics': metrics,
        }

    # 6) Optional VWAP filter (only for LONG per spec)
    vwap_val = None
    above_vwap = None
    if require_above_vwap:
        vwap_val = _session_vwap_up_to(bars, br_idx)
        if direction == 'LONG':
            if vwap_val is None or br_close <= vwap_val:
                metrics['vwap'] = (round(vwap_val, 4) if vwap_val is not None else None)
                return {
                    'status': 'BELOW_VWAP',
                    'reason': (f"LONG breakout_close={br_close:.4f} not above VWAP="
                               f"{vwap_val}"),
                    'signal': None, 'metrics': metrics,
                }
            above_vwap = True
        else:
            above_vwap = (vwap_val is not None and br_close > vwap_val)

    signal = {
        'timeframe': '5 mins',
        'base_start_time': bars[base_start].date,
        'base_end_time': bars[base_end].date,
        'base_high': round(base_high, 2),
        'base_low': round(base_low, 2),
        'base_range': round(base_range, 4),
        'atr14': round(atr_val, 4),
        'direction': direction,
        'breakout_time': bars[br_idx].date,
        'breakout_price': round(br_close, 2),
        'extras': {
            'avg_body': round(avg_body, 4),
            'avg_range': round(avg_range, 4),
            'vwap': (round(vwap_val, 4) if vwap_val is not None else None),
            'above_vwap': above_vwap,
        },
    }
    return {
        'status': f'OK_{direction}',
        'reason': (f"Valid base {base_low:.2f}-{base_high:.2f} (range={base_range:.4f} <= "
                   f"{atr_factor*100:.0f}% ATR={atr_factor*atr_val:.4f}); "
                   f"breakout_close={br_close:.4f} -> {direction}"),
        'signal': signal,
        'metrics': metrics,
    }


def evaluate_base_only(
    bars,
    *,
    atr_factor=0.75,
    body_factor=0.5,
):
    """Validate a 5-bar consolidation base using the LAST 5 CLOSED bars (no
    separate breakout bar required). Mirrors the RB/FB pattern of arming an
    entry STP using the most recent completed bar(s).

    Returns the same shape as evaluate_breakout but without 'direction'/'signal'
    derived from a breakout candle.

    Statuses: NO_DATA / NO_ATR / RANGE_TOO_WIDE / NO_OVERLAP / BODY_TOO_BIG /
              OK_BASE
    """
    if bars is None or len(bars) < 19:  # need 14 ATR + 5 base
        return {
            'status': 'NO_DATA',
            'reason': f"Need >=19 bars (got {0 if not bars else len(bars)})",
            'metrics': {},
        }

    highs = np.array([float(b.high) for b in bars])
    lows = np.array([float(b.low) for b in bars])
    closes = np.array([float(b.close) for b in bars])
    opens = np.array([float(b.open) for b in bars])

    atr = _wilder_atr(highs, lows, closes, period=14)

    n = len(bars)
    base_start = n - 5
    base_end = n - 1  # inclusive: last closed bar IS the 5th base bar

    bh = highs[base_start:base_end + 1]
    bl = lows[base_start:base_end + 1]
    bo_ = opens[base_start:base_end + 1]
    bc = closes[base_start:base_end + 1]

    base_high = float(bh.max())
    base_low = float(bl.min())
    base_range = base_high - base_low
    atr_val = float(atr[base_end]) if not np.isnan(atr[base_end]) else 0.0

    avg_body = float(np.mean(np.abs(bc - bo_)))
    avg_range = float(np.mean(bh - bl))

    metrics = {
        'base_high': round(base_high, 4),
        'base_low': round(base_low, 4),
        'base_range': round(base_range, 4),
        'atr14': round(atr_val, 4),
        'atr_threshold': round(atr_factor * atr_val, 4),
        'avg_body': round(avg_body, 4),
        'avg_range': round(avg_range, 4),
        'body_threshold': round(body_factor * avg_range, 4) if avg_range > 0 else None,
        'base_start_time': bars[base_start].date,
        'base_end_time': bars[base_end].date,
        'overlap_fail_pair': None,
    }

    if atr_val <= 0:
        return {'status': 'NO_ATR', 'reason': 'ATR(14) unavailable / zero',
                'metrics': metrics}
    if base_range > atr_factor * atr_val:
        return {
            'status': 'RANGE_TOO_WIDE',
            'reason': (f"base_range={base_range:.4f} > {atr_factor*100:.0f}% * ATR(14)="
                       f"{atr_factor*atr_val:.4f}"),
            'metrics': metrics,
        }
    for i in range(1, 5):
        if max(bl[i - 1], bl[i]) > min(bh[i - 1], bh[i]):
            metrics['overlap_fail_pair'] = (int(i - 1), int(i))
            return {
                'status': 'NO_OVERLAP',
                'reason': (f"bars {i-1} and {i} do not overlap "
                           f"(low_pair_max={max(bl[i-1], bl[i]):.4f} > "
                           f"high_pair_min={min(bh[i-1], bh[i]):.4f})"),
                'metrics': metrics,
            }
    if avg_range <= 0:
        return {'status': 'NO_DATA', 'reason': 'avg_range is zero', 'metrics': metrics}
    # Body filter disabled per client request (was: avg_body > body_factor * avg_range)

    return {
        'status': 'OK_BASE',
        'reason': (f"Valid base {base_low:.2f}-{base_high:.2f} "
                   f"(range={base_range:.4f} <= {atr_factor*100:.0f}% ATR="
                   f"{atr_factor*atr_val:.4f})"),
        'metrics': metrics,
    }


def detect_consolidation_breakout(
    bars,
    *,
    atr_factor=0.75,
    body_factor=0.5,
    require_volume_decline=False,
    require_above_vwap=False,
):
    """Backward-compatible wrapper that returns signal dict or None.

    `bars` must be sorted oldest-first; each bar must expose attributes
    `date, open, high, low, close, volume` (ib_insync BarData satisfies this).
    """
    res = evaluate_breakout(
        bars,
        atr_factor=atr_factor,
        body_factor=body_factor,
        require_volume_decline=require_volume_decline,
        require_above_vwap=require_above_vwap,
    )
    return res.get('signal')


# ---------------------------------------------------------------------------
# Async scanner
# ---------------------------------------------------------------------------

async def scan_once(connection, symbol, params, on_signal):
    """Scan a single symbol once. Returns the signal dict or None."""
    try:
        if getContract is None:
            logging.warning("Scanner: getContract not available, skipping %s", symbol)
            return None
        contract = getContract(symbol, None)
        try:
            connection.ib.qualifyContracts(contract)
        except Exception:
            pass

        bars = connection.ib.reqHistoricalData(
            contract=contract,
            endDateTime='',
            formatDate=1,
            whatToShow='TRADES',
            durationStr=Config.scanner_duration,
            barSizeSetting=Config.scanner_timeframe,
            useRTH=True,
            keepUpToDate=False,
        )
        if not bars or len(bars) < 20:
            logging.debug("Scanner: not enough bars for %s (got %d)", symbol, 0 if not bars else len(bars))
            return None

        sig = detect_consolidation_breakout(
            list(bars),
            atr_factor=float(params.get('atr_factor', Config.scanner_atr_factor)),
            body_factor=float(params.get('body_factor', Config.scanner_body_factor)),
            require_volume_decline=bool(params.get('require_volume_decline', Config.scanner_require_volume_decline)),
            require_above_vwap=bool(params.get('require_above_vwap', Config.scanner_require_above_vwap)),
        )
        if sig:
            sig['symbol'] = symbol
            logging.info("Scanner signal: %s", sig)
            try:
                on_signal(sig)
            except Exception as cb_err:
                logging.error("Scanner on_signal error: %s", cb_err)
        return sig
    except Exception as e:
        logging.error("Scanner error for %s: %s", symbol, e)
        return None


def _next_bar_close_eta_seconds(bar_minutes=5):
    now = datetime.datetime.now()
    minute_block = (now.minute // bar_minutes + 1) * bar_minutes
    if minute_block >= 60:
        next_close = (now.replace(second=0, microsecond=0, minute=0)
                      + datetime.timedelta(hours=1))
    else:
        next_close = now.replace(second=0, microsecond=0, minute=minute_block)
    # Add 2-second buffer so IB has flushed the closing bar.
    return max(5.0, (next_close - now).total_seconds() + 2.0)


async def scanner_loop(connection, get_state, on_signal):
    """Repeatedly scan symbols just after each 5-min bar close.

    `get_state()` must return {'running': bool, 'symbols': [str], 'params': {...}}.
    """
    logging.info("Breakout scanner: loop started")
    # Run an immediate first pass so user sees output quickly.
    first_pass = True
    try:
        while True:
            st = get_state()
            if not st.get('running'):
                break
            symbols = [s.strip().upper() for s in st.get('symbols', []) if s and s.strip()]
            params = st.get('params', {})
            for sym in symbols:
                if not get_state().get('running'):
                    break
                await scan_once(connection, sym, params, on_signal)
                await asyncio.sleep(0.4)  # pace IB historical requests

            if first_pass:
                first_pass = False
            wait_s = _next_bar_close_eta_seconds(5)
            # Sleep in small slices so Stop is responsive.
            end = datetime.datetime.now() + datetime.timedelta(seconds=wait_s)
            while datetime.datetime.now() < end:
                if not get_state().get('running'):
                    break
                await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        logging.info("Breakout scanner: loop cancelled")
        raise
    except Exception as e:
        logging.error("Breakout scanner: loop crashed: %s", e)
    finally:
        logging.info("Breakout scanner: loop stopped")

import asyncio
import datetime
import math
import threading
import time
import traceback

import Config
from header import *
from SendTrade import *

class connection:

    def __init__(self):
        self.ib = IB()
        self._order_id_lock = threading.Lock()
        self._order_id_counter = None

    # it will set trade order status value in global variable.
    def orderStatusEvent(self,trade: Trade):
        if  trade.orderStatus.status == 'Filled':
            Config.orderFilledPrice.update({ trade.order.orderId :  trade.orderStatus.avgFillPrice })

        if Config.orderStatusData.get(trade.order.orderId) != None:
            data = Config.orderStatusData.get(trade.order.orderId)
            data.update({'status': trade.orderStatus.status})
            if hasattr(trade.orderStatus, 'whyHeld') and trade.orderStatus.whyHeld:
                data.update({'whyHeld': trade.orderStatus.whyHeld})
            Config.orderStatusData.update({trade.order.orderId: data})
            # Exclude manual orders (Stop Order, Limit Order) and RBB from sendTpAndSl during regular hours
            # because they already send bracket orders. Extended hours manual orders and RBB need sendTpAndSl.
            is_manual_order = data.get('barType', '') in Config.manualOrderTypes
            is_extended_hours = data.get('outsideRth', False)
            ord_type = data.get('ordType', '')
            bar_type = data.get('barType', '')
            
            # Detailed logging for debugging
            logging.info("orderStatusEvent: orderId=%s, status=%s, barType=%s, ordType=%s, outsideRth=%s, is_manual_order=%s, is_extended_hours=%s",
                        trade.order.orderId, trade.orderStatus.status, bar_type, ord_type, is_extended_hours, is_manual_order, is_extended_hours)
            
            # Logic: Send TP/SL if:
            # 1. It's a manual order in extended hours (they need TP/SL after fill, not bracket orders)
            # 2. OR it's not a manual order (other trade types always need TP/SL)
            # 3. AND it's not FB (entryTradeType[0]) - but wait, entryTradeType[0] is actually "Stop Order" now
            # Actually, the original logic was checking if barType != entryTradeType[0] to exclude FB
            # But since entryTradeType now starts with manualOrderTypes, we need different logic
            
            # For manual orders: send TP/SL only in extended hours (not in regular hours where bracket orders are used)
            # For other trade types: send TP/SL (except FB and PBe1 which use bracket orders in regular hours)
            if is_manual_order:
                # Manual orders: only send TP/SL in extended hours
                should_send_tp_sl = is_extended_hours
            else:
                # Other trade types: send TP/SL (except FB, Conditional Order, RB, RBB, and PBe1 which use bracket orders in regular hours)
                # Since entryTradeType = manualOrderTypes + ['Conditional Order', 'FB', ...], FB is at index 3
                # manualOrderTypes = ['Stop Order', 'Limit Order'] (indices 0, 1)
                # entryTradeType[2] = 'Conditional Order', entryTradeType[3] = 'FB', entryTradeType[4] = 'RB', entryTradeType[5] = 'RBB', entryTradeType[6] = 'PBe1'
                conditional_order_index = 2
                fb_index = 3
                rb_index = 4
                rbb_index = 5
                pbe1_index = 6
                # Exclude FB, Conditional Order, and RB in regular hours (they use bracket orders)
                # Conditional Order uses bracket orders in RTH (like Custom entry), but needs sendTpAndSl in extended hours
                # RBB and PBe1 place only entry order in RTH, TP/SL sent after fill (like RBB)
                # In extended hours, RB and RBB still need sendTpAndSl (don't use bracket orders)
                is_conditional_order = data['barType'] == Config.entryTradeType[conditional_order_index] if len(Config.entryTradeType) > conditional_order_index else False
                is_fb = data['barType'] == Config.entryTradeType[fb_index]
                is_rb = data['barType'] == Config.entryTradeType[rb_index] if len(Config.entryTradeType) > rb_index else False
                is_rbb = data['barType'] == Config.entryTradeType[rbb_index] if len(Config.entryTradeType) > rbb_index else False
                is_pbe1 = data['barType'] == Config.entryTradeType[pbe1_index] if len(Config.entryTradeType) > pbe1_index else False
                if is_conditional_order:
                    should_send_tp_sl = is_extended_hours  # Conditional Order uses bracket orders in RTH, separate orders in extended hours
                elif is_fb:
                    should_send_tp_sl = False  # FB always uses bracket orders
                elif is_rb:
                    should_send_tp_sl = is_extended_hours  # RB uses bracket orders in RTH, separate orders in extended hours
                elif is_rbb:
                    should_send_tp_sl = True  # RBB places only entry order in RTH, TP/SL sent after fill
                elif is_pbe1:
                    should_send_tp_sl = True  # PBe1 places only entry order in RTH (like RBB), TP/SL sent after fill
                else:
                    # Check if this is LB, LB2, or LB3
                    lb_index = 8
                    lb2_index = 9
                    lb3_index = 10
                    is_lb = data['barType'] == Config.entryTradeType[lb_index] if len(Config.entryTradeType) > lb_index else False
                    is_lb2 = data['barType'] == Config.entryTradeType[lb2_index] if len(Config.entryTradeType) > lb2_index else False
                    is_lb3 = data['barType'] == Config.entryTradeType[lb3_index] if len(Config.entryTradeType) > lb3_index else False
                    if is_lb or is_lb2 or is_lb3:
                        should_send_tp_sl = is_extended_hours  # LB/LB2/LB3 use bracket orders in RTH, separate orders in extended hours
                    else:
                        should_send_tp_sl = True  # Other trade types always need sendTpAndSl
            
            logging.info("orderStatusEvent: should_send_tp_sl=%s (barType != entryTradeType[3] (FB): %s, not (is_manual_order and not is_extended_hours): %s)",
                        should_send_tp_sl, 
                        data['barType'] != Config.entryTradeType[3],
                        not (is_manual_order and not is_extended_hours))

            # ------------------------------------------------------------------
            # Immediate recovery for manual Custom brackets on Duplicate order id
            # ------------------------------------------------------------------
            #
            # If a manual Custom bracket entry is cancelled with IB Error 103
            # ("Duplicate order id"), we do NOT want to leave the user with no
            # active orders. Instead, we:
            #
            # - Detect the duplicate-id cancellation from the trade.log.
            # - Generate a brand-new bracket (entry, TP, SL) with fresh IDs.
            # - Place the new bracket immediately, without any artificial delay.
            #
            # This is similar in spirit to the Conditional Order fix, but scoped
            # only to manual Custom (barType='Custom', is_manual_order=True).
            duplicate_id_cancel = False
            try:
                if trade.orderStatus.status == 'Cancelled' and bar_type == 'Custom' and ord_type == 'Entry':
                    # Inspect latest TradeLogEntry to see if this was Error 103
                    last_log = trade.log[-1] if hasattr(trade, "log") and trade.log else None
                    if last_log is not None and getattr(last_log, "errorCode", None) == 103:
                        duplicate_id_cancel = True
            except Exception as log_err:
                logging.debug("orderStatusEvent: Unable to inspect trade.log for duplicate-id detection: %s", log_err)

            if duplicate_id_cancel and is_manual_order:
                # Avoid infinite regeneration loops: only retry once per entry.
                already_retried = data.get('duplicateIdRetryDone', False)
                if already_retried:
                    logging.warning(
                        "orderStatusEvent: Duplicate-id cancellation for manual Custom entry %s "
                        "but duplicateIdRetryDone is already True. Skipping regeneration to avoid loop.",
                        trade.order.orderId,
                    )
                else:
                    # Mark as retried for this original entry id; new entry will
                    # also carry duplicateIdRetryDone=True in its copied state.
                    data['duplicateIdRetryDone'] = True
                    Config.orderStatusData[trade.order.orderId] = data
                    try:
                        # Extract common state
                        contract = data.get('contract')
                        tif = data.get('tif', 'DAY')
                        buy_sell_type = data.get('tradeType') or data.get('action') or trade.order.action
                        outside_rth_flag = data.get('outsideRth', False)
                        entry_price = data.get('lastPrice', trade.order.auxPrice)
                        qty = data.get('quantity', trade.order.totalQuantity)

                        if not contract:
                            logging.error(
                                "orderStatusEvent: Cannot regenerate manual Custom entry for %s - contract missing in orderStatusData",
                                trade.order.orderId,
                            )
                        elif is_extended_hours:
                            # ------------------------------------------------------------------
                            # Extended hours (PREMARKET/AFTERHOURS/OVERNIGHT) manual Custom
                            # ------------------------------------------------------------------
                            # Only the entry STP LMT exists; TP/SL are sent after fill via
                            # sendTpAndSl. Here we regenerate just the entry STP LMT with a
                            # fresh orderId and identical pricing.
                            entry_limit_price = data.get('entryLimitPrice')
                            if entry_limit_price is None:
                                logging.error(
                                    "orderStatusEvent: Cannot regenerate extended-hours manual Custom entry for %s - "
                                    "entryLimitPrice missing in orderStatusData",
                                    trade.order.orderId,
                                )
                            else:
                                new_entry_id = self.get_next_order_id()
                                logging.info(
                                    "orderStatusEvent: Regenerating extended-hours manual Custom entry with new ID %s "
                                    "(old entry=%s, stop=%s, limit=%s, qty=%s)",
                                    new_entry_id,
                                    trade.order.orderId,
                                    entry_price,
                                    entry_limit_price,
                                    qty,
                                )
                                entry_order = Order(
                                    orderId=new_entry_id,
                                    orderType="STP LMT",
                                    action=buy_sell_type,
                                    totalQuantity=qty,
                                    auxPrice=entry_price,
                                    lmtPrice=entry_limit_price,
                                    tif=tif,
                                )
                                entry_resp = self.placeTrade(contract=contract, order=entry_order, outsideRth=outside_rth_flag)
                                logging.info(
                                    "orderStatusEvent: Regenerated extended-hours manual Custom entry placed - newOrderId=%s, status=%s",
                                    entry_resp.order.orderId,
                                    entry_resp.orderStatus.status,
                                )
                                # Copy state to new entry id
                                new_state = dict(data)
                                new_state['orderId'] = new_entry_id
                                new_state['duplicateIdRetryDone'] = True
                                Config.orderStatusData[new_entry_id] = new_state
                        else:
                            # ------------------------------------------------------------------
                            # Regular-hours manual Custom: full bracket regeneration
                            # ------------------------------------------------------------------
                            logging.warning(
                                "orderStatusEvent: Detected Duplicate order id (103) for manual Custom entry %s. "
                                "Regenerating full bracket with new order IDs.",
                                trade.order.orderId,
                            )

                            # Additional state needed for bracket
                            tp_price = data.get('tp_price')
                            stop_loss_price = data.get('stop_loss_price') or data.get('stopLossPrice')
                            stop_size = data.get('stopSize')

                            if tp_price is None or stop_loss_price is None or stop_size is None:
                                logging.error(
                                    "orderStatusEvent: Cannot regenerate manual Custom bracket for %s - "
                                    "tp_price/stop_loss_price/stopSize missing (tp=%s, sl=%s, stopSize=%s)",
                                    trade.order.orderId,
                                    tp_price,
                                    stop_loss_price,
                                    stop_size,
                                )
                            else:
                                # Generate fresh IDs for the new bracket
                                new_parent_id = self.get_next_order_id()
                                new_tp_id = self.get_next_order_id()
                                new_sl_id = self.get_next_order_id()

                                logging.info(
                                    "orderStatusEvent: Regenerating manual Custom bracket with new IDs: "
                                    "entry=%s, tp=%s, sl=%s (old entry=%s)",
                                    new_parent_id,
                                    new_tp_id,
                                    new_sl_id,
                                    trade.order.orderId,
                                )

                                # Rebuild entry STP order
                                entry_order = Order(
                                    orderId=new_parent_id,
                                    orderType="STP",
                                    action=buy_sell_type,
                                    totalQuantity=qty,
                                    auxPrice=entry_price,
                                    tif=tif,
                                    transmit=False,
                                )

                                # Rebuild TP LMT order
                                tp_order = Order(
                                    orderId=new_tp_id,
                                    orderType="LMT",
                                    action="SELL" if buy_sell_type.upper() == "BUY" else "BUY",
                                    totalQuantity=qty,
                                    lmtPrice=tp_price,
                                    parentId=new_parent_id,
                                    transmit=False,
                                )

                                # Rebuild SL STP order
                                sl_order = Order(
                                    orderId=new_sl_id,
                                    orderType="STP",
                                    action="SELL" if buy_sell_type.upper() == "BUY" else "BUY",
                                    totalQuantity=qty,
                                    auxPrice=round(stop_loss_price, Config.roundVal),
                                    parentId=new_parent_id,
                                    transmit=True,  # last leg transmits entire bracket
                                )

                                # Place the new bracket immediately (no artificial delay)
                                entry_resp = self.placeTrade(contract=contract, order=entry_order, outsideRth=outside_rth_flag)
                                logging.info(
                                    "orderStatusEvent: Regenerated manual Custom entry placed - newOrderId=%s, status=%s",
                                    entry_resp.order.orderId,
                                    entry_resp.orderStatus.status,
                                )

                                tp_resp = self.placeTrade(contract=contract, order=tp_order, outsideRth=outside_rth_flag)
                                logging.info(
                                    "orderStatusEvent: Regenerated manual Custom TP placed - newOrderId=%s, status=%s, parentId=%s",
                                    tp_resp.order.orderId,
                                    tp_resp.orderStatus.status,
                                    tp_resp.order.parentId,
                                )

                                sl_resp = self.placeTrade(contract=contract, order=sl_order, outsideRth=outside_rth_flag)
                                logging.info(
                                    "orderStatusEvent: Regenerated manual Custom SL placed - newOrderId=%s, status=%s, parentId=%s, transmit=%s",
                                    sl_resp.order.orderId,
                                    sl_resp.orderStatus.status,
                                    sl_resp.order.parentId,
                                    sl_resp.order.transmit,
                                )

                                # Update orderStatusData to reference the new entry ID
                                new_state = dict(data)
                                new_state['orderId'] = new_parent_id
                                new_state['duplicateIdRetryDone'] = True
                                Config.orderStatusData[new_parent_id] = new_state

                    except Exception as regen_err:
                        logging.error(
                            "orderStatusEvent: Error while regenerating manual Custom order after Duplicate order id "
                            "for %s: %s",
                            trade.order.orderId,
                            regen_err,
                        )
            
            # Only call sendTpAndSl when entry order is Filled (not for PendingCancel, Submitted, etc.)
            # This prevents duplicate TP/SL orders when entry order is being updated (cancelled/replaced)
            if should_send_tp_sl and trade.orderStatus.status == 'Filled' and ord_type == 'Entry':
                logging.info("orderStatusEvent: Calling sendTpAndSl for orderId=%s, barType=%s, ordType=%s, status=%s",
                            trade.order.orderId, bar_type, ord_type, trade.orderStatus.status)
                sendTpAndSl(self, data)
            elif should_send_tp_sl:
                logging.info("orderStatusEvent: NOT calling sendTpAndSl for orderId=%s, barType=%s, ordType=%s, status=%s (status != 'Filled' or ordType != 'Entry')",
                            trade.order.orderId, bar_type, ord_type, trade.orderStatus.status)
            else:
                logging.info("orderStatusEvent: NOT calling sendTpAndSl for orderId=%s, barType=%s, ordType=%s (should_send_tp_sl=False)",
                            trade.order.orderId, bar_type, ord_type)

    # tws connection stablish
    def connect(self):
        try:
            self.ib.connect(host=Config.host, port=Config.port, clientId=Config.clientId)
            # self.ib.waitOnUpdate()
            self.ib.orderStatusEvent += self.orderStatusEvent
            self.pnlEvent = self.pnlData
            # self.ib.pendingTickersEvent += self.onPendingTickers
            # self.reqPnl()
            self._initialize_order_ids()
        except Exception as e:
            logging.error("Error in ib connection " + str(e))
            return False

    def _initialize_order_ids(self):
        """
        Ensure the IB client is fully ready so that we can use
        ``self.ib.client.getReqId()`` as the **only** source of new
        order IDs.

        ib_insync already syncs its internal request-ID sequence with
        TWS's ``nextValidId`` during the connection handshake (see
        Client._onSocketHasData: msgId == 9). By using
        ``client.getReqId()`` for every new order we automatically
        stay above any ID IB has ever seen for this client session.

        IMPORTANT:
        - We no longer seed our own counter from ``time.time()``.
        - We no longer guess at an ``orderIdSeq`` attribute (it does
          not exist on ib_insync's Client).

        This removes the root cause of Error 103 ("Duplicate order id")
        that came from using time-based seeds like 1769681xxx.
        """
        try:
            start = time.time()
            # Wait until the client reports that the API is ready.
            while not self.ib.client.isReady():
                if time.time() - start > 10:
                    logging.warning(
                        "IB client not fully ready for order IDs after 10s; "
                        "will still rely on client.getReqId() when available."
                    )
                    break
                self.ib.waitOnUpdate(timeout=1)

            if self.ib.client.isReady():
                logging.info(
                    "Order ID initialization: IB client is ready; "
                    "get_next_order_id() will use ib.client.getReqId() "
                    "for all new order IDs."
                )
            else:
                logging.warning(
                    "Order ID initialization: IB client not ready yet; "
                    "get_next_order_id() will fall back to a local counter "
                    "until the client becomes ready."
                )
        except Exception as err:
            logging.error("Unable to run order ID initialization: %s", err)

    def _sync_order_id_with_existing_trades(self):
        """
        Ensure _order_id_counter is strictly greater than any orderId that
        already exists for this client in the current IB session.

        This is a defensive guard against "Duplicate order id" (Error 103)
        that can happen if:
        - The app was restarted and used a time-based seed that overlaps with
          previously used IDs in the same TWS session, or
        - nextValidId / orderIdSeq was not yet available and we seeded too low.
        """
        try:
            existing_trades = self.ib.trades()

            # ib.trades() can be a list or a dict-like; handle both safely.
            if isinstance(existing_trades, dict):
                trade_iter = existing_trades.values()
            else:
                trade_iter = existing_trades

            max_used_id = None
            for trade in trade_iter:
                try:
                    oid = getattr(trade.order, "orderId", None)
                    if oid is None:
                        continue
                    if max_used_id is None or oid > max_used_id:
                        max_used_id = oid
                except Exception:
                    # Be defensive; a single bad Trade object should not break syncing
                    continue

            if max_used_id is not None:
                with self._order_id_lock:
                    if self._order_id_counter is None or self._order_id_counter <= max_used_id:
                        new_counter = int(max_used_id) + 1
                        logging.info(
                            "Syncing order ID counter to %s based on existing trades (max_used_id=%s)",
                            new_counter,
                            max_used_id,
                        )
                        self._order_id_counter = new_counter
        except Exception as e:
            # This is a best-effort safeguard; log and continue if it fails.
            logging.warning("Failed to sync order ID counter with existing trades: %s", e)

    def get_next_order_id(self):
        """
        Get a new unique order ID.

        Primary path:
        - Use ``self.ib.client.getReqId()``, which ib_insync seeds from
          IB's ``nextValidId``. This guarantees we always move forward
          and never reuse an ID from the current TWS/Gateway session.

        Fallback:
        - If the client is not ready yet (very early startup) or if
          something goes wrong, we fall back to an internal counter.
          That counter is only a last resort; under normal operation
          all live trades will use the ib_insync-generated IDs.
        """
        with self._order_id_lock:
            try:
                if self.ib.client.isReady():
                    oid = self.ib.client.getReqId()
                    logging.debug("get_next_order_id: using ib.client.getReqId() -> %s", oid)
                    return oid
            except Exception as e:
                logging.warning("get_next_order_id: ib.client.getReqId() failed, falling back to local counter: %s", e)

            # Fallback local counter (should rarely be used)
            if self._order_id_counter is None:
                # Start from a high range to reduce the chance of clashing
                # with any historical IDs in this TWS session.
                base = int(time.time())
                if base < 2_000_000_000:
                    base = 2_000_000_000
                self._order_id_counter = base
                logging.warning(
                    "get_next_order_id: initializing local fallback counter to %s "
                    "(ib.client not ready)",
                    self._order_id_counter,
                )

            oid = self._order_id_counter
            self._order_id_counter += 1
            logging.debug("get_next_order_id: using local fallback counter -> %s", oid)
            return oid
    def reqPnl(self, retry_count=0, max_retries=10):
        """
        Request PnL tracking. Will retry if connection/account not ready yet.
        """
        try:
            # Check if connected first
            if not self.ib.isConnected():
                if retry_count < max_retries:
                    logging.info(f"reqPnl: Not connected yet, retrying in 1 second... (attempt {retry_count + 1}/{max_retries})")
                    import threading
                    threading.Timer(1.0, lambda: self.reqPnl(retry_count + 1, max_retries)).start()
                    return
                else:
                    logging.warning("reqPnl: Connection not established after max retries. PnL tracking disabled.")
                    print("Warning: Could not connect to TWS. PnL tracking disabled.")
                    return
            
            print("req pnl initializing...")
            accountValues = self.getAccountValue()
            
            if accountValues and len(accountValues) > 0:
                account = accountValues[0].account
                self.ib.reqPnL(account=account)
                asyncio.ensure_future(self.pnlData())
                print(f"PnL request successful for account: {account}")
                logging.info(f"PnL tracking enabled for account: {account}")
            else:
                # Retry if account values not available yet
                if retry_count < max_retries:
                    logging.info(f"reqPnl: Account values not available yet, retrying in 1 second... (attempt {retry_count + 1}/{max_retries})")
                    import threading
                    threading.Timer(1.0, lambda: self.reqPnl(retry_count + 1, max_retries)).start()
                    return
                else:
                    logging.warning("Could not get account values after max retries. PnL tracking disabled.")
                    print("Warning: No account info available. PnL tracking disabled.")
                    print("This is normal if TWS is not connected yet or account info is not available.")
        except Exception as e:
            # Retry on exception if we haven't exceeded max retries
            if retry_count < max_retries:
                logging.warning(f"reqPnl: Error on attempt {retry_count + 1}: {e}, retrying in 1 second...")
                import threading
                threading.Timer(1.0, lambda: self.reqPnl(retry_count + 1, max_retries)).start()
                return
            else:
                logging.error(f"Error requesting PnL after {max_retries} attempts: {e}")
                print(f"Warning: Could not initialize PnL tracking: {e}")

    async def pnlData(self):
        try:
            nest_asyncio.apply()
            await asyncio.sleep(1)
            accountValues = self.getAccountValue()
            if not accountValues or len(accountValues) == 0:
                logging.warning("No account values available for PnL tracking")
                return
            
            account = accountValues[0].account
            while True:
                try:
                    acc = self.ib.pnl(account=account)
                    if len(acc) > 0:
                        pnl = acc[0].dailyPnL
                        print(pnl)
                        if not math.isnan(pnl):
                            Config.currentPnl = pnl
                    else:
                        # print(acc)
                        pass
                except Exception as e:
                    logging.error(f"Error getting PnL data: {e}")
                await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"Error in pnlData loop: {e}")
            print(f"PnL tracking stopped: {e}")

    # when application will start if tws not connected then tkinter will check ib status regularly
    def ibStatusCheck(self):
        if self.ib.isConnected():
            return True
        else:
            return False

    # place trade on tws
    def placeTrade(self, contract, order, outsideRth=False, **kwargs):
        # nest_asyncio.apply()
        session = self._get_current_session()
        logging.info("placeTrade: session=%s, outsideRth=%s, orderType=%s", session, outsideRth, order.orderType)
        
        if outsideRth == False or outsideRth == 'False':
            order.outsideRth = False
        else:
            # Outside regular hours: decide behavior by session
            order.outsideRth = True
            if session == 'OVERNIGHT':
                # Overnight: ALL orders must be converted to LMT - no exceptions
                originalOrderType = order.orderType
                logging.info(f"Overnight session: Converting order type {originalOrderType} to LMT")
                
                try:
                    # If already LMT, check if lmtPrice exists, otherwise get price
                    if order.orderType == 'LMT':
                        if not hasattr(order, 'lmtPrice') or order.lmtPrice is None or order.lmtPrice == 0:
                            # LMT order without price - need to get price
                            logging.info("Overnight session: LMT order without price, getting price from market data")
                            limitPrice = self._get_price_for_overnight_order(contract, order.action)
                            order.lmtPrice = limitPrice
                            logging.info(f"Overnight session: Set LMT price to {limitPrice}")
                        else:
                            logging.info(f"Overnight session: LMT order already has price {order.lmtPrice}")
                    
                    # Convert MKT to LMT
                    elif order.orderType == 'MKT':
                        limitPrice = self._get_price_for_overnight_order(contract, order.action)
                        order.orderType = 'LMT'
                        order.lmtPrice = limitPrice
                        logging.info(f"Overnight session: Converting MKT to LMT at {limitPrice}")
                    
                    # Convert STP/STP LMT to LMT
                    elif order.orderType == 'STP' or order.orderType == 'STP LMT':
                        # Use auxPrice if available, otherwise get market price
                        if hasattr(order, 'auxPrice') and order.auxPrice:
                            limitPrice = order.auxPrice
                            logging.info(f"Overnight session: Using auxPrice {limitPrice} for STP conversion")
                        else:
                            logging.info("Overnight session: No auxPrice, getting market price for STP conversion")
                            limitPrice = self._get_price_for_overnight_order(contract, order.action)
                        order.orderType = 'LMT'
                        order.lmtPrice = limitPrice
                        # Clear auxPrice since we're converting to LMT
                        if hasattr(order, 'auxPrice'):
                            order.auxPrice = 0
                        logging.info(f"Overnight session: Converting {originalOrderType} to LMT at {limitPrice}")
                    
                    # Any other order type - convert to LMT
                    else:
                        # For any other order type, try to get price from existing fields or market data
                        limitPrice = None
                        if hasattr(order, 'lmtPrice') and order.lmtPrice:
                            limitPrice = order.lmtPrice
                            logging.info(f"Overnight session: Using existing lmtPrice {limitPrice}")
                        elif hasattr(order, 'auxPrice') and order.auxPrice:
                            limitPrice = order.auxPrice
                            logging.info(f"Overnight session: Using auxPrice {limitPrice}")
                        else:
                            logging.info("Overnight session: Getting market price for order conversion")
                            limitPrice = self._get_price_for_overnight_order(contract, order.action)
                        
                        order.orderType = 'LMT'
                        order.lmtPrice = limitPrice
                        # Clear auxPrice if it exists
                        if hasattr(order, 'auxPrice'):
                            order.auxPrice = 0
                        logging.info(f"Overnight session: Converting {originalOrderType} to LMT at {limitPrice} (all orders must be LMT during overnight)")
                except Exception as e:
                    logging.error(f"Overnight session: Error converting order {originalOrderType} to LMT: {e}")
                    logging.error(f"Overnight session: Order details - action={order.action}, auxPrice={getattr(order, 'auxPrice', 'N/A')}, lmtPrice={getattr(order, 'lmtPrice', 'N/A')}")
                    # Re-raise the exception so the caller knows the order failed
                    raise
            else:
                # Pre-market / After-hours: allow same types as RTH, no conversion
                logging.info("%s session: passing order type %s without conversion", session, order.orderType)

        # Check if a Trade already exists for this order ID and handle it
        # This prevents Error 105 (Order being modified does not match original order) and AssertionError
        try:
            existing_trades = self.ib.trades()
            # ib.trades() returns a dict-like object keyed by order ID
            if order.orderId and hasattr(existing_trades, '__contains__') and order.orderId in existing_trades:
                existing_trade = existing_trades[order.orderId]
                existing_status = existing_trade.orderStatus.status if hasattr(existing_trade, 'orderStatus') else 'Unknown'
                old_order_id = order.orderId
                
                # If order exists (regardless of status), we need a new order ID to avoid Error 105
                # IBKR will try to modify the existing order if we use the same ID, which causes Error 105
                logging.warning("Order ID %s already exists with status '%s'. Getting new order ID to avoid Error 105.", 
                              old_order_id, existing_status)
                new_order_id = self.get_next_order_id()
                order.orderId = new_order_id
                logging.info("Assigned new order ID: %s (was %s)", new_order_id, old_order_id)
                
                # If the existing order is in a done state, try to remove it from the trades collection
                if existing_status in ['Filled', 'Cancelled', 'Inactive']:
                    try:
                        if hasattr(existing_trades, '__delitem__'):
                            del existing_trades[old_order_id]
                            logging.info("Removed existing done Trade for old orderId %s", old_order_id)
                    except Exception as e:
                        logging.warning("Could not remove existing Trade for orderId %s: %s", old_order_id, e)
        except Exception as e:
            logging.debug("Error checking existing trades: %s (this is usually fine)", e)
        
        try:
            response = self.ib.placeOrder(contract=contract, order=order)
            return response
        except AssertionError as e:
            # This happens when ib_insync detects the order is already in a done state
            # Usually means the order ID was reused or there's a cached Trade object
            # Retry with a new order ID
            logging.warning(f"AssertionError placing order {order.orderId}: Order may already be in a done state. Retrying with new order ID...")
            logging.warning("Order details: orderId=%s, orderType=%s, action=%s, status=%s", 
                         order.orderId, order.orderType, order.action, 
                         getattr(self.ib.trades().get(order.orderId, None), 'orderStatus.status', 'N/A') if order.orderId in self.ib.trades() else 'N/A')
            
            # Try once more with a new order ID
            try:
                new_order_id = self.get_next_order_id()
                order.orderId = new_order_id
                logging.info(f"Retrying order placement with new order ID: {new_order_id}")
                response = self.ib.placeOrder(contract=contract, order=order)
                return response
            except Exception as retry_error:
                error_msg = f"AssertionError placing order {order.orderId}: Order may already be in a done state. " \
                           f"Retry with new order ID {new_order_id} also failed: {retry_error}"
                logging.error(error_msg)
                raise Exception(error_msg) from retry_error

    def _get_price_for_overnight_order(self, contract, action):
        """Get price for overnight order - tries multiple methods"""
        # Try to get live price from tick data
        try:
            self.subscribeTicker(contract)
            priceObj = self.getTickByTick(contract)
            if priceObj != None:
                lastPrice = priceObj.marketPrice()
                self.cancelTickData(contract)
                # Add 2% buffer for BUY, subtract 2% for SELL
                if action == 'BUY':
                    lastPrice = lastPrice + ((lastPrice / 100) * 2)
                else:
                    lastPrice = lastPrice - ((lastPrice / 100) * 2)
                logging.info("Overnight: Got price from tick data: %s", lastPrice)
                return round(lastPrice, 2)
        except Exception as e:
            logging.warning("Overnight: Could not get tick data: %s", e)
        
        # Fallback 1: use 1-min historical data
        try:
            logging.info("Overnight: Trying 1-min historical data for price")
            histData = self.getChartData(contract, '1 min', datetime.datetime.now())
            if len(histData) > 0:
                lastPrice = histData[-1].close
                # Add 2% buffer for BUY, subtract 2% for SELL
                if action == 'BUY':
                    lastPrice = lastPrice + ((lastPrice / 100) * 2)
                else:
                    lastPrice = lastPrice - ((lastPrice / 100) * 2)
                logging.info("Overnight: Got price from 1-min historical data: %s", lastPrice)
                return round(lastPrice, 2)
        except Exception as e:
            logging.warning("Overnight: Could not get 1-min historical data: %s", e)
        
        # Fallback 2: use daily candle data (last close price)
        try:
            logging.info("Overnight: Trying daily candle data for price")
            dailyData = self.getDailyCandle(contract)
            if len(dailyData) > 0:
                lastPrice = dailyData[-1].close
                # Add 2% buffer for BUY, subtract 2% for SELL
                if action == 'BUY':
                    lastPrice = lastPrice + ((lastPrice / 100) * 2)
                else:
                    lastPrice = lastPrice - ((lastPrice / 100) * 2)
                logging.info("Overnight: Got price from daily candle data: %s", lastPrice)
                return round(lastPrice, 2)
        except Exception as e:
            logging.warning("Overnight: Could not get daily candle data: %s", e)
        
        # If all else fails, we can't get price - this should not happen but log it
        error_msg = f"Cannot get price for overnight order - no tick data or historical data available for {contract}"
        logging.error(error_msg)
        raise Exception(error_msg)

    def _get_current_session(self):
        now = datetime.datetime.now().time().replace(microsecond=0)
        pre_start = datetime.time(4, 0, 0)
        rth_start = datetime.time(9, 30, 0)
        rth_end = datetime.time(16, 0, 0)
        after_end = datetime.time(20, 0, 0)
        if rth_start <= now < rth_end:
            return 'RTH'
        if pre_start <= now < rth_start:
            return 'PREMARKET'
        if rth_end <= now < after_end:
            return 'AFTERHOURS'
        return 'OVERNIGHT'

    def cancelTrade(self, order):
        logging.info("Going to Cancel Trade For " + str(order))
        response = self.ib.cancelOrder(order)
        return response


    def getFullDayData(self, ibcontract, timeFrame, configTime):
        nest_asyncio.apply()
        logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime, timeFrame, ibcontract)
        histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                             useRTH=False)
        # if (len(histData) < (Config.pullBackNo + 2
        if (len(histData) < (Config.pullBackNo)):
            logging.info("historical data not found for %s contract , time frame %s, time %s", ibcontract, timeFrame, configTime)
            return {}

        historical={}
        histData.reverse()
        x=0;
        for data in histData:
            if data.date.date() == datetime.datetime.now().date():
                if(configTime.time() <= data.date.time()):
                    # checking trading time......
                    if(data.date.time() >= datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime,"%Y-%m-%d %H:%M:%S").time() ):
                        historical.update({x:{"date":data.date,"close": data.close, "open": data.open, "high": data.high, "low": data.low}})
                        x = x +1

        return historical

    def BracketOrder(self,parentOrderId, action, quantity, limitPrice, takeProfitLimitPrice, stopLossPrice):
        parent = Order()
        parent.orderId = parentOrderId
        parent.action = action
        parent.orderType = "MKT"
        parent.totalQuantity = quantity
        parent.lmtPrice = limitPrice
        parent.transmit = True

        takeProfit = Order()
        takeProfit.orderId = parent.orderId + 1
        takeProfit.action = "SELL" if action.upper() == "BUY" else "BUY"
        takeProfit.orderType = "LMT"
        takeProfit.totalQuantity = quantity
        takeProfit.lmtPrice = takeProfitLimitPrice
        takeProfit.parentId = parentOrderId
        takeProfit.transmit = True

        stopLoss = Order()
        stopLoss.orderId = parent.orderId + 2
        stopLoss.action = "SELL" if action.upper() == "BUY" else "BUY"
        stopLoss.orderType = "STP"
        stopLoss.auxPrice = stopLossPrice
        stopLoss.totalQuantity = quantity
        stopLoss.parentId = parentOrderId
        stopLoss.transmit = True
        bracketOrder = [parent, takeProfit, stopLoss]
        return bracketOrder

    def getHistoricalChartDataForEntry(self, ibcontract, timeFrame, configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime, timeFrame, ibcontract)
            histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                                 useRTH=False)
            if (len(histData) < 2):
                logging.info("historical data not found for %s contract , time frame %s, time %s", ibcontract, timeFrame, configTime)
                return {}

            oldRow = None
            historical = {}
            i=0
            configTime = configTime.time().replace(microsecond=0)

            # for x in range(Config.pullBackNo + 1):
            #     no = (i - (x + 1))
            #     historical.update({(x + 1): {"date":histData[no].date,"close": histData[no].close, "open": histData[no].open, "high": histData[no].high, "low": histData[no].low}})
                # print(histData[no])
            x=0
            for d in histData:
                if (d.date.date() == datetime.datetime.now().date()) and (d.date.time() >= datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime, "%Y-%m-%d %H:%M:%S").time()):
                    historical.update({x: {"date": d.date, "close": d.close,
                                                 "open": d.open, "high": d.high,
                                                 "low": d.low}})
                    x = x+1

            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def getDailyCandle(self, ibcontract):
        try:
            nest_asyncio.apply()
            # Request enough days for ATR calculation: atrPeriod (20) + buffer for weekends/holidays
            # Request 40 days to ensure we have at least 21 trading days
            duration_days = max(40, Config.atrPeriod + 20)  # At least 40 days, or atrPeriod + 20
            logging.info("we are getting %s days candle data for %s contract (ATR period=%s)", duration_days, ibcontract, Config.atrPeriod)
            histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=f'{duration_days} D', barSizeSetting='1 day',
                                                 useRTH=False)

            return histData
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def getChartData(self,ibcontract,timeFrame,configTime):
        histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                             useRTH=False)
        return histData

    def get_recent_close_price_data(self, ibcontract, timeFrame, configTime):
        try:
            nest_asyncio.apply()
            logging.info("for close price we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime,
                         timeFrame, ibcontract)
            histData = self.getChartData(ibcontract, timeFrame, configTime)
            if (len(histData) == 0):
                logging.info("historical data not found for close price %s contract , time frame %s, time %s", ibcontract,
                             timeFrame, configTime)
                return {}

            oldRow = None
            historical = {}
            oldRow = histData[-1]
            historical = {"close": oldRow.close, "open": oldRow.open, "high": oldRow.high,
                          "low": oldRow.low, "dateTime": oldRow.date}

            logging.info("historical data found %s ", historical)
            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def lb1_entry_historical_data(self, ibcontract, timeFrame, configTime):
        try:
            nest_asyncio.apply()
            logging.info("entry_historical_data we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime, timeFrame, ibcontract)
            histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                                 useRTH=False)
            if (len(histData) < 2):
                logging.info("historical data not found for %s contract , time frame %s, time %s", ibcontract, timeFrame, configTime)
                return {}

            oldRow = None
            historical = {}
            i=0
            configTime = configTime.time().replace(microsecond=0)
            x=0
            for d in histData:
                if (d.date.date() == datetime.datetime.now().date()) and (d.date.time() >= configTime):
                    historical.update({x: {"date": d.date, "close": d.close,
                                                 "open": d.open, "high": d.high,
                                                 "low": d.low}})
                    x = x+1

            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def pbe1_entry_historical_data(self, ibcontract, timeFrame, configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime, timeFrame, ibcontract)
            histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                                 useRTH=False)
            if (len(histData) < 2):
                logging.info("historical data not found for %s contract , time frame %s, time %s", ibcontract, timeFrame, configTime)
                return {}

            oldRow = None
            historical = {}
            i=0
            configTime = configTime.time().replace(microsecond=0)
            x=0
            
            # Get current time to determine if we're in premarket
            current_time = datetime.datetime.now().time()
            trading_time = datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime, "%Y-%m-%d %H:%M:%S").time()
            is_premarket = current_time < trading_time
            
            # For PBe1: Include ALL bars from today (including premarket/postmarket)
            # This allows PBe1 to work in premarket, regular hours, and after-hours
            for d in histData:
                if d.date.date() == datetime.datetime.now().date():
                    # Include all bars from today (no time filter)
                    historical.update({x: {"date": d.date, "close": d.close,
                                                 "open": d.open, "high": d.high,
                                                 "low": d.low}})
                    x = x + 1

            logging.info("pbe1_entry_historical_data: Found %s bars for today (premarket=%s, current_time=%s, trading_time=%s)", 
                        len(historical), is_premarket, current_time, trading_time)
            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))
            return {}

    def fb_entry_historical_data(self, ibcontract, timeFrame, configTime):
        """
        FB: Same logic as lb1_entry_historical_data - just different configTime.
        LB: configTime=09:29 -> index 0 = LAST BAR of pre-market.
        FB: configTime=09:30 -> index 0 = FIRST BAR of regular hours.
        """
        return self.lb1_entry_historical_data(ibcontract, timeFrame, configTime)

    def rbb_entry_historical_data(self,ibcontract,timeFrame,configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ",configTime,timeFrame,ibcontract)
            histData = self.getChartData(ibcontract,timeFrame,configTime)
            if(len(histData) == 0):
                logging.info("historical data not found for %s contract , time frame %s, time %s",ibcontract,timeFrame,configTime)
                return {}

            oldRow=None
            historical ={}
            configTimeAsTime = configTime.time().replace(microsecond=0).replace(second=0)
            today = datetime.datetime.now().date()
            lastBarFromToday = None  # fallback: most recent completed bar from today
            for data in histData:
                chart_date = data.date.date()
                if chart_date == today:
                    lastBarFromToday = data
                if (today == chart_date) and (data.date.time().replace(microsecond=0).replace(second=0) == configTimeAsTime):
                    logging.info("we are adding this row in historical %s   {For %s contract }",data,ibcontract)
                    historical = {"close": data.close, "open": data.open, "high": data.high, "low": data.low,"dateTime":data.date}
                    break
                oldRow = data
            # When no bar exactly matches configTime (e.g. current bar not yet complete), use most recent completed bar from today
            if not historical and lastBarFromToday is not None:
                historical = {"close": lastBarFromToday.close, "open": lastBarFromToday.open, "high": lastBarFromToday.high, "low": lastBarFromToday.low,"dateTime":lastBarFromToday.date}
                logging.info("RBB: no exact bar for %s; using last completed bar from today %s for %s", configTimeAsTime, lastBarFromToday.date, ibcontract)
            logging.info("historical data found %s ",historical)
            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))


    def getHistoricalChartData(self,ibcontract,timeFrame,configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ",configTime,timeFrame,ibcontract)
            histData = self.getChartData(ibcontract,timeFrame,configTime)
            if(len(histData) == 0):
                logging.info("historical data not found for %s contract , time frame %s, time %s",ibcontract,timeFrame,configTime)
                return {}

            oldRow=None
            historical ={}

            # If configTime is None, return the latest bar data without time filtering
            if configTime is None:
                if len(histData) > 0:
                    latest_bar = histData[-1]
                    historical = {"close": latest_bar.close, "open": latest_bar.open, "high": latest_bar.high, "low": latest_bar.low, "dateTime": latest_bar.date}
                    logging.info("historical data found (latest bar, no time filter) %s ", historical)
                    return historical
                else:
                    logging.info("No historical data available")
                    return {}

            configTime = configTime.time().replace(microsecond=0)
            for data in histData:
                chart_date = data.date.date()

                #   todo  need to remove
                # chart_date =datetime.datetime.now().date()
                # configTime = datetime.datetime.strptime("2023-04-01 17:15:00","%Y-%m-%d %H:%M:%S").time()

                if (datetime.datetime.now().date() == chart_date) and (data.date.time() >= configTime):
                    if(oldRow != None and (oldRow.date.time() == configTime)):
                        logging.info("we are adding this row in historical %s   {For %s contract }",oldRow,ibcontract)
                        if (data.date.date() == datetime.datetime.now().date()) and (data.date.time() >= datetime.datetime.strptime( str(datetime.datetime.now().date()) + " " + Config.tradingTime,  "%Y-%m-%d %H:%M:%S").time()):
                            historical = {"close": oldRow.close, "open": oldRow.open, "high": oldRow.high, "low": oldRow.low,"dateTime":oldRow.date}
                            break
                oldRow = data
            logging.info("historical data found %s ",historical)
            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    #  with the help of this function we are unsubscribe ticker event. activate ticker event by getTickByTick function.
    def cancelTickData(self,currencyPair):
        try:
            nest_asyncio.apply()
            execution = self.ib.cancelMktData(contract=currencyPair)
            return execution
        except Exception as e:
            logging.error('cancel market data ' + str(e))

    def getAccountValue(self):
        try:
            val = self.ib.accountValues()
            if val and len(val) > 0:
                logging.info("Account value found: " + str(val[:3]))  # Log first 3 items
            else:
                logging.warning("No account values returned from IB")
            return val
        except Exception as e:
            logging.error(f"Error getting account values: {e}")
            return []


    # req market data gives data in ticker so firstly we need to define event function, see  onPendingTickers.
    def subscribeTicker(self,currencyPair):
        try:
            nest_asyncio.apply()
            self.ib.qualifyContracts(currencyPair)
            self.ib.reqMktData(currencyPair,'', False, False)
            self.ib.waitOnUpdate()
            self.ib.sleep(2)
        except Exception as e:
            logging.error('req market data ' + str(e))

    def cancelTickData(self,currencyPair):
        try:
            nest_asyncio.apply()
            execution = self.ib.cancelMktData(contract=currencyPair)

        except Exception as e:
            logging.error('cancel market data ' + str(e))

    def getTickByTick(self,currencyPair):
        try:
            tickers = self.ib.ticker(currencyPair)
            logging.info("Ticker Found " + str(tickers))
            return tickers
        except Exception as e:
            logging.error('req market data ' + str(e))

    def getAllOpenOrder(self):
        try:
            trades = self.ib.openTrades()
            logging.info('open trades --------------- %s ',trades)
            return trades
        except Exception as e:
            logging.error('get all open Trade ' + str(e))

    def getAllOpenPosition(self):
        try:
            trades = self.ib.positions()
            logging.info('open position --------------- %s ',trades)
            return trades
        except Exception as e:
            logging.error('get all open Trade ' + str(e))

    #  for tws disconnect
    def connection_close(self):
        if (self.ib.isConnected()):
            self.ib.disconnect()
            logging.info('TWS disconnect')

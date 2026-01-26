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
        # SIMPLIFIED ORDER ID SYSTEM: Use a single sequential counter that's always ahead of IB's nextValidId
        # This eliminates duplicate order ID issues by ensuring we're always using IDs that IB hasn't seen yet
        self._order_id_counter = None  # Main counter - always ahead of IB's nextValidId
        self._ib_next_valid_id = None   # Track IB's actual nextValidId
        self._last_sync_time = 0        # Track when we last synced with IB
        self._sync_interval = 10       # Sync with IB every 10 seconds
        self._min_gap = 10000           # Always stay at least 10000 IDs ahead of IB (increased for safety)

    # it will set trade order status value in global variable.
    def orderStatusEvent(self,trade: Trade):
        # Handle Error 103 (Duplicate order id) - retry with new order ID
        # IMPORTANT: Only retry ENTRY orders, not bracket orders (TP/SL)
        # Bracket orders will be regenerated when entry order is retried
        if trade.orderStatus.status == 'Cancelled':
            error_msg = getattr(trade.orderStatus, 'whyHeld', '') or ''
            log_entries = getattr(trade, 'log', [])
            duplicate_detected = False
            for log_entry in log_entries:
                if hasattr(log_entry, 'message') and ('Duplicate order id' in log_entry.message or '103' in log_entry.message):
                    duplicate_detected = True
                    logging.warning(f"Error 103 (Duplicate order id) detected for orderId={trade.order.orderId}. Attempting automatic retry...")
                    break
            
            # Only retry ENTRY orders (not bracket orders like TP/SL)
            # Bracket orders that fail due to missing parent should not be retried individually
            if duplicate_detected:
                try:
                    # Get order data from Config.orderStatusData
                    order_data = Config.orderStatusData.get(trade.order.orderId)
                    if order_data:
                        ord_type = order_data.get('ordType', '')
                        # Only retry Entry orders - bracket orders will be regenerated with new parent
                        if ord_type == 'Entry':
                            # Get trade type for proper order ID generation
                            bar_type = order_data.get('barType', '')
                            trade_type = bar_type if bar_type else None
                            
                            old_order_id = trade.order.orderId
                            
                            # Generate new order ID (ensure it's truly unique - check both trades and openOrders)
                            def is_order_id_active_check(order_id):
                                existing_trades_check = self.ib.trades()
                                # Check in trades
                                if isinstance(existing_trades_check, (list, tuple)):
                                    for t in existing_trades_check:
                                        if hasattr(t, 'order') and hasattr(t.order, 'orderId') and t.order.orderId == order_id:
                                            if hasattr(t, 'orderStatus') and hasattr(t.orderStatus, 'status'):
                                                status = t.orderStatus.status
                                                return status not in ['Filled', 'Cancelled', 'Inactive']
                                            return True
                                else:
                                    if order_id in existing_trades_check:
                                        trade_check = existing_trades_check[order_id]
                                        if hasattr(trade_check, 'orderStatus') and hasattr(trade_check.orderStatus, 'status'):
                                            status = trade_check.orderStatus.status
                                            return status not in ['Filled', 'Cancelled', 'Inactive']
                                        return True
                                
                                # Also check openOrders
                                try:
                                    open_orders = self.ib.openOrders()
                                    if open_orders:
                                        for open_order in open_orders:
                                            if hasattr(open_order, 'order') and hasattr(open_order.order, 'orderId') and open_order.order.orderId == order_id:
                                                return True
                                except Exception:
                                    pass
                                
                                return False
                            
                            # When retrying after Error 103, we need to jump ahead significantly
                            # because IB may have already used many sequential IDs
                            # First, try to get IB's actual nextValidOrderId
                            ib_next_id = None
                            try:
                                # Request next valid ID from IB
                                self.ib.client.reqIds(1)
                                self.ib.waitOnUpdate(timeout=0.5)
                                if hasattr(self.ib.client, "orderIdSeq") and self.ib.client.orderIdSeq:
                                    ib_next_id = self.ib.client.orderIdSeq
                                elif hasattr(self.ib, 'nextValidOrderId') and self.ib.nextValidOrderId:
                                    ib_next_id = self.ib.nextValidOrderId
                            except Exception as e:
                                logging.debug(f"Could not get IB's nextValidOrderId: {e}")
                            
                            # If we got IB's next ID, use it with a gap, otherwise use our counter
                            if ib_next_id:
                                # Use IB's ID + a gap to ensure we're ahead
                                new_order_id = int(ib_next_id) + self._min_gap
                                logging.info(f"Retry: Using IB's nextValidOrderId ({ib_next_id}) + gap ({self._min_gap}) = {new_order_id}")
                            else:
                                # Fallback: jump ahead significantly from current counter
                                with self._order_id_lock:
                                    current_counter = self._order_id_counter if self._order_id_counter else 2000000000
                                new_order_id = current_counter + 1000  # Jump ahead by 1000 to avoid collisions
                                logging.info(f"Retry: Jumping ahead by 1000 from counter: {new_order_id}")
                            
                            max_retries = 50
                            retry_count = 0
                            while is_order_id_active_check(new_order_id) and retry_count < max_retries:
                                logging.warning(f"Retry: Order ID {new_order_id} already exists and is active, getting next ID... (trade_type={trade_type}, retry {retry_count + 1}/{max_retries})")
                                # Jump ahead by larger increments when retrying
                                new_order_id += 100  # Jump by 100 instead of 1
                                retry_count += 1
                                # Refresh trades list periodically
                                if retry_count % 5 == 0:
                                    existing_trades_check = self.ib.trades()
                                    
                            # Update the counter to be ahead of the new order ID
                            with self._order_id_lock:
                                self._order_id_counter = new_order_id + 1
                            
                            logging.info(f"Retrying entry order with new order ID: {new_order_id} (was {old_order_id}), trade_type={trade_type}")
                            
                            # Update orderStatusData with new order ID BEFORE placing order
                            # This ensures we can retry again if needed
                            Config.orderStatusData[new_order_id] = order_data.copy()
                            Config.orderStatusData[new_order_id]['orderId'] = new_order_id
                            Config.orderStatusData[new_order_id]['old_order_id'] = old_order_id
                            # Preserve TP/SL prices for bracket order regeneration
                            if 'tp_price' in order_data:
                                Config.orderStatusData[new_order_id]['tp_price'] = order_data['tp_price']
                            if 'stop_loss_price' in order_data:
                                Config.orderStatusData[new_order_id]['stop_loss_price'] = order_data['stop_loss_price']
                            
                            # Update any pending bracket orders to use new parent ID
                            # Search for TP/SL orders that reference the old parent ID
                            try:
                                open_orders = self.ib.openOrders()
                                if open_orders:
                                    for open_order in open_orders:
                                        if hasattr(open_order, 'order') and hasattr(open_order.order, 'parentId') and open_order.order.parentId == old_order_id:
                                            logging.info(f"Updating bracket order {open_order.order.orderId} parentId from {old_order_id} to {new_order_id}")
                                            open_order.order.parentId = new_order_id
                                            # Update the order in IB
                                            self.ib.placeOrder(open_order.contract, open_order.order)
                            except Exception as e:
                                logging.warning(f"Could not update bracket orders: {e}")
                            
                            # Retry placing the order
                            contract = order_data.get('contract')
                            if contract:
                                # Create a fresh order object to avoid modification conflicts
                                from ib_insync import Order
                                retry_order = Order()
                                # Copy key attributes
                                for attr in ['action', 'totalQuantity', 'orderType', 'lmtPrice', 'auxPrice', 
                                            'tif', 'ocaGroup', 'ocaType', 'outsideRth', 'transmit']:
                                    if hasattr(trade.order, attr):
                                        setattr(retry_order, attr, getattr(trade.order, attr))
                                
                                # Set the new order ID
                                retry_order.orderId = new_order_id
                                outside_rth = order_data.get('outsideRth', False)
                                
                                logging.info(f"Retrying order placement: orderId={new_order_id}, contract={contract}, orderType={retry_order.orderType}, action={retry_order.action}")
                                retry_response = self.placeTrade(contract, retry_order, outsideRth=outside_rth, trade_type=trade_type)
                                if retry_response:
                                    logging.info(f"Successfully retried entry order with new order ID: {new_order_id}")
                                    
                                    # For Custom and Limit Order types, regenerate bracket orders (TP/SL) with new parent ID
                                    if bar_type in ['Custom', 'Limit Order']:
                                        try:
                                            # Get TP/SL prices from orderStatusData
                                            tp_price = order_data.get('tp_price')
                                            stop_loss_price = order_data.get('stop_loss_price')
                                            
                                            if tp_price and stop_loss_price:
                                                logging.info(f"Regenerating bracket orders for {bar_type}: new_parent={new_order_id}, tp={tp_price}, sl={stop_loss_price}")
                                                
                                                from ib_insync import Order
                                                qty = retry_order.totalQuantity
                                                buy_sell_type = retry_order.action
                                                
                                                # Generate new order IDs for TP and SL
                                                tp_order_id = self.get_next_order_id('TakeProfit')
                                                sl_order_id = self.get_next_order_id('StopLoss')
                                                
                                                # Take Profit order
                                                tp_order = Order(
                                                    orderId=tp_order_id,
                                                    orderType="LMT",
                                                    action="SELL" if buy_sell_type == "BUY" else "BUY",
                                                    totalQuantity=qty,
                                                    lmtPrice=tp_price,
                                                    parentId=new_order_id,
                                                    transmit=False
                                                )
                                                
                                                # Stop Loss order
                                                sl_order = Order(
                                                    orderId=sl_order_id,
                                                    orderType="STP",
                                                    action="SELL" if buy_sell_type == "BUY" else "BUY",
                                                    totalQuantity=qty,
                                                    auxPrice=stop_loss_price,
                                                    parentId=new_order_id,
                                                    transmit=True  # Last order transmits entire bracket
                                                )
                                                
                                                # Place bracket orders
                                                tp_response = self.placeTrade(contract=contract, order=tp_order, outsideRth=outside_rth, trade_type='TakeProfit')
                                                if tp_response:
                                                    logging.info(f"Regenerated TP order: orderId={tp_order_id}, parentId={new_order_id}")
                                                
                                                sl_response = self.placeTrade(contract=contract, order=sl_order, outsideRth=outside_rth, trade_type='StopLoss')
                                                if sl_response:
                                                    logging.info(f"Regenerated SL order: orderId={sl_order_id}, parentId={new_order_id}, transmit=True")
                                                
                                                # Update orderStatusData with new bracket order IDs
                                                if new_order_id in Config.orderStatusData:
                                                    Config.orderStatusData[new_order_id]['tp_order_id'] = tp_order_id
                                                    Config.orderStatusData[new_order_id]['sl_order_id'] = sl_order_id
                                            else:
                                                logging.warning(f"Cannot regenerate bracket orders: TP/SL prices not found in orderStatusData for {bar_type}")
                                        except Exception as e:
                                            logging.error(f"Error regenerating bracket orders for {bar_type}: {e}")
                                            logging.error(traceback.format_exc())
                                else:
                                    logging.error(f"Failed to retry entry order with new order ID: {new_order_id}")
                            else:
                                logging.error(f"Cannot retry order: contract not found in orderStatusData for orderId={trade.order.orderId}")
                        else:
                            logging.warning(f"Error 103 detected for {ord_type} order {trade.order.orderId}, but not retrying (only Entry orders are retried). Parent order may need to be regenerated.")
                    else:
                        logging.warning(f"Cannot retry order: orderStatusData not found for orderId={trade.order.orderId}")
                except Exception as e:
                    logging.error(f"Error retrying order after duplicate order ID: {e}")
                    logging.error(traceback.format_exc())
        
        if  trade.orderStatus.status == 'Filled':
            Config.orderFilledPrice.update({ trade.order.orderId :  trade.orderStatus.avgFillPrice })

        if Config.orderStatusData.get(trade.order.orderId) != None:
            data = Config.orderStatusData.get(trade.order.orderId)
            data.update({'status': trade.orderStatus.status})
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
                # Note: Option orders don't have 'barType', so use .get() with empty string default
                bar_type = data.get('barType', '')
                is_conditional_order = bar_type == Config.entryTradeType[conditional_order_index] if len(Config.entryTradeType) > conditional_order_index else False
                is_fb = bar_type == Config.entryTradeType[fb_index]
                is_rb = bar_type == Config.entryTradeType[rb_index] if len(Config.entryTradeType) > rb_index else False
                is_rbb = bar_type == Config.entryTradeType[rbb_index] if len(Config.entryTradeType) > rbb_index else False
                is_pbe1 = bar_type == Config.entryTradeType[pbe1_index] if len(Config.entryTradeType) > pbe1_index else False
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
                    is_lb = bar_type == Config.entryTradeType[lb_index] if len(Config.entryTradeType) > lb_index else False
                    is_lb2 = bar_type == Config.entryTradeType[lb2_index] if len(Config.entryTradeType) > lb2_index else False
                    is_lb3 = bar_type == Config.entryTradeType[lb3_index] if len(Config.entryTradeType) > lb3_index else False
                    if is_lb or is_lb2 or is_lb3:
                        should_send_tp_sl = is_extended_hours  # LB/LB2/LB3 use bracket orders in RTH, separate orders in extended hours
                    else:
                        should_send_tp_sl = True  # Other trade types always need sendTpAndSl
            
            logging.info("orderStatusEvent: should_send_tp_sl=%s (barType != entryTradeType[3] (FB): %s, not (is_manual_order and not is_extended_hours): %s)",
                        should_send_tp_sl, 
                        bar_type != Config.entryTradeType[3] if bar_type else 'N/A (option order)',
                        not (is_manual_order and not is_extended_hours))
            
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

            # Option trading: Trigger option orders when stock orders are placed or fill
            # - Entry placed (PreSubmitted/Submitted): Place option orders using trigger price (for immediate execution)
            # - Entry fills: Place option orders using fill price (fallback if not already placed)
            # - Option entry fills: Place option stop loss and take profit orders (to avoid Error 201)
            # - Stop loss/Profit fills: Trigger corresponding option orders
            try:
                logging.info("orderStatusEvent: Checking option trading - orderId=%s, status=%s, ord_type=%s, barType=%s", 
                            trade.order.orderId, trade.orderStatus.status, ord_type, bar_type)
                
                if ord_type == 'Entry':
                    # For Entry orders: 
                    # When placed (PreSubmitted/Submitted): Place option entry order immediately
                    # When filled: Also try to place option entry order as fallback (in case it wasn't placed earlier)
                    # Option entry order triggers when stock price crosses entry price (not waiting for stock order to fill)
                    if trade.orderStatus.status in ('PreSubmitted', 'Submitted', 'Filled'):
                        # Check if option trading is enabled for this trade
                        if hasattr(Config, 'option_trade_params') and Config.option_trade_params:
                            symbol = data.get('usersymbol')
                            timeFrame = data.get('timeFrame')
                            barType = data.get('barType')
                            buySellType = data.get('userBuySell') or data.get('action')
                            
                            # Find matching option params
                            matching_params = None
                            matching_key = None
                            for trade_key, params in list(Config.option_trade_params.items()):
                                if len(trade_key) >= 5:
                                    key_symbol, key_tf, key_bar, key_side, ts = trade_key
                                    if (key_symbol == symbol and key_tf == timeFrame and 
                                        key_bar == barType and key_side == buySellType):
                                        matching_params = params
                                        matching_key = trade_key
                                        break
                            
                            if matching_params and matching_params.get('enabled'):
                                # Check if option entry order was already placed for this stock order
                                option_already_placed = False
                                if trade.order.orderId in Config.orderStatusData:
                                    entry_data = Config.orderStatusData.get(trade.order.orderId, {})
                                    option_entry_order_id = entry_data.get('option_entry_order_id')
                                    if option_entry_order_id:
                                        # Check if option entry order exists
                                        try:
                                            option_trades = self.ib.trades()
                                            if isinstance(option_trades, (list, tuple)):
                                                for t in option_trades:
                                                    if hasattr(t, 'order') and hasattr(t.order, 'orderId') and t.order.orderId == option_entry_order_id:
                                                        option_already_placed = True
                                                        break
                                            elif option_entry_order_id in option_trades:
                                                option_already_placed = True
                                        except Exception:
                                            pass
                                
                                if not option_already_placed:
                                    logging.info("orderStatusEvent: Entry order %s (status=%s) - placing option entry order for orderId=%s", 
                                                'placed' if trade.orderStatus.status in ('PreSubmitted', 'Submitted') else 'filled',
                                                trade.orderStatus.status, trade.order.orderId)
                                    from OptionTrading import placeOptionEntryOrderImmediately
                                    import asyncio
                                    # Get entry, stop loss, and profit prices from orderStatusData
                                    entry_data = Config.orderStatusData.get(trade.order.orderId, {})
                                    # For filled orders, use filled price; for placed orders, use trigger price
                                    if trade.orderStatus.status == 'Filled' and trade.orderStatus.avgFillPrice:
                                        entry_price = float(trade.orderStatus.avgFillPrice)
                                    else:
                                        entry_price = entry_data.get('lastPrice') or entry_data.get('auxPrice') or entry_data.get('entryPrice')
                                    stop_loss_price = entry_data.get('stop_loss_price') or entry_data.get('stopLossPrice')
                                    profit_price = entry_data.get('tp_price') or entry_data.get('profit_price')
                                    
                                    if entry_price and stop_loss_price and profit_price:
                                        # Remove from option_trade_params since we're placing it now
                                        if matching_key in Config.option_trade_params:
                                            del Config.option_trade_params[matching_key]
                                        asyncio.ensure_future(
                                            placeOptionEntryOrderImmediately(
                                                self, trade.order.orderId, symbol, entry_price, stop_loss_price, profit_price,
                                                matching_params, buySellType, entry_data
                                            )
                                        )
                                    else:
                                        logging.warning("orderStatusEvent: Cannot place option entry order - missing prices: entry=%s, sl=%s, tp=%s", 
                                                       entry_price, stop_loss_price, profit_price)
                                else:
                                    logging.debug("orderStatusEvent: Option entry order already placed for orderId=%s", trade.order.orderId)
                elif ord_type == 'OptionEntry' and trade.orderStatus.status == 'Filled':
                    # When option entry order fills, place stop loss and take profit orders immediately
                    # These orders trigger when stock price reaches stop loss or take profit (not waiting for stock orders to fill)
                    logging.info("orderStatusEvent: Option entry order FILLED - placing option stop loss and take profit orders for orderId=%s", 
                                trade.order.orderId)
                    from OptionTrading import handleOptionEntryFill
                    handleOptionEntryFill(self, trade.order.orderId)
                elif ord_type in ('OptionStopLoss', 'OptionProfit') and trade.orderStatus.status == 'Filled':
                    # When option TP or SL fills, cancel the other bracket order
                    logging.info("orderStatusEvent: Option %s order FILLED - calling handleOptionTpSlFill for orderId=%s", 
                                ord_type, trade.order.orderId)
                    from OptionTrading import handleOptionTpSlFill
                    handleOptionTpSlFill(self, trade.order.orderId, ord_type)
                # Note: We no longer trigger option orders when stock stop loss/take profit fill
                # Option stop loss and take profit orders are placed immediately when option entry fills
                # They trigger based on stock price movements, not stock order fills
            except Exception as e:
                logging.error("orderStatusEvent: Error in option order trigger for orderId=%s: %s", trade.order.orderId, e)
                logging.error(traceback.format_exc())

    # tws connection stablish
    def connect(self):
        try:
            self.ib.connect(host=Config.host, port=Config.port, clientId=Config.clientId)
            # Wait a bit for connection to fully establish and nextValidId to be received
            self.ib.waitOnUpdate(timeout=2)
            self.ib.orderStatusEvent += self.orderStatusEvent
            self.ib.errorEvent += self._handle_error_event
            self.pnlEvent = self.pnlData
            # self.ib.pendingTickersEvent += self.onPendingTickers
            # self.reqPnl()
            self._initialize_order_ids()
        except Exception as e:
            logging.error("Error in ib connection " + str(e))
            return False

    def _initialize_order_ids(self):
        """Initialize order ID counter by syncing with IB's nextValidId."""
        try:
            # Request next valid ID from IB
            if hasattr(self.ib.client, 'reqIds'):
                self.ib.client.reqIds(1)
                logging.info("Requested nextValidId from IB")
            else:
                logging.info("reqIds not available, waiting for nextValidId from connection")
            
            # Wait for nextValidId to be received (up to 15 seconds, checking more frequently)
            start = time.time()
            ib_next_id = None
            check_count = 0
            
            while ib_next_id is None and (time.time() - start) < 15:
                check_count += 1
                # Try multiple ways to get IB's next valid ID
                # Check orderIdSeq first (most reliable)
                if hasattr(self.ib.client, "orderIdSeq") and self.ib.client.orderIdSeq:
                    ib_next_id = self.ib.client.orderIdSeq
                    logging.info("Got nextValidId from orderIdSeq: %s (after %d checks)", ib_next_id, check_count)
                    break
                # Check nextValidOrderId attribute
                elif hasattr(self.ib, 'nextValidOrderId') and self.ib.nextValidOrderId:
                    ib_next_id = self.ib.nextValidOrderId
                    logging.info("Got nextValidId from nextValidOrderId: %s (after %d checks)", ib_next_id, check_count)
                    break
                # Check if there's a nextValidId event handler result
                elif hasattr(self.ib.client, '_nextValidOrderId') and self.ib.client._nextValidOrderId:
                    ib_next_id = self.ib.client._nextValidOrderId
                    logging.info("Got nextValidId from _nextValidOrderId: %s (after %d checks)", ib_next_id, check_count)
                    break
                else:
                    # Wait a bit and check again
                    self.ib.waitOnUpdate(timeout=0.2)
                    if check_count % 10 == 0:
                        logging.debug("Still waiting for nextValidId... (check %d)", check_count)
            
            if ib_next_id is None:
                # Fallback: Check ALL open orders to find the highest order ID
                # This is critical because IB's nextValidId might be much higher than any existing trade
                logging.warning("nextValidId not received from IB after %d checks, checking all open orders...", check_count)
                try:
                    # Get all open orders (not just trades)
                    all_orders = []
                    try:
                        # Try to get open orders
                        open_orders = self.ib.openOrders()
                        if open_orders:
                            all_orders.extend(open_orders)
                            logging.info("Found %d open orders", len(open_orders))
                    except Exception as e:
                        logging.debug("Could not get openOrders: %s", e)
                    
                    # Also check trades
                    existing_trades = self.ib.trades()
                    if existing_trades:
                        if isinstance(existing_trades, (list, tuple)):
                            all_orders.extend(existing_trades)
                        else:
                            # Dict-like: convert to list
                            all_orders.extend(existing_trades.values() if hasattr(existing_trades, 'values') else [])
                    
                    # Find the maximum order ID from all sources
                    max_id = 0
                    for order_or_trade in all_orders:
                        try:
                            # Handle both Order objects and Trade objects
                            if hasattr(order_or_trade, 'orderId'):
                                oid_int = int(order_or_trade.orderId)
                            elif hasattr(order_or_trade, 'order') and hasattr(order_or_trade.order, 'orderId'):
                                oid_int = int(order_or_trade.order.orderId)
                            else:
                                continue
                            
                            if oid_int > max_id:
                                max_id = oid_int
                        except (ValueError, TypeError, AttributeError):
                            continue
                    
                    if max_id > 0:
                        # Use max_id + 10000 to ensure we're well ahead (matching _min_gap)
                        # But also try one more time to get IB's actual nextValidOrderId
                        ib_next_id = max_id + self._min_gap
                        logging.info("Found highest existing order ID: %s, using next ID: %s (gap=%s)", max_id, ib_next_id, self._min_gap)
                        
                        # Try one more time to get IB's actual nextValidOrderId (it might be higher)
                        try:
                            self.ib.client.reqIds(1)
                            self.ib.waitOnUpdate(timeout=0.5)
                            if hasattr(self.ib.client, "orderIdSeq") and self.ib.client.orderIdSeq:
                                actual_ib_id = int(self.ib.client.orderIdSeq)
                                if actual_ib_id > ib_next_id:
                                    ib_next_id = actual_ib_id + self._min_gap
                                    logging.info("IB's actual nextValidOrderId (%s) is higher than max order ID, using %s", actual_ib_id, ib_next_id)
                        except Exception as e:
                            logging.debug("Could not get IB's nextValidOrderId in fallback: %s", e)
                    else:
                        # No valid order IDs found - this is unusual, but use a safe high number
                        # Use a number that's likely higher than IB's current counter
                        # IB's counters are typically in the billions, so use current time * 1000
                        ib_next_id = int(time.time()) * 1000
                        # But ensure it's not too high (stay under 2 billion)
                        if ib_next_id > 2000000000:
                            ib_next_id = 2000000000
                        logging.warning("No valid order IDs found, using calculated ID: %s", ib_next_id)
                except Exception as e:
                    logging.error("Error checking existing orders: %s", e)
                    # Last resort: use a high number
                    ib_next_id = int(time.time()) * 1000
                    if ib_next_id > 2000000000:
                        ib_next_id = 2000000000
                    logging.warning("Using fallback ID: %s", ib_next_id)
                
                # Double-check the fallback ID is not in use
                try:
                    existing_trades = self.ib.trades()
                    # Check if ID exists - handle both list and dict cases
                    id_exists = False
                    if isinstance(existing_trades, (list, tuple)):
                        # Check if any trade has this order ID
                        for trade in existing_trades:
                            if hasattr(trade, 'order') and hasattr(trade.order, 'orderId') and trade.order.orderId == ib_next_id:
                                id_exists = True
                                break
                    else:
                        # Dict-like: check if key exists
                        id_exists = ib_next_id in existing_trades if hasattr(existing_trades, '__contains__') else False
                    
                    while id_exists:
                        logging.warning("Fallback order ID %s already exists, incrementing...", ib_next_id)
                        ib_next_id += 1
                        # Re-check
                        if isinstance(existing_trades, (list, tuple)):
                            id_exists = any(hasattr(t, 'order') and hasattr(t.order, 'orderId') and t.order.orderId == ib_next_id for t in existing_trades)
                        else:
                            id_exists = ib_next_id in existing_trades if hasattr(existing_trades, '__contains__') else False
                except Exception as e:
                    logging.debug("Could not verify fallback ID uniqueness: %s", e)
            else:
                logging.info("Order ID initialized from IB: %s", ib_next_id)
            
            # Ensure we stay within 32-bit signed integer limit
            max_safe_id = 2147483647
            if ib_next_id > max_safe_id - self._min_gap:
                ib_next_id = max_safe_id - self._min_gap
                logging.warning(f"IB nextValidId too large, resetting to safe value: {ib_next_id}")
            
            with self._order_id_lock:
                # Set our counter to be ahead of IB's nextValidId
                # Always stay at least _min_gap IDs ahead to prevent conflicts
                self._ib_next_valid_id = int(ib_next_id)
                self._order_id_counter = self._ib_next_valid_id + self._min_gap
                self._last_sync_time = time.time()
                logging.info("Order ID system initialized: IB nextValidId=%s, our counter=%s (gap=%s)", 
                           self._ib_next_valid_id, self._order_id_counter, self._min_gap)
        except Exception as err:
            logging.error("Unable to initialize order ids: %s", err)
            # Fallback: use time-based ID with safety margin
            with self._order_id_lock:
                time_based_id = int(time.time())
                # Ensure it's within safe limits
                max_safe_id = 2147483647
                if time_based_id > max_safe_id - self._min_gap:
                    time_based_id = max_safe_id - self._min_gap
                self._ib_next_valid_id = time_based_id
                self._order_id_counter = time_based_id + self._min_gap
                self._last_sync_time = time.time()
                logging.warning("Using time-based order ID fallback: IB=%s, counter=%s", 
                              self._ib_next_valid_id, self._order_id_counter)

    def _sync_with_ib_order_id(self):
        """Sync our order ID counter with IB's actual nextValidId."""
        try:
            # Check if we need to sync (every _sync_interval seconds)
            current_time = time.time()
            if current_time - self._last_sync_time < self._sync_interval:
                return  # Don't sync too frequently
            
            # Try to get IB's current nextValidId
            ib_next_id = None
            if hasattr(self.ib.client, "orderIdSeq") and self.ib.client.orderIdSeq:
                ib_next_id = self.ib.client.orderIdSeq
            elif hasattr(self.ib, 'nextValidOrderId') and self.ib.nextValidOrderId:
                ib_next_id = self.ib.nextValidOrderId
            else:
                # Request it
                if hasattr(self.ib.client, 'reqIds'):
                    self.ib.client.reqIds(1)
                    self.ib.waitOnUpdate(timeout=0.5)
                    if hasattr(self.ib.client, "orderIdSeq") and self.ib.client.orderIdSeq:
                        ib_next_id = self.ib.client.orderIdSeq
            
            if ib_next_id:
                with self._order_id_lock:
                    new_ib_next_id = int(ib_next_id)
                    # Update IB's nextValidId
                    self._ib_next_valid_id = new_ib_next_id
                    
                    # Ensure our counter is always ahead of IB's
                    # If our counter is None or behind IB's, reset it
                    if self._order_id_counter is None or self._order_id_counter <= new_ib_next_id:
                        self._order_id_counter = new_ib_next_id + self._min_gap
                        logging.info(f"Synced order ID counter: IB={self._ib_next_valid_id}, our counter={self._order_id_counter} (gap={self._min_gap})")
                    # If IB's nextValidId has advanced significantly, we should also advance
                    elif new_ib_next_id > self._ib_next_valid_id + 100:
                        # IB has advanced significantly, make sure we're still ahead
                        if self._order_id_counter <= new_ib_next_id:
                            self._order_id_counter = new_ib_next_id + self._min_gap
                            logging.info(f"IB advanced significantly, updated counter: IB={self._ib_next_valid_id}, our counter={self._order_id_counter}")
                    
                    self._last_sync_time = current_time
        except Exception as e:
            logging.debug(f"Could not sync with IB order ID: {e} (this is usually fine)")
    
    def get_next_order_id(self, trade_type=None):
        """
        Get next unique order ID.
        
        SIMPLIFIED SYSTEM: Uses a single sequential counter that's always ahead of IB's nextValidId.
        This eliminates duplicate order ID issues by ensuring we never use IDs that IB has already seen.
        
        Args:
            trade_type: Trade type string (ignored in simplified system, kept for compatibility)
        
        Returns:
            int: Next unique order ID
        """
        # Periodically sync with IB to ensure we're ahead
        self._sync_with_ib_order_id()
        
        with self._order_id_lock:
            # Initialize counter if not set
            if self._order_id_counter is None:
                logging.warning("Order ID counter not initialized, initializing now...")
                self._initialize_order_ids()
                if self._order_id_counter is None:
                    # Last resort fallback
                    self._order_id_counter = int(time.time()) + self._min_gap
                    logging.error("Order ID counter still None after initialization, using fallback: %s", self._order_id_counter)
            
            # Get next ID from counter
            next_id = self._order_id_counter
            self._order_id_counter += 1
            
            # Check if we're getting too close to IB's nextValidId
            if self._ib_next_valid_id and next_id <= self._ib_next_valid_id:
                # We're behind IB! This should never happen, but handle it
                logging.error(f"WARNING: Order ID {next_id} is behind IB's nextValidId {self._ib_next_valid_id}. Resyncing...")
                self._sync_with_ib_order_id()
                if self._order_id_counter <= self._ib_next_valid_id:
                    self._order_id_counter = self._ib_next_valid_id + self._min_gap
                next_id = self._order_id_counter
                self._order_id_counter += 1
            
            # Check if we've exceeded the 32-bit signed integer limit
            if next_id > 2147483647:
                logging.error(f"Order ID {next_id} exceeds 32-bit limit! Resetting counter...")
                # Reset to a safe value (1 billion)
                self._order_id_counter = 1000000000
                next_id = self._order_id_counter
                self._order_id_counter += 1
            
            # Final safety check: verify ID is not in active use
            # This is a last resort check - with proper initialization and syncing, this should rarely trigger
            try:
                existing_trades = self.ib.trades()
                max_safety_retries = 20  # Increased retries
                safety_retry_count = 0
                
                # Helper function to check if order ID exists and get the trade
                def get_trade_by_id(trades, order_id):
                    if isinstance(trades, (list, tuple)):
                        for trade in trades:
                            if hasattr(trade, 'order') and hasattr(trade.order, 'orderId') and trade.order.orderId == order_id:
                                return trade
                        return None
                    else:
                        # Dict-like
                        return trades.get(order_id) if hasattr(trades, 'get') else (trades[order_id] if order_id in trades else None)
                
                # Also check openOrders for active orders
                def is_order_id_active(order_id):
                    # Check in trades
                    trade = get_trade_by_id(existing_trades, order_id)
                    if trade is not None:
                        if hasattr(trade, 'orderStatus') and hasattr(trade.orderStatus, 'status'):
                            status = trade.orderStatus.status
                            # Only consider active states as conflicts
                            return status not in ['Filled', 'Cancelled', 'Inactive']
                        # Can't check status, assume active
                        return True
                    
                    # Also check openOrders
                    try:
                        open_orders = self.ib.openOrders()
                        if open_orders:
                            for open_order in open_orders:
                                if hasattr(open_order, 'order') and hasattr(open_order.order, 'orderId') and open_order.order.orderId == order_id:
                                    return True
                    except Exception as e:
                        logging.debug(f"Could not check openOrders: {e}")
                    
                    return False
                
                while is_order_id_active(next_id) and safety_retry_count < max_safety_retries:
                    logging.warning(f"Order ID {next_id} is active, incrementing... (retry {safety_retry_count + 1}/{max_safety_retries})")
                    next_id = self._order_id_counter
                    self._order_id_counter += 1
                    safety_retry_count += 1
                    # Refresh trades list periodically
                    if safety_retry_count % 3 == 0:
                        existing_trades = self.ib.trades()
                
                if safety_retry_count > 0:
                    logging.info(f"Adjusted order ID to {next_id} after {safety_retry_count} safety retries")
            except Exception as e:
                logging.debug(f"Error checking existing trades in safety check: {e}")
            
            return next_id
    
    def _handle_error_event(self, reqId, errorCode, errorString, contract):
        """Handle error events from IB, especially Error 103 (Duplicate order id)"""
        if errorCode == 103 or 'Duplicate order id' in errorString:
            logging.error(f"Error 103 (Duplicate order id) detected: reqId={reqId}, errorString={errorString}")
            # Note: We can't automatically retry here, but this helps with logging
            # The actual retry should happen in placeTrade when the error is detected
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
    def placeTrade(self, contract, order, outsideRth=False, trade_type=None):
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
        # This prevents Error 103 (Duplicate order id) and Error 105 (Order being modified does not match original order)
        # CRITICAL: This check must happen BEFORE placing the order to prevent bracket order failures
        try:
            existing_trades = self.ib.trades()
            
            # Helper function to get trade by order ID (handles both list and dict)
            def get_trade_by_id(trades, order_id):
                if isinstance(trades, (list, tuple)):
                    for trade in trades:
                        if hasattr(trade, 'order') and hasattr(trade.order, 'orderId') and trade.order.orderId == order_id:
                            return trade
                    return None
                else:
                    # Dict-like
                    return trades.get(order_id) if hasattr(trades, 'get') else (trades[order_id] if order_id in trades else None)
            
            # Helper function to check if order ID exists
            def order_id_exists(trades, order_id):
                if isinstance(trades, (list, tuple)):
                    return any(hasattr(t, 'order') and hasattr(t.order, 'orderId') and t.order.orderId == order_id for t in trades)
                else:
                    return order_id in trades if hasattr(trades, '__contains__') else False
            
            # Check both trades and openOrders
            def is_order_id_active(order_id):
                # Check in trades
                if order_id_exists(existing_trades, order_id):
                    trade = get_trade_by_id(existing_trades, order_id)
                    if trade and hasattr(trade, 'orderStatus') and hasattr(trade.orderStatus, 'status'):
                        status = trade.orderStatus.status
                        # Only consider active states as conflicts
                        return status not in ['Filled', 'Cancelled', 'Inactive']
                    # Can't check status, assume active
                    return True
                
                # Also check openOrders
                try:
                    open_orders = self.ib.openOrders()
                    if open_orders:
                        for open_order in open_orders:
                            if hasattr(open_order, 'order') and hasattr(open_order.order, 'orderId') and open_order.order.orderId == order_id:
                                return True
                except Exception as e:
                    logging.debug(f"Could not check openOrders: {e}")
                
                return False
            
            if order.orderId and is_order_id_active(order.orderId):
                existing_trade = get_trade_by_id(existing_trades, order.orderId)
                existing_status = existing_trade.orderStatus.status if existing_trade and hasattr(existing_trade, 'orderStatus') else 'Unknown'
                old_order_id = order.orderId
                
                # If order exists (regardless of status), we MUST get a new order ID to avoid Error 103 and Error 105
                # IBKR will reject the order if we use the same ID (Error 103) or try to modify it (Error 105)
                # This is especially critical for entry orders because bracket orders depend on them
                logging.warning("Order ID %s already exists and is active (status='%s'). Getting new order ID to avoid Error 103/105 (trade_type=%s).", 
                              old_order_id, existing_status, trade_type)
                
                # Keep trying until we get a unique order ID
                # First, try to sync with IB's actual nextValidOrderId to avoid being behind
                ib_next_id = None
                try:
                    self.ib.client.reqIds(1)
                    self.ib.waitOnUpdate(timeout=0.5)
                    if hasattr(self.ib.client, "orderIdSeq") and self.ib.client.orderIdSeq:
                        ib_next_id = self.ib.client.orderIdSeq
                    elif hasattr(self.ib, 'nextValidOrderId') and self.ib.nextValidOrderId:
                        ib_next_id = self.ib.nextValidOrderId
                except Exception as e:
                    logging.debug(f"Could not get IB's nextValidOrderId in placeTrade: {e}")
                
                # If we got IB's next ID, use it with a gap, otherwise use our counter
                if ib_next_id:
                    new_order_id = int(ib_next_id) + self._min_gap
                    logging.info("placeTrade: Using IB's nextValidOrderId (%s) + gap (%s) = %s (was %s)", 
                              ib_next_id, self._min_gap, new_order_id, old_order_id)
                else:
                    new_order_id = self.get_next_order_id(trade_type)
                
                max_retries = 50  # Increased to ensure we find a unique ID
                retry_count = 0
                
                while is_order_id_active(new_order_id) and retry_count < max_retries:
                    logging.warning("New order ID %s also exists and is active, trying next ID... (trade_type=%s, retry %d/%d)", 
                                  new_order_id, trade_type, retry_count + 1, max_retries)
                    # Jump ahead by larger increments when retrying
                    new_order_id += 100  # Jump by 100 instead of just incrementing
                    retry_count += 1
                    # Refresh trades list every few retries
                    if retry_count % 5 == 0:
                        existing_trades = self.ib.trades()
                        # Also try to re-sync with IB
                        try:
                            self.ib.client.reqIds(1)
                            self.ib.waitOnUpdate(timeout=0.5)
                            if hasattr(self.ib.client, "orderIdSeq") and self.ib.client.orderIdSeq:
                                ib_next_id = self.ib.client.orderIdSeq
                                new_order_id = int(ib_next_id) + self._min_gap
                                logging.info("placeTrade: Re-synced with IB's nextValidOrderId (%s), using %s", ib_next_id, new_order_id)
                        except Exception:
                            pass
                
                if is_order_id_active(new_order_id):
                    logging.error("WARNING: Could not find unique inactive order ID after %d retries. Using %s anyway (may cause Error 103).", 
                                max_retries, new_order_id)
                else:
                    logging.info("Assigned new unique order ID: %s (was %s, trade_type=%s)", new_order_id, old_order_id, trade_type)
                
                order.orderId = new_order_id
        except Exception as e:
            logging.warning("Error checking existing trades: %s", e)
        
        try:
            response = self.ib.placeOrder(contract=contract, order=order)
            # Check if the response indicates a duplicate order ID error
            duplicate_detected = False
            if response and hasattr(response, 'orderStatus'):
                if response.orderStatus.status == 'Cancelled':
                    error_msg = getattr(response.orderStatus, 'whyHeld', '') or str(response.orderStatus)
                    # Check log entries for duplicate order ID error
                    if hasattr(response, 'log') and response.log:
                        for log_entry in response.log:
                            if hasattr(log_entry, 'message') and ('Duplicate order id' in log_entry.message or '103' in log_entry.message):
                                duplicate_detected = True
                                break
                    if not duplicate_detected and ('Duplicate order id' in error_msg or '103' in error_msg):
                        duplicate_detected = True
                    
                    if duplicate_detected:
                        # Duplicate order ID detected - get new ID and retry (using trade-type-specific range)
                        logging.warning(f"Duplicate order ID {order.orderId} detected in response. Getting new order ID and retrying... (trade_type={trade_type})")
                        new_order_id = self.get_next_order_id(trade_type)
                        order.orderId = new_order_id
                        logging.info(f"Retrying order placement with new order ID: {new_order_id}")
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
            
            # Try once more with a new order ID (using trade-type-specific range)
            try:
                new_order_id = self.get_next_order_id(trade_type)
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

    def fb_entry_historical_data(self,ibcontract,timeFrame,configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ",configTime,timeFrame,ibcontract)
            histData = self.getChartData(ibcontract,timeFrame,configTime)
            if(len(histData) == 0):
                logging.info("historical data not found for %s contract , time frame %s, time %s",ibcontract,timeFrame,configTime)
                return {}

            oldRow=None
            historical ={}
            configTime = configTime.time().replace(microsecond=0)
            for data in histData:
                chart_date = data.date.date()
                if (datetime.datetime.now().date() == chart_date) and (data.date.time() >= configTime):
                    # here we are checking if time 9:31 thenwe will get 9:30 data...
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

    def rbb_entry_historical_data(self,ibcontract,timeFrame,configTime):
        """
        Get the most recent bar data for RBB logic.
        Instead of searching for a specific time, just get the latest available bar.
        This is more efficient and reliable.
        """
        try:
            nest_asyncio.apply()
            logging.info("Getting most recent bar data for %s contract, timeFrame=%s (requested time=%s)", 
                        ibcontract, timeFrame, configTime)
            
            # Get chart data - pass None to get the most recent bars
            histData = self.getChartData(ibcontract, timeFrame, None)
            if(len(histData) == 0):
                logging.info("No chart data found for %s contract, time frame %s", ibcontract, timeFrame)
                return {}

            # Get the most recent bar (last item in the list)
            if histData and len(histData) > 0:
                latest_bar = histData[-1]
                historical = {
                    "close": latest_bar.close, 
                    "open": latest_bar.open, 
                    "high": latest_bar.high, 
                    "low": latest_bar.low,
                    "dateTime": latest_bar.date
                }
                logging.info("Got most recent bar data: %s (datetime=%s) for %s contract", 
                           historical, latest_bar.date, ibcontract)
                return historical
            else:
                logging.info("No bars found in chart data for %s contract", ibcontract)
                return {}
        except Exception as e:
            logging.error('Error getting RBB historical data: %s', str(e))
            logging.error(traceback.format_exc())
            return {}


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

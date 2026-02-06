from header import *

def StatusUpdate(response,ordType,contract,type,tradeType, quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,entryData,tif,barType,userBuySell,userAtr,slValue=0,breakEven=False,outsideRth=False,replayEnabled=False,entry_points='0'):
    try:
        # For Entry orders, try to get replay state from pending orders
        if ordType == 'Entry' and replayEnabled == False:
            # Try to find matching replay state from pending orders
            # Use the most recent matching key
            matching_key = None
            for key in sorted(Config.order_replay_pending.keys(), key=lambda x: x[4], reverse=True):  # Sort by timestamp
                if key[0] == symbol and key[1] == timeFrame and key[2] == barType and key[3] == userBuySell:
                    matching_key = key
                    break
            if matching_key and matching_key in Config.order_replay_pending:
                replayEnabled = Config.order_replay_pending.pop(matching_key)  # Remove from pending
                logging.info("Retrieved replay state for Entry order: orderId=%s, symbol=%s, replay=%s", 
                            response.order.orderId, symbol, replayEnabled)
        
        logging.info("updating order status response = %s,ordType = %s,contract = %s,type = %s,tradeType = %s, quantity = %s,histData = %s,lastPrice = %s, symbol = %s,timeFrame = %s,profit = %s,stopLoss = %s,risk  = %s",response,ordType,contract,type,tradeType, quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk)
        parent_id = getattr(response.order, 'parentId', 0) or 0
        Config.orderStatusData.update({int(response.order.orderId):
                                           {'slValue':slValue, 'ordType': ordType, 'orderId': int(response.order.orderId),
                                            'contract': contract, 'type': type, 'action': tradeType, 'totalQuantity': quantity,
                                            'status': response.orderStatus.status,"histData":histData, "usersymbol": symbol,
                                            "lastPrice": lastPrice,
                                            "timeFrame": timeFrame, "profit": profit, "stopLoss": stopLoss,"breakEven":breakEven,
                                            "risk": risk,"dateTime":datetime.datetime.now(),"entryData":entryData,"tif":tif,"barType":barType,"userBuySell":userBuySell,"userAtr":userAtr,"outsideRth":outsideRth,"replayEnabled":replayEnabled,"entry_points":entry_points,"parentId":parent_id}})
    except Exception as e:
        logging.error("Error in updating order status dict "+str(e))



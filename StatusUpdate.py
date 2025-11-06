from header import *

def StatusUpdate(response,ordType,contract,type,tradeType, quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,entryData,tif,barType,userBuySell,userAtr,slValue=0,breakEven=False,outsideRth=False):
    try:
        logging.info("updating order status response = %s,ordType = %s,contract = %s,type = %s,tradeType = %s, quantity = %s,histData = %s,lastPrice = %s, symbol = %s,timeFrame = %s,profit = %s,stopLoss = %s,risk  = %s",response,ordType,contract,type,tradeType, quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk)
        Config.orderStatusData.update({int(response.order.orderId):
                                           {'slValue':slValue, 'ordType': ordType, 'orderId': int(response.order.orderId),
                                            'contract': contract, 'type': type, 'action': tradeType, 'totalQuantity': quantity,
                                            'status': response.orderStatus.status,"histData":histData, "usersymbol": symbol,
                                            "lastPrice": lastPrice,
                                            "timeFrame": timeFrame, "profit": profit, "stopLoss": stopLoss,"breakEven":breakEven,
                                            "risk": risk,"dateTime":datetime.datetime.now(),"entryData":entryData,"tif":tif,"barType":barType,"userBuySell":userBuySell,"userAtr":userAtr,"outsideRth":outsideRth}})
    except Exception as e:
        logging.error("Error in updating order status dict "+str(e))



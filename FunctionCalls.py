import Config
import datetime
from header import *
def subscribePrice(ibContract,connection):
    connection.subscribeTicker(ibContract)
    return connection.getTickByTick(ibContract)

def getKey(barType,symbol):
    if (barType == Config.entryTradeType[0]) or (barType == Config.entryTradeType[2]):
        return symbol
    else:
        return (symbol + str(datetime.datetime.now()))

def checkTradingTimeForLb(tradingTime,timeFrame,outsideRth=False):
    conf_trading_time = tradingTime
    if outsideRth:
        conf_trading_time = tradingTime
    configTime = datetime.datetime.strptime(conf_trading_time, "%H:%M:%S")
    # It will add timeframe in config trading time
    configTime = (configTime + datetime.timedelta(seconds=Config.timeDict.get(timeFrame)))
    #  it will change date. changed date into current date
    configTime = datetime.datetime.combine(datetime.datetime.now().date(), configTime.time())
    logging.info("we will get historical data for %s and we will execute historical data %s ",conf_trading_time,configTime)
    if (datetime.datetime.now().time() < configTime.time()):
        # current time is low we need to sleep our thread and wait for trading time.
        return configTime
    else:
        #  we can execute our trade.
        return None

def checkTradingTime(timeFrame,outsideRth=False):
    conf_trading_time = Config.tradingTime
    if outsideRth:
        conf_trading_time = Config.outsideRthTradingtime
    configTime = datetime.datetime.strptime(conf_trading_time, "%H:%M:%S")
    # It will add timeframe in config trading time
    configTime = (configTime + datetime.timedelta(seconds=Config.timeDict.get(timeFrame)))
    #  it will change date. changed date into current date
    configTime = datetime.datetime.combine(datetime.datetime.now().date(), configTime.time())
    logging.info("we will get historical data for %s and we will execute historical data %s ",conf_trading_time,configTime)
    if (datetime.datetime.now().time() < configTime.time()):
        # current time is low we need to sleep our thread and wait for trading time.
        return configTime
    else:
        #  we can execute our trade.
        return None




def getTimeInterval(timeFrame,currentTime):
    midnight = currentTime.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = (currentTime - midnight).seconds
    currtimep = seconds
    # sec = currtimep - (Config.timeDict.get(timeFrame) - (currtimep % Config.timeDict.get(timeFrame)))
    return (Config.timeDict.get(timeFrame) - (currtimep % Config.timeDict.get(timeFrame)))

def getRecentChartTime(timeFrame):
    currentTime = datetime.datetime.now()
    # minuteInterval is giving future second means now ime is 6.6.30 in 5 min timeframe it will give 200 sec  if we add sec in curent time it will show 6.10.00
    minuteInterval = getTimeInterval(timeFrame, currentTime)
    # in futureChartTime we will get 6.5 but we want 6.00.00 chart timming
    futureChartTime = ((currentTime + datetime.timedelta(seconds=minuteInterval)) - datetime.timedelta(seconds=Config.timeDict.get((timeFrame))))
    #  in this variable recent chart time will store
    oldChartTime = (futureChartTime - datetime.timedelta(seconds=Config.timeDict.get((timeFrame))))
    return oldChartTime
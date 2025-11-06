from header import *
from SendTrade import *
import NewTradeFrame as newTrade
from ManagePositionFrame import *
import os as os
import time as time

cacheFile = "Cache.npy"
settingFile = "Settings.npy"
def StatusSaveInFile():
    logging.info("Cache saving start")
    np.save(cacheFile, Config.orderStatusData)
    logging.info("Cache successfully save")
    np.save(settingFile, Config.defaultValue)
    logging.info("Default Setting successfully save")
    print("save successfully")
    print(len(Config.orderStatusData))

def loadCache(connection):
    logging.info("Cache loading start")
    try:
        if path.exists(cacheFile):
            createDate = (os.path.getmtime(cacheFile))
            fileCreateDate= datetime.datetime.fromtimestamp(createDate)
            currentDate = datetime.datetime.now()

            if(currentDate.date() == fileCreateDate.date()):
                    if path.exists(cacheFile):
                        try:
                            Config.orderStatusData = np.load(cacheFile, allow_pickle='TRUE').item()
                            logging.info("Cache successfully load")
                            print("load successfully")
                            print(len(Config.orderStatusData))
                        except Exception as e:
                            logging.warning(f"Could not load cache file: {e}")
                            print(f"Warning: Could not load previous trades cache: {e}")
                            Config.orderStatusData = {}

        if path.exists(settingFile):
            try:
                Config.defaultValue =  np.load(settingFile, allow_pickle='TRUE').item()
                logging.info("Default Setting successfully load")
            except Exception as e:
                logging.warning(f"Could not load settings file: {e}")
                print(f"Warning: Could not load previous settings: {e}")
                Config.defaultValue = {}
    except Exception as e:
        logging.error(f"Error in loadCache: {e}")
        print(f"Starting with fresh configuration")
        Config.orderStatusData = {}
        Config.defaultValue = {}

    restartThread(connection)

def restartThread(connection):

    for key in Config.orderStatusData:
        value = Config.orderStatusData.get(key)
        print(value)
        if (value.get('status') != 'Inactive' and value.get('status') != 'Filled' and value.get('status') != 'Cancelled'):
            if ( value.get('ordType') == 'StopLoss' and value.get('stopLoss') == Config.stopLoss[1]):
                loop = asyncio.get_event_loop()
                print(value)
                asyncio.ensure_future(stopLossThread(connection, value.get('entryData'), value.get('lastPrice'), value.get('action'), value.get('orderId')))

            if (  value.get('ordType') == 'TakeProfit' and  value.get('profit') == Config.takeProfit[4]):
                loop = asyncio.get_event_loop()
                asyncio.ensure_future(takeProfitThread(connection, value.get('entryData'), value.get('lastPrice'), value.get('action'), value.get('orderId')))

            if (value.get('ordType') == 'StopLossInd' and value.get('stopLoss') == Config.stopLoss[1]):
                loop = asyncio.get_event_loop()
                asyncio.ensure_future(stopLossThreadMang(connection,value.get('action'),value.get('contract'),value.get('timeFrame'),value.get('stopLoss'),value.get('totalQuantity'),value.get('orderId')))






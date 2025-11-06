import traceback

from header import *
from SendTrade import *



threadno={}
tradeno={}
symbol_position_list={}
symbol_currency_list={}
symbol=[]
position=[]
timeFrame = []
stopLoss=[]
manQuantity=[]
cancelButton=[]
statusQuantity=[]
addButton = None

def ManagePositionFrame(connection):
    logging.info("Manage Position Frame Init")
    global positionFrame
    positionFrame = Tk()
    s = ttk.Style(positionFrame)
    s.theme_use('clam')
    s.configure('raised.TMenubutton', borderwidth=1)
    positionFrame.title('Open Position')
    positionFrame.protocol("WM_DELETE_WINDOW", on_closing)
    positionFrame.geometry(
        "%dx%d+%d+%d" % (680, 520, (positionFrame.winfo_screenwidth() / 2) - 100, (positionFrame.winfo_screenheight() / 2) - 300))


    container =Frame(positionFrame)
    canvas = Canvas(container,width=660, height=510)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    scrollable_frame = ttk.Frame(canvas)
    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")
        )
    )
    canvas.create_window((-20, 1), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    label(scrollable_frame,connection)
    trades = connection.getAllOpenPosition()
    for trade in trades:
        symbol_position_list.update({trade.contract.symbol: trade.position})
        symbol_currency_list.update({trade.contract.symbol: trade.contract.currency})

    fields(scrollable_frame)

    # openTrade(connection,scrollable_frame)

    container.pack()

    canvas.pack( side = LEFT, expand = True)
    scrollbar.pack(side="left", fill="y")


def label(scrollable_frame,connection):
    labelFrame = Frame(scrollable_frame)
    lblSymbol = Label(labelFrame,  font=(Config.fontName2, Config.fontSize2), text="Symbol")
    lblSymbol.config(width=10)
    lblSymbol.pack(side=LEFT)
    lblPosition = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Position")
    lblPosition.config(width=10)
    lblPosition.pack(side=LEFT)
    lblTimeFrame = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Time Frame")
    lblTimeFrame.config(width=10)
    lblTimeFrame.pack(side=LEFT)
    lblStopLoss = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Stop Loss")
    lblStopLoss.config(width=10)
    lblStopLoss.pack(side=LEFT)
    lblquantity = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Quantity")
    lblquantity.config(width=10)
    lblquantity.pack(side=LEFT)
    lblStatus = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Status")
    lblStatus.config(width=10)
    lblStatus.pack(side=LEFT)
    lblCancel = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Cancel")
    lblCancel.config(width=10)
    lblCancel.pack(side=LEFT)
    labelFrame.pack()
    global addButton
    addButton = Frame(scrollable_frame)
    sorder = Button(addButton, width="15", height="1", text="ADD")
    sorder['command'] = lambda arg2=connection,arg1=scrollable_frame: send_Order(arg2,arg1)
    sorder.pack(side=BOTTOM)
    addButton.pack(side=BOTTOM)
    # zz = Label(scrollable_frame, font=(Config.fontName2, Config.fontSize2), justify=LEFT)
    # zz.pack(fill="y")




def cancelManagePositionTrade(asyncObj,butObj,statusObj,rowNo,connection):
    if asyncObj == None:
        tkinter.messagebox.showinfo('Success', "All Trade Successfully Executed/Canceled")
    else:
        try:
            asyncObj.cancel()
        except Exception as e:
            print(e)
        try:
            if threadno.get(rowNo) != None:
                threadno.get(rowNo).cancel()
                threadno.update({rowNo: None})
        except Exception as e:
            print(e)
        try:
            if tradeno.get(rowNo) != None:
                order = tradeno.get(rowNo)
                connection.cancelTrade(order.order)
        except Exception as e:
            print(e)


        butObj['command'] = lambda arg1=None,arg2=None,arg3=None,arg4=None,arg5=None: cancelManagePositionTrade(arg1,arg2,arg3,arg4,arg5)
        statusObj.config(state="normal")
        statusObj.delete(0, END)
        statusObj.insert(0, "Canceled")
        statusObj.config(state="disabled")
        # tkinter.messagebox.showinfo('Success', "Successfully Canceled")





def sendStpTrade(connection,symbol,position,timeframe,stoploss,quan,statusobj,rowNo):

    try:
        statusobj.delete(0, END)
        statusobj.insert(0, "Execute")
        logging.info(f"sending trade in manage position symbol is {symbol} , position {position} , timeframe {timeframe}, stoploss {stoploss}, quantity is {quan}")
        if quan == "" or quan == "0":
            position = position
        else:
            position = int(quan)

        if position == None or position == 0:
            tkinter.messagebox.showinfo('Error', "Position Value Not Found")
            return None

        loop = asyncio.get_event_loop()
        action="BUY"
        if position >0:
            action = "SELL"
        else:
            position = abs(position)

        position = round(position,Config.roundVal)
        ibcontract = getContract(symbol,symbol_currency_list.get(symbol))
        logging.info(f"manage position sending stopLoss first trade contract is {ibcontract} action is {action} time frame {timeframe} stop loss {stoploss} position {position} quan {quan}")
        x = asyncio.ensure_future(sendStopLoss(connection,position,action,ibcontract,timeframe,stoploss,rowNo))
        return x
    except Exception as e:
        print(e)
        return None



def send_Order(connection,scrollable_frame):
    print("new row adding.")

    global statusQuantity
    statusQuantity[len(statusQuantity) - 1].delete(0, END)
    statusQuantity[len(statusQuantity) - 1].insert(0, "Execute")
    loop = asyncio.get_event_loop()
    symbol_data = symbol[len(symbol) - 1].get()

    # position[len(position) - 1].current()

    asyncobj = sendStpTrade(connection,symbol_data,symbol_position_list.get(symbol_data),timeFrame[len(timeFrame) - 1].get(),
                                           stopLoss[len(stopLoss) - 1].get(),manQuantity[len(manQuantity) - 1].get(),statusQuantity[len(statusQuantity) - 1],len(symbol)  )

    if (asyncobj != None):
        but = cancelButton[len(cancelButton) - 1]
        but['command'] = lambda arg1=asyncobj, arg2=but, arg3=statusQuantity[len(statusQuantity) - 1],arg4=len(symbol),arg5=connection: cancelManagePositionTrade(arg1, arg2, arg3,arg4,arg5)

    disableEntry()
    fields(scrollable_frame)






def disableEntry():
    symbol[len(symbol) - 1].config(state="disabled")
    timeFrame[len(timeFrame) - 1].config(state="disabled")
    position[len(position) - 1].config(state="disabled")
    stopLoss[len(stopLoss) - 1].config(state="disabled")
    manQuantity[len(manQuantity) - 1].config(state="disabled")


def symbolchange(event):
    position[len(position) - 1].config(state="normal")
    position[len(position) - 1].delete(0, END)
    position[len(position) - 1].insert(0, symbol_position_list.get(symbol[len(symbol) - 1].get()))
    position[len(position) - 1].config(state="disabled")

def fields(scrollable_frame):
    symbol_list = []
    position_list = []
    for k, v in symbol_position_list.items():
        symbol_list.append(k)
        position_list.append(v)
    if len(symbol_list) ==0:
        return

    field = Frame(scrollable_frame)
    secEntry = ttk.Combobox(field, state="readonly", width="10", value=symbol_list)
    secEntry.config(width=10)
    secEntry.bind( '<<ComboboxSelected>>', symbolchange)
    secEntry.pack(side=LEFT, padx=9)
    secEntry.current(0)
    symbol.append(secEntry)
    posEntry = Entry(field,  width="10", textvariable=StringVar(field,value=symbol_position_list.get(symbol_list[0]))  )
    posEntry.config(width=10)
    posEntry.pack(side=LEFT, padx=9)
    posEntry.config(state="disabled")
    position.append(posEntry)


    tmEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.timeFrame)
    tmEntry.config(width=10)
    tmEntry.pack(side=LEFT, padx=9)
    tmEntry.current(0)
    setDefaultTimeFrame(tmEntry)
    timeFrame.append(tmEntry)

    stpLossEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.stopLoss)
    stpLossEntry.config(width=10)
    stpLossEntry.pack(side=LEFT,padx=9)
    stpLossEntry.current(0)
    setDefaultStp(stpLossEntry)
    stopLoss.append(stpLossEntry)

    quanEntry = Entry(field, width="10", textvariable=StringVar(field))
    quanEntry.config(width=10)
    quanEntry.pack(side=LEFT, padx=9)
    manQuantity.append(quanEntry)

    statusEntry = Entry(field, width="10", textvariable=StringVar(field))
    statusEntry.config(width=10)
    statusEntry.pack(side=LEFT, padx=9)
    statusQuantity.append(statusEntry)

    but = Button(field, width="10", height="1", text="Cancel")
    but['command'] = lambda arg1=None, arg2=None, arg3=None, arg4=None,arg5=None: cancelManagePositionTrade(arg1, arg2, arg3,arg4,arg5)
    but.pack(side=LEFT, padx=9)
    cancelButton.append(but)
    field.pack(side=TOP, pady=8)




def setDefaultTimeFrame(timeFrame):
    if Config.defaultValue.get("timeFrame") != None:
        timeFrame.current(Config.timeFrame.index(Config.defaultValue.get("timeFrame")))


def setDefaultStp(stpLoss):
    if Config.defaultValue.get("stpLoss") != None:
        stpLoss.current(Config.stopLoss.index(Config.defaultValue.get("stpLoss")))

def setDefaultQuantity(quantity):
    if Config.defaultValue.get("quantity") != None:
        quantity.delete(0, END)
        quantity.insert(0, Config.defaultValue.get("quantity"))


def checkTradingTimeStopLoss(timeFrame):
    try:
        configTime = datetime.datetime.strptime(Config.tradingTime, "%H:%M:%S")
        # It will add timeframe in config trading time
        configTime = (configTime + datetime.timedelta(seconds=Config.timeDict.get(timeFrame)))
        #  it will change date. changed date into current date
        configTime = datetime.datetime.combine(datetime.datetime.now().date(), configTime.time())
        logging.info("we will get historical data for %s and we will execute historical data %s ",Config.tradingTime,configTime)
        if (datetime.datetime.now().time() < configTime.time()):
            # current time is low we need to sleep our thread and wait for trading time.
            return configTime
        else:
            #  we can execute our trade.
            return None
    except Exception as e:
        logging.error("error in checking trading time %s ", e)
        print(e)

async def sendStopLoss(connection, position,action,ibContract,timeFrame,stopLoss,rowNo):
    print('end stop loss working')
    try:
        tradingTime = checkTradingTimeStopLoss(timeFrame)
        if tradingTime != None:
            logging.info("thread will sleep for %s", (tradingTime - datetime.datetime.now()).total_seconds())
            sec = (tradingTime - datetime.datetime.now()).total_seconds()
            tradingTime = (tradingTime - datetime.timedelta(seconds=Config.timeDict.get((timeFrame))))
            await asyncio.sleep(sec)
        else:
            tradingTime = datetime.datetime.strptime(Config.tradingTime, "%H:%M:%S")
        while True:
            print('first loop working')
            logging.info("symbol is %s ",ibContract.symbol)
            if Config.historicalData.get(ibContract.symbol) == None:
                histData = connection.getHistoricalChartData(ibContract, timeFrame, tradingTime)
                logging.info("manage position hist data is %s , timing %s ",histData,tradingTime)
                print("entry hisTORICAL DATA ", histData)
                if (len(histData) == 0):
                    logging.info("Chart Data is Not Comming")
                    await asyncio.sleep(2)
                    continue
                else:
                    Config.historicalData.update({ibContract.symbol: histData})

            histData = Config.historicalData.get(ibContract.symbol)

            price = 0
            if(action == "BUY"):
                price= float(histData['high'])
                price = price + 0.01
            else:
                price= float(histData['low'])
                price = price - 0.01
            price = round(price,Config.roundVal)
            logging.info(f" data found stploss histdata is {histData} contract is {ibContract} position {position} price {price} action {action} ")
            stpResponse = sendStpLoss(connection, price,action,ibContract,timeFrame,stopLoss,0,position,histData)
            tradeno.update({rowNo: stpResponse})

            if(stopLoss == Config.stopLoss[1]):
                loop = asyncio.get_event_loop()
                x = asyncio.ensure_future(stopLossThreadMang(connection,action,ibContract,timeFrame,stopLoss,position,stpResponse.order.orderId))
                threadno.update({rowNo: x})

            else:
                threadno.update({rowNo:None})
            break
    except Exception as e:
        print(traceback.format_exc())


async def stopLossThreadMang(connection,action,ibContract,timeFrame,stopLoss,quantity,orderId):
    try:
        lmtData = Config.orderStatusData.get(orderId)
        print(" manage position stp loss thread is running")
        currentTime = datetime.datetime.now()
        minuteInterval = getTimeInterval(lmtData['timeFrame'],currentTime)
        chartTime = ((currentTime + datetime.timedelta(seconds=minuteInterval))- datetime.timedelta(seconds=Config.timeDict.get((lmtData['timeFrame']))))
        sleepTime = ((minuteInterval) + 1)
        logging.info("Thread is going to sleep %s  in second and timeframe is %s", sleepTime, lmtData['timeFrame'])
        print("(first time) Thread is going to sleep %s   current datetime is %s  and chart timming  %s", sleepTime, currentTime,chartTime)
        await asyncio.sleep(sleepTime)
        print("stop loss status after sleep %s", lmtData['status'])
        nextSleepTime = Config.timeDict.get(lmtData['timeFrame'])
        while (lmtData != None and (lmtData['status'] != 'Filled' and lmtData['status'] != 'Cancelled' and lmtData['status'] != 'Inactive')):
            print("running in stop loss while, status is %s", lmtData['status'])
            histData = connection.getHistoricalChartData(lmtData['contract'], lmtData['timeFrame'], chartTime)
            if (len(histData) == 0):
                logging.info("hist data not found going to sleep for 1 second")
                print("hist data not found")
                nextSleepTime = nextSleepTime - 1
                if (nextSleepTime == 0):
                    nextSleepTime = Config.timeDict.get(lmtData['timeFrame'])
                await asyncio.sleep(1)
                continue
            if lmtData['action'] == "BUY":
                price = float(histData['high'])
                price = price + 0.01
            else:
                price = float(histData['low'])
                price = price - 0.01
            price = round(price, Config.roundVal)
            print("New Price %s ",price)

            logging.info(f" data found in manage position thread stploss histdata is {histData} contract is {ibContract} position {quantity} price {price} action {action} stopLoss {stopLoss} timeFrame {timeFrame}")

            sendStpLoss(connection, price,action,ibContract,timeFrame,stopLoss,orderId,quantity,histData)
            logging.info("BarByBar stopLoss thread is sleeping for %s time in second ", Config.timeDict.get(lmtData['timeFrame']))
            print("(second time) BarByBar stopLoss thread is sleeping for %s time in second ", Config.timeDict.get(lmtData['timeFrame']))
            chartTime = (chartTime + datetime.timedelta(seconds=Config.timeDict.get((lmtData['timeFrame']))))
            logging.info("barByBar stop loss new chart data time %s ", chartTime)
            await asyncio.sleep(nextSleepTime)
            lmtData = Config.orderStatusData.get(orderId)

        print("stop loss thread end")
    except Exception as e:
        print(e)

def sendStpLoss(connection, price,action,ibContract,timeFrame,stopLoss,orderId,quantity,histData):
    # if orderId == 0:
    #     stpResponse = connection.placeTrade(contract=ibContract,
    #                                         order=Order(orderType="STP", action=action,
    #                                                     totalQuantity=quantity, auxPrice=price, orderId=orderId))
    # else:
    stpResponse = connection.placeTrade(contract=ibContract,
                                            order=Order(orderType="STP", action=action,
                                                        totalQuantity=quantity,auxPrice=price, orderId=orderId))
    StatusUpdate(stpResponse, 'StopLossInd', ibContract, 'STP', action, quantity, histData, price, '', timeFrame, '', stopLoss, 0,'','','','','',0,False)
    return stpResponse

def on_closing():
    Config.manage_frame_check = False
    threadno = {}
    tradeno = {}
    timeframe = {}
    stoploss = {}
    quantity={}
    symbol_position_list = {}
    symbol = []
    position = []
    timeFrame = []
    stopLoss = []
    manQuantity = []
    cancelButton = []
    statusQuantity = []
    addButton = None
    positionFrame.destroy()



def getTimeInterval(timeFrame,currentTime):
    try:
        midnight = currentTime.replace(hour=0, minute=0, second=0, microsecond=0)
        seconds = (currentTime - midnight).seconds
        currtimep = seconds
        sec = currtimep - (Config.timeDict.get(timeFrame) - (currtimep % Config.timeDict.get(timeFrame)))
        # currentHour = ((currentTime.time().hour * 60) * 60)
        # currentMinute = (currentHour + currentTime.time().second)
        # minuteInterval = currentMinute + (Config.timeDict.get(timeFrame) - (currentMinute % Config.timeDict.get(timeFrame)))
        # minuteInterval = (minuteInterval - currentMinute)
        return (Config.timeDict.get(timeFrame) - (currtimep % Config.timeDict.get(timeFrame)))
    except Exception as e:
        print(e)
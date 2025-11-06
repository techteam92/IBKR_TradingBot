import Config
from header import *
from StatusSaveInFile import *
from StatusUpdate import *

data={}
settingFrame = None
def DefaultSetting(connection):
    logging.info("Default Setting")


    global settingFrame
    settingFrame = Tk()
    s = ttk.Style(settingFrame)
    s.theme_use('clam')
    s.configure('raised.TMenubutton', borderwidth=1)
    settingFrame.title('Setting')
    settingFrame.protocol("WM_DELETE_WINDOW", on_closing)
    settingFrame.geometry(
        "%dx%d+%d+%d" % (
            300, 350, (settingFrame.winfo_screenwidth() / 2) - 500, (settingFrame.winfo_screenheight() / 2) - 300))
    settingFrame.attributes('-topmost', True)
    content()

def content():
    # Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Symbol:- ", justify=LEFT).place(
    #     x=20, y=20)
    #
    # firstEntry = Entry(settingFrame, width="13", textvariable=StringVar(settingFrame))
    # firstEntry.place(x=150, y=21)
    # setDefaultSymbol(firstEntry)
    # data.update({"symbol":firstEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Time Frame:- ", justify=LEFT).place(
        x=20, y=20)
    secEntry = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.timeFrame)
    secEntry.place(x=150, y=21)
    secEntry.current(0)
    setDefaultTimeFrame(secEntry)
    data.update({"timeFrame": secEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Profit:- ", justify=LEFT).place(
        x=20, y=50)
    profitEntry = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.takeProfit)
    profitEntry.place(x=150, y=52)
    profitEntry.current(0)
    setDefaultProfit(profitEntry)
    data.update({"Profit": profitEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Stop Loss:- ", justify=LEFT).place(
        x=20, y=80)
    stpLossEntry = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.stopLoss)
    stpLossEntry.place(x=150, y=82)
    stpLossEntry.current(0)
    setDefaultStp(stpLossEntry)
    data.update({"stopLoss": stpLossEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Time In Force:- ", justify=LEFT).place(
        x=20, y=110)
    tifEntry = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.timeInForce)
    tifEntry.place(x=150, y=112)
    tifEntry.current(0)
    setDefaultTif(tifEntry)
    data.update({"tif": tifEntry})


    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Entry Trade Type:- ", justify=LEFT).place(
        x=20, y=140)
    entryType = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.entryTradeType)
    entryType.place(x=150, y=142)
    entryType.current(0)
    setDefaultEntryType(entryType)
    data.update({"entryType": entryType})


    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Buy/Sell:- ", justify=LEFT).place(
        x=20, y=170)
    buySellType = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.buySell)
    buySellType.place(x=150, y=172)
    buySellType.current(0)
    setDefaultBuySell(buySellType)
    data.update({"buySellType": buySellType})


    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Risk:- ", justify=LEFT).place(
        x=20, y=200)
    riskEntry = Entry(settingFrame, width="13", textvariable=StringVar(settingFrame))
    riskEntry.place(x=150, y=202)
    setDefaultRisk(riskEntry)
    data.update({"risk": riskEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="ATR:- ", justify=LEFT).place(
        x=20, y=230)
    atrEntry = Entry(settingFrame, width="13", textvariable=StringVar(settingFrame))
    atrEntry.place(x=150, y=232)
    setDefaultAtr(atrEntry)
    data.update({"atr": atrEntry})

    # Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Pre/Post:- ", justify=LEFT).place(
    #     x=20, y=258)
    # rthType = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.prePostBool)
    # rthType.place(x=150, y=260)
    # rthType.current(0)
    # setDefaultRth(rthType)
    # data.update({"rth": rthType})

    # Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Set Daily PNL, for position close. ", justify=LEFT).place(
    #     x=20, y=286)
    #
    # Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Pnl:- ", justify=LEFT).place(
    #     x=20, y=314)
    # pnlType = Entry(settingFrame, width="13", textvariable=StringVar(settingFrame))
    # pnlType.place(x=150, y=316)
    # setDefaultPnl(pnlType)
    # data.update({"pnl": pnlType})




    update = Button(settingFrame, width="10", height="1", text="Save", command=updateSetting)
    update.place(relx=0.43, rely=0.9, anchor=CENTER)


def updateSetting():
    Config.defaultValue.update({"tif": data.get("tif").get()})
    Config.defaultValue.update({"symbol": ""})
    Config.defaultValue.update({"timeFrame": data.get("timeFrame").get()})
    Config.defaultValue.update({"profit": data.get("Profit").get()})
    Config.defaultValue.update({"stpLoss": data.get("stopLoss").get()})
    Config.defaultValue.update({"risk": data.get("risk").get()})
    Config.defaultValue.update({"entryType": data.get("entryType").get()})
    Config.defaultValue.update({"buySellType": data.get("buySellType").get()})
    Config.defaultValue.update({"atr": data.get("atr").get()})

    # Config.defaultValue.update({"rthType": data.get("rth").get()})
    # Config.defaultValue.update({"pnl": data.get("pnl").get()})
    StatusSaveInFile()
    tkinter.messagebox.showinfo('Success', "Setting Saved")

# def setDefaultSymbol(symbol):
#     if Config.defaultValue.get("symbol") != None:
#         symbol.delete(0, END)
#         symbol.insert(0, Config.defaultValue.get("symbol"))

def setDefaultTimeFrame(timeFrame):
    if Config.defaultValue.get("timeFrame") != None:
        timeFrame.current(Config.timeFrame.index(Config.defaultValue.get("timeFrame")))

def setDefaultProfit(profit):
    if Config.defaultValue.get("profit") != None:
        profit.current(Config.takeProfit.index(Config.defaultValue.get("profit")))

def setDefaultStp(stpLoss):
    if Config.defaultValue.get("stpLoss") != None:
        stpLoss.current(Config.stopLoss.index(Config.defaultValue.get("stpLoss")))

def setDefaultEntryType(entryType):
    if Config.defaultValue.get("entryType") != None:
        entryType.current(Config.entryTradeType.index(Config.defaultValue.get("entryType")))

def setDefaultTif(tif):
    if Config.defaultValue.get("tif") != None:
        tif.current(Config.timeInForce.index(Config.defaultValue.get("tif")))

def setDefaultAtr(atr):
    if Config.defaultValue.get("atr") != None:
        atr.delete(0, END)
        atr.insert(0, Config.defaultValue.get("atr"))
def setDefaultPnl(pnlType):
    if Config.defaultValue.get("pnl") != None:
        pnlType.delete(0, END)
        pnlType.insert(0, Config.defaultValue.get("pnl"))

def setDefaultRisk(risk):
    if Config.defaultValue.get("risk") != None:
        risk.delete(0, END)
        risk.insert(0, Config.defaultValue.get("risk"))


def setDefaultRth(rthType):
    if Config.defaultValue.get("rthType") != None:
        if Config.defaultValue.get("rthType") == 'True':
            rthType.current(Config.prePostBool.index(True))
        else:
            rthType.current(Config.prePostBool.index(False))

def setDefaultBuySell(buySellType):
    if Config.defaultValue.get("buySellType") != None:
        buySellType.current(Config.buySell.index((Config.defaultValue.get("buySellType"))))

def on_closing():
    settingFrame.destroy()
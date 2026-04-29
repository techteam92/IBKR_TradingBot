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
            340, 430, (settingFrame.winfo_screenwidth() / 2) - 500, (settingFrame.winfo_screenheight() / 2) - 300))
    settingFrame.attributes('-topmost', True)
    content()

def content():
    # Order: Stop Loss, Trade Type, Buy/Sell, Risk, Profit, Time Frame, Time In Force, Replay, Break Even

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Stop Loss:- ", justify=LEFT).place(
        x=20, y=20)
    stpLossEntry = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.stopLoss)
    stpLossEntry.place(x=150, y=21)
    stpLossEntry.current(0)
    setDefaultStp(stpLossEntry)
    data.update({"stopLoss": stpLossEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Entry Trade Type:- ", justify=LEFT).place(
        x=20, y=50)
    entryType = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.entryTradeType)
    entryType.place(x=150, y=52)
    entryType.current(0)
    setDefaultEntryType(entryType)
    data.update({"entryType": entryType})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Buy/Sell:- ", justify=LEFT).place(
        x=20, y=80)
    buySellType = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.buySell)
    buySellType.place(x=150, y=82)
    buySellType.current(0)
    setDefaultBuySell(buySellType)
    data.update({"buySellType": buySellType})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Risk:- ", justify=LEFT).place(
        x=20, y=110)
    riskEntry = Entry(settingFrame, width="13", textvariable=StringVar(settingFrame))
    riskEntry.place(x=150, y=112)
    setDefaultRisk(riskEntry)
    data.update({"risk": riskEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Profit:- ", justify=LEFT).place(
        x=20, y=140)
    profitEntry = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.takeProfit)
    profitEntry.place(x=150, y=142)
    profitEntry.current(0)
    setDefaultProfit(profitEntry)
    data.update({"Profit": profitEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Time Frame:- ", justify=LEFT).place(
        x=20, y=170)
    secEntry = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.timeFrame)
    secEntry.place(x=150, y=172)
    secEntry.current(0)
    setDefaultTimeFrame(secEntry)
    data.update({"timeFrame": secEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Time In Force:- ", justify=LEFT).place(
        x=20, y=200)
    tifEntry = ttk.Combobox(settingFrame, state="readonly", width="10", value=Config.timeInForce)
    tifEntry.place(x=150, y=202)
    tifEntry.current(0)
    setDefaultTif(tifEntry)
    data.update({"tif": tifEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Replay:- ", justify=LEFT).place(
        x=20, y=230)
    replayEntry = ttk.Combobox(settingFrame, state="readonly", width="10", values=["Off", "On"])
    replayEntry.place(x=150, y=232)
    replayEntry.current(0)
    setDefaultReplay(replayEntry)
    data.update({"replay": replayEntry})

    Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="Break Even:- ", justify=LEFT).place(
        x=20, y=260)
    breakEvenEntry = ttk.Combobox(settingFrame, state="readonly", width="10", values=["Off", "On"])
    breakEvenEntry.place(x=150, y=262)
    breakEvenEntry.current(0)
    setDefaultBreakEven(breakEvenEntry)
    data.update({"breakEven": breakEvenEntry})

    Button(
        settingFrame, width="18", height="1", text="Option defaults…",
        command=_open_option_defaults_modal,
    ).place(x=20, y=292)

    # ATR field disabled - functionality removed
    # Label(settingFrame, font=(Config.fontName2, Config.fontSize2), text="ATR:- ", justify=LEFT).place(
    #     x=20, y=230)
    # atrEntry = Entry(settingFrame, width="13", textvariable=StringVar(settingFrame))
    # atrEntry.place(x=150, y=232)
    # setDefaultAtr(atrEntry)
    # Create a hidden entry with default value to maintain compatibility
    atrEntry = Entry(settingFrame, width="0")
    atrEntry.insert(0, "0")
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
    update.place(relx=0.43, rely=0.91, anchor=CENTER)


EXPIRY_OPTIONS_DISPLAY = [
    "0 = Current Friday", "1 = Next Friday", "2 = Next Next Friday", "3 = Next Next Next Friday"
]


def _persist_option_defaults_to_config(opt_strike_combo, opt_expire_combo, opt_entry_ot, opt_sl_ot, opt_tp_ot, opt_risk_entry):
    """Write option-default widgets to Config.defaultValue (same keys as NewTradeFrame addField)."""
    strike_map_reverse = {"ATM": "ATM", "OTM 1": "OTM1", "OTM 2": "OTM2", "OTM 3": "OTM3"}
    sel_strike = opt_strike_combo.get()
    Config.defaultValue.update({"optStrike": strike_map_reverse.get(sel_strike, "ATM")})
    exp_disp = opt_expire_combo.get().strip()
    exp_val = "0"
    if exp_disp and "=" in exp_disp:
        exp_val = exp_disp.split("=")[0].strip()
    if exp_val not in ("0", "1", "2", "3"):
        exp_val = "0"
    Config.defaultValue.update({"optExpire": exp_val})
    for file_key, combo in (
        ("optEntryOT", opt_entry_ot),
        ("optSlOT", opt_sl_ot),
        ("optTpOT", opt_tp_ot),
    ):
        wval = combo.get()
        if wval not in Config.optionOrderTypes:
            wval = "Market"
        Config.defaultValue.update({file_key: wval})
    Config.defaultValue.update({"optRisk": opt_risk_entry.get().strip()})


def _open_option_defaults_modal():
    """Modal to edit default option trading parameters (strike, expiry, order types, risk)."""
    parent = settingFrame
    if parent is None:
        return
    modal = Toplevel(parent)
    modal.title("Option defaults")
    modal.resizable(False, False)
    modal.transient(parent)
    modal.grab_set()
    modal.attributes('-topmost', True)

    w, h = 420, 400
    modal.update_idletasks()
    x = (modal.winfo_screenwidth() // 2) - (w // 2)
    y = (modal.winfo_screenheight() // 2) - (h // 2)
    modal.geometry("%dx%d+%d+%d" % (w, h, x, y))

    pad = {"padx": 12, "pady": 6}

    Label(modal, font=(Config.fontName2, Config.fontSize2), text="Strike:", justify=LEFT).grid(row=0, column=0, sticky="w", **pad)
    opt_strike = ttk.Combobox(modal, state="readonly", width=16, values=["ATM", "OTM 1", "OTM 2", "OTM 3"])
    opt_strike.grid(row=0, column=1, sticky="ew", **pad)
    setDefaultOptStrike(opt_strike)

    Label(modal, font=(Config.fontName2, Config.fontSize2), text="Expiration:", justify=LEFT).grid(row=1, column=0, sticky="w", **pad)
    opt_expire = ttk.Combobox(modal, state="readonly", width=28, values=EXPIRY_OPTIONS_DISPLAY)
    opt_expire.grid(row=1, column=1, sticky="ew", **pad)
    setDefaultOptExpire(opt_expire, EXPIRY_OPTIONS_DISPLAY)

    Label(modal, font=(Config.fontName2, Config.fontSize2), text="Risk ($):", justify=LEFT).grid(row=2, column=0, sticky="w", **pad)
    opt_risk = Entry(modal, width=18, font=(Config.fontName2, Config.fontSize2))
    opt_risk.grid(row=2, column=1, sticky="ew", **pad)
    setDefaultOptRisk(opt_risk)

    Label(modal, font=(Config.fontName2, Config.fontSize2), text="Entry order type:", justify=LEFT).grid(row=3, column=0, sticky="w", **pad)
    opt_e = ttk.Combobox(modal, state="readonly", width=16, values=Config.optionOrderTypes)
    opt_e.grid(row=3, column=1, sticky="ew", **pad)
    setDefaultOptOrderType(opt_e, "optEntryOT")

    Label(modal, font=(Config.fontName2, Config.fontSize2), text="Stop loss order type:", justify=LEFT).grid(row=4, column=0, sticky="w", **pad)
    opt_sl = ttk.Combobox(modal, state="readonly", width=16, values=Config.optionOrderTypes)
    opt_sl.grid(row=4, column=1, sticky="ew", **pad)
    setDefaultOptOrderType(opt_sl, "optSlOT")

    Label(modal, font=(Config.fontName2, Config.fontSize2), text="Profit order type:", justify=LEFT).grid(row=5, column=0, sticky="w", **pad)
    opt_tp = ttk.Combobox(modal, state="readonly", width=16, values=Config.optionOrderTypes)
    opt_tp.grid(row=5, column=1, sticky="ew", **pad)
    setDefaultOptOrderType(opt_tp, "optTpOT")

    info = (
        "These apply to new trade rows and the per-row Option dialog. "
        "Bid+ / Ask- adjust price every 2s until fill."
    )
    Label(modal, font=(Config.fontName2, 9), text=info, justify=LEFT, wraplength=w - 24).grid(
        row=6, column=0, columnspan=2, sticky="w", padx=12, pady=10)

    btn_fr = Frame(modal)
    btn_fr.grid(row=7, column=0, columnspan=2, pady=16)

    def on_ok():
        _persist_option_defaults_to_config(opt_strike, opt_expire, opt_e, opt_sl, opt_tp, opt_risk)
        StatusSaveInFile()
        modal.destroy()
        tkinter.messagebox.showinfo("Saved", "Option defaults saved.")

    def on_cancel():
        modal.destroy()

    Button(btn_fr, width=10, height="1", text="OK", command=on_ok).pack(side=LEFT, padx=8)
    Button(btn_fr, width=10, height="1", text="Cancel", command=on_cancel).pack(side=LEFT, padx=8)

    modal.grid_columnconfigure(1, weight=1)
    modal.bind("<Escape>", lambda e: on_cancel())
    modal.protocol("WM_DELETE_WINDOW", on_cancel)


def _write_setting_form_to_config_and_file():
    """Write current Setting dialog values to Config.defaultValue and persist to file."""
    if not data:
        return
    Config.defaultValue.update({"tif": data.get("tif").get()})
    Config.defaultValue.update({"symbol": ""})
    Config.defaultValue.update({"timeFrame": data.get("timeFrame").get()})
    Config.defaultValue.update({"profit": data.get("Profit").get()})
    Config.defaultValue.update({"stpLoss": data.get("stopLoss").get()})
    Config.defaultValue.update({"risk": data.get("risk").get()})
    Config.defaultValue.update({"entryType": data.get("entryType").get()})
    Config.defaultValue.update({"buySellType": data.get("buySellType").get()})
    Config.defaultValue.update({"atr": data.get("atr").get()})
    replay_val = data.get("replay").get()
    Config.defaultValue.update({"replay": replay_val == "On"})
    break_even_val = data.get("breakEven").get()
    Config.defaultValue.update({"breakEven": "True" if break_even_val == "On" else "False"})

    # Option defaults are edited in the Option defaults modal only (keys left unchanged here).

    StatusSaveInFile()


def updateSetting():
    _write_setting_form_to_config_and_file()
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

def setDefaultReplay(replayCombo):
    """Set Replay default from saved setting. Same as UI: On = re-enter trade when stop loss fills."""
    val = Config.defaultValue.get("replay")
    if val is None:
        return
    if isinstance(val, str):
        val = val.lower() in ("true", "1", "yes", "on")
    replayCombo.current(1 if val else 0)

def setDefaultBreakEven(breakEvenCombo):
    """Set Break Even default from saved setting. Stored as 'True'/'False' for NewTradeFrame."""
    val = Config.defaultValue.get("breakEven")
    if val is None:
        return
    if isinstance(val, str):
        on = val.lower() in ("true", "1", "yes", "on")
    else:
        on = bool(val)
    breakEvenCombo.current(1 if on else 0)


def setDefaultOptStrike(combo):
    raw = str(Config.defaultValue.get("optStrike") or "ATM").strip().upper().replace(" ", "")
    mapping = {"ATM": "ATM", "OTM1": "OTM 1", "OTM2": "OTM 2", "OTM3": "OTM 3"}
    disp = mapping.get(raw, "ATM")
    combo.set(disp)


def setDefaultOptExpire(combo, expiry_options_display):
    expiry_options_values = ["0", "1", "2", "3"]
    val = str(Config.defaultValue.get("optExpire", "0")).strip()
    if val in expiry_options_values:
        idx = expiry_options_values.index(val)
        combo.set(expiry_options_display[idx])
    else:
        combo.set(expiry_options_display[0])


def setDefaultOptOrderType(combo, key):
    v = Config.defaultValue.get(key, "Market")
    if v not in Config.optionOrderTypes:
        v = "Market"
    combo.current(Config.optionOrderTypes.index(v))


def setDefaultOptRisk(risk_entry):
    v = Config.defaultValue.get("optRisk")
    if v is not None and str(v).strip() != "":
        risk_entry.insert(0, str(v).strip())

def on_closing():
    # Save current settings when closing the dialog so they persist and load on next app run
    try:
        _write_setting_form_to_config_and_file()
        logging.debug("Settings saved on Setting dialog close")
    except Exception as e:
        logging.warning("Could not save settings on close: %s", e)
    settingFrame.destroy()
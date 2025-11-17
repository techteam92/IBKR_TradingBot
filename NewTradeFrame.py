import asyncio

from header import *
from DefaultSetting import *
from SendTrade import *
from SendTrade import _get_latest_hist_bar
symbol=[]
timeFrame=[]
takeProfit=[]
stopLoss=[]
stopLossValue=[]
breakEven=[]
risk=[]
status = []
timeInForce = []
tradeType = []
buySell = []
entry_points = []
atr=[]
quantity=[]
cancelButton = []

rowPosition=0
buttonRely=0.3
addButton = None
IbConn = None
scrollable_frame=None

def _show_entry_price_modal(trade_type_combo, entry_points_entry, order_type_name):
    """
    Show a modal dialog to enter entry price for Limit Order or Stop Order.
    """
    # Get parent window from the combobox
    parent = trade_type_combo.winfo_toplevel()
    
    # Create modal dialog
    modal = tkinter.Toplevel(parent)
    modal.title(f"{order_type_name} - Entry Price")
    modal.geometry("300x150")
    modal.resizable(False, False)
    modal.transient(parent)  # Make it modal relative to parent
    modal.grab_set()  # Make it modal
    
    # Center the dialog
    modal.update_idletasks()
    x = (modal.winfo_screenwidth() // 2) - (300 // 2)
    y = (modal.winfo_screenheight() // 2) - (150 // 2)
    modal.geometry(f"300x150+{x}+{y}")
    
    # Get current value if any
    current_value = entry_points_entry.get() if entry_points_entry.get() else "0"
    
    # Store previous selection index
    previous_index = 0  # Default to first option
    if hasattr(trade_type_combo, '_previous_index'):
        previous_index = trade_type_combo._previous_index
    
    # Label
    Label(modal, text=f"Enter Entry Price for {order_type_name}:", font=(Config.fontName2, Config.fontSize2)).pack(pady=10)
    
    # Entry field
    value_var = StringVar(modal, value=current_value)
    entry_widget = Entry(modal, textvariable=value_var, width=15, font=(Config.fontName2, Config.fontSize2))
    entry_widget.pack(pady=5)
    entry_widget.select_range(0, END)
    entry_widget.focus()
    
    # Buttons frame
    button_frame = Frame(modal)
    button_frame.pack(pady=10)
    
    def save_value():
        try:
            val = value_var.get().strip()
            if val:
                float(val)  # Validate it's a number
                entry_points_entry.delete(0, END)
                entry_points_entry.insert(0, val)
            else:
                entry_points_entry.delete(0, END)
                entry_points_entry.insert(0, "0")
            modal.destroy()
        except ValueError:
            tkinter.messagebox.showerror("Invalid Input", "Please enter a valid number")
            entry_widget.focus()
    
    def cancel_dialog():
        # Reset to previous selection if cancelled
        modal.destroy()
        trade_type_combo.current(previous_index)
    
    Button(button_frame, text="OK", width=8, command=save_value).pack(side=LEFT, padx=5)
    Button(button_frame, text="Cancel", width=8, command=cancel_dialog).pack(side=LEFT, padx=5)
    
    # Bind Enter key to save
    entry_widget.bind("<Return>", lambda e: save_value())
    entry_widget.bind("<Escape>", lambda e: cancel_dialog())
    
    # Wait for modal to close
    modal.wait_window()

def _show_custom_stop_loss_modal(stop_loss_combo, value_entry):
    """
    Show a modal dialog to enter custom stop loss value when "Custom" is selected.
    """
    # Get parent window from the combobox
    parent = stop_loss_combo.winfo_toplevel()
    
    # Create modal dialog
    modal = tkinter.Toplevel(parent)
    modal.title("Custom Stop Loss")
    modal.geometry("300x150")
    modal.resizable(False, False)
    modal.transient(parent)  # Make it modal relative to parent
    modal.grab_set()  # Make it modal
    
    # Center the dialog
    modal.update_idletasks()
    x = (modal.winfo_screenwidth() // 2) - (300 // 2)
    y = (modal.winfo_screenheight() // 2) - (150 // 2)
    modal.geometry(f"300x150+{x}+{y}")
    
    # Get current value if any
    current_value = value_entry.get() if value_entry.get() else "0"
    
    # Store previous selection index (before "Custom" was selected)
    # Since we're already on "Custom", we need to track what was before
    # We'll default to 0 (EntryBar) if we can't determine
    previous_index = 0  # Default to EntryBar
    # Try to find a stored previous index, or use default
    if hasattr(stop_loss_combo, '_previous_index'):
        previous_index = stop_loss_combo._previous_index
    
    # Label
    Label(modal, text="Enter Custom Stop Loss Value:", font=(Config.fontName2, Config.fontSize2)).pack(pady=10)
    
    # Entry field
    value_var = StringVar(modal, value=current_value)
    entry_widget = Entry(modal, textvariable=value_var, width=15, font=(Config.fontName2, Config.fontSize2))
    entry_widget.pack(pady=5)
    entry_widget.select_range(0, END)
    entry_widget.focus()
    
    # Buttons frame
    button_frame = Frame(modal)
    button_frame.pack(pady=10)
    
    def save_value():
        try:
            val = value_var.get().strip()
            if val:
                float(val)  # Validate it's a number
                value_entry.delete(0, END)
                value_entry.insert(0, val)
            else:
                value_entry.delete(0, END)
                value_entry.insert(0, "0")
            modal.destroy()
        except ValueError:
            tkinter.messagebox.showerror("Invalid Input", "Please enter a valid number")
            entry_widget.focus()
    
    def cancel_dialog():
        # Reset to previous selection if cancelled
        modal.destroy()
        stop_loss_combo.current(previous_index)
    
    Button(button_frame, text="OK", width=8, command=save_value).pack(side=LEFT, padx=5)
    Button(button_frame, text="Cancel", width=8, command=cancel_dialog).pack(side=LEFT, padx=5)
    
    # Bind Enter key to save
    entry_widget.bind("<Return>", lambda e: save_value())
    entry_widget.bind("<Escape>", lambda e: cancel_dialog())
    
    # Wait for modal to close
    modal.wait_window()

def _update_stop_loss_value_field(stop_loss_combo, value_entry, reset_value=False):
    """
    Handle stop loss selection change. Shows modal for Custom option.
    """
    selection = stop_loss_combo.get()
    if selection == Config.stopLoss[-1]:  # "Custom"
        # Store the current index before showing modal (which is already "Custom")
        # We'll track the previous index inside the modal function
        _show_custom_stop_loss_modal(stop_loss_combo, value_entry)
    else:
        if reset_value:
            value_entry.delete(0, END)
            value_entry.insert(0, "0")

def getScrollableframe(frame):
    container = Frame(frame)
    canvas = Canvas(container, width=1200, height=610)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    global scrollable_frame
    scrollable_frame = ttk.Frame(canvas)
    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")
        )
    )
    canvas.create_window((-20, 1), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    container.pack()
    canvas.pack(side=LEFT, expand=True)
    scrollbar.pack(side="left", fill="y")

def NewTradeFrame(frame,connection):
    logging.info("New Trade Frame Init")
    getScrollableframe(frame)

    global IbConn
    IbConn = connection
    asyncio.ensure_future(pnl_check(IbConn))
    labelFrame = Frame(scrollable_frame)
    lblSymbol = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Symbol", justify=LEFT)
    lblSymbol.config(width=10)
    lblSymbol.pack(side=LEFT)

    timeFramelbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Time Frame", justify=LEFT)
    timeFramelbl.config(width=10)
    timeFramelbl.pack(side=LEFT)

    profitlbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Profit", justify=LEFT)
    profitlbl.config(width=10)
    profitlbl.pack(side=LEFT)

    stpLosslbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Stop Loss", justify=LEFT)
    stpLosslbl.config(width=10)
    stpLosslbl.pack(side=LEFT)

    # stpLossValuelbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="SL Value", justify=LEFT)
    # stpLossValuelbl.config(width=10)
    # stpLossValuelbl.pack(side=LEFT)

    breakEvenlbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Break Even", justify=LEFT)
    breakEvenlbl.config(width=10)
    breakEvenlbl.pack(side=LEFT)

    timeInForcelbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Time In Force", justify=LEFT)
    timeInForcelbl.config(width=10)
    timeInForcelbl.pack(side=LEFT)

    timeInForcelbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Trade Type", justify=LEFT)
    timeInForcelbl.config(width=10)
    timeInForcelbl.pack(side=LEFT)

    # Entry Point label is hidden since the field is now modal-based
    # timeInForcelbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Entry Point", justify=LEFT)
    # timeInForcelbl.config(width=10)
    # timeInForcelbl.pack(side=LEFT)

    buySelllbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Buy/Sell", justify=LEFT)
    buySelllbl.config(width=10)
    buySelllbl.pack(side=LEFT)

    risklbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Risk", justify=LEFT)
    risklbl.config(width=9)
    risklbl.pack(side=LEFT)

    # risklbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Quantity", justify=LEFT)
    # risklbl.config(width=9)
    # risklbl.pack(side=LEFT)

    statuslbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Status", justify=LEFT)
    statuslbl.config(width=9)
    statuslbl.pack(side=LEFT)

    atrlbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="ATR %", justify=LEFT)
    atrlbl.config(width=9)
    atrlbl.pack(side=LEFT)

    # statuslbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Pre/Post", justify=LEFT)
    # statuslbl.config(width=9)
    # statuslbl.pack(side=LEFT)

    statuslbl = Label(labelFrame, font=(Config.fontName2, Config.fontSize2), text="Cancel", justify=LEFT)
    statuslbl.config(width=9)
    statuslbl.pack(side=LEFT)


    labelFrame.pack()

    addOldCache()
    addField(0)

    # global defaultSetting
    # defaultSetting = Button(frame, width="18", height="1", text="Setting", command=Setting)
    # defaultSetting.place(relx=0.28, rely=0.3, anchor=CENTER)
    global addButton
    addButton = Frame(scrollable_frame)
    Button(addButton, width="15", height="1", text="ADD", command=add).pack( side = BOTTOM)
    addButton.pack( side = BOTTOM)
    # addButtonbt.place(relx=0.5, rely=0.3, anchor=CENTER)
    # global managePositionButton
    # managePositionButton = Button(frame, width="15", height="1", text="Manage Position", command=openManagePosition)
    # managePositionButton.place(relx=0.7, rely=0.3, anchor=CENTER)


def _get_current_session():
    """Detect current trading session"""
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

def checkLastTradingTime():
    """Check if trading is closed - only blocks during regular hours after trading end"""
    currentTime = datetime.datetime.now()
    session = _get_current_session()
    
    # Allow all extended hours trading (pre-market, after-hours, overnight)
    if session in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT'):
        return False  # Trading is allowed in extended hours
    
    # For regular hours, check if it's after trading end time
    configTime = datetime.datetime.strptime(Config.tradingEnd, "%H:%M:%S")
    lastTime = datetime.datetime.combine(datetime.datetime.now().date(), configTime.time())
    print("currentTime ",currentTime, "session:", session)
    if currentTime.time() > lastTime.time():
        return True  # Trading closed after regular hours end
    else:
        return False  # Trading is open

def add():
    print("new row adding.")
    global rowPosition
    global buttonRely
    global status
    if checkLastTradingTime():
        tkinter.messagebox.showinfo('Error', "We can not place entry trade. Trading closed...")
        return

    # Auto-detect outsideRth based on current session
    session = _get_current_session()
    outsideRth = session in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT')
    logging.info(f"Auto-detected session: {session}, outsideRth: {outsideRth}")

    status[len(status) - 1].delete(0, END)
    status[len(status) - 1].insert(0, "Execute")

    # Log latest bar high/low whenever Add is clicked (premarket/after-hours audit)
    try:
        current_symbol = symbol[len(symbol) - 1].get()
        current_timeframe = timeFrame[len(timeFrame) - 1].get()
        contract = getContract(current_symbol, None)
        hist_bar = _get_latest_hist_bar(IbConn, contract, current_timeframe)
        if hist_bar:
            logging.info(
                "Add button snapshot -> symbol=%s timeframe=%s high=%s low=%s",
                current_symbol,
                current_timeframe,
                hist_bar.get("high"),
                hist_bar.get("low")
            )
        else:
            logging.warning(
                "Add button snapshot -> unable to fetch bar for %s %s",
                current_symbol,
                current_timeframe
            )
    except Exception as snapshot_err:
        logging.error("Add button snapshot error: %s", snapshot_err)

    loop = asyncio.get_event_loop()
    current_sl_value = "0"
    if len(stopLossValue) > 0:
        current_sl_value = stopLossValue[len(stopLossValue) - 1].get()
        if current_sl_value == "":
            current_sl_value = "0"
    x = asyncio.ensure_future(SendTrade(IbConn,symbol[len(symbol) - 1].get(),timeFrame[len(timeFrame) - 1].get(),takeProfit[len(takeProfit) - 1].get(),
              stopLoss[len(stopLoss) - 1].get(),risk[len(risk) - 1].get(),timeInForce[len(timeInForce) - 1].get(),tradeType[len(tradeType) - 1].get(),
                                    buySell[len(buySell) - 1].get(),atr[len(atr) - 1].get(),0,Config.pullBackNo , current_sl_value,
                                        breakEven[len(breakEven) - 1].get() , outsideRth,entry_points[len(entry_points) - 1].get()  ))

    but = cancelButton[len(cancelButton) - 1]
    but['command'] = lambda arg1=x,arg2=but,arg3=status[len(status) - 1]: cancelEntryTrade(arg1,arg2,arg3)

    disableEntryState()

    # rowPosition = rowPosition + 30
    # buttonRely = buttonRely + 0.07
    # addButton.place(relx=0.5, rely=buttonRely, anchor=CENTER)
    addField(0)


def addOldCache():
    for key in Config.orderStatusData:
        value = Config.orderStatusData.get(key)
        if value.get("ordType") == "Entry":
            addField(0)
            symbol[len(symbol) - 1].delete(0, END)
            symbol[len(symbol) - 1].insert(0, value.get("usersymbol"))

            timeFrame[len(timeFrame) - 1].current(Config.timeFrame.index(value.get("timeFrame")))
            takeProfit[len(takeProfit) - 1].current(Config.takeProfit.index(value.get("profit")))
            stopLoss[len(stopLoss) - 1].current(Config.stopLoss.index(value.get("stopLoss")))
            if len(stopLossValue) > 0:
                stopLossValue[len(stopLossValue) - 1].config(state="normal")
                stopLossValue[len(stopLossValue) - 1].delete(0, END)
                if value.get("slValue")==None:
                    stopLossValue[len(stopLossValue) - 1].insert(0,"0")
                else:
                    stopLossValue[len(stopLossValue) - 1].insert(0, value.get("slValue"))
                _update_stop_loss_value_field(stopLoss[len(stopLoss) - 1], stopLossValue[len(stopLossValue) - 1], reset_value=False)
            if value.get("entry_points")==None:
                entry_points[len(entry_points) - 1].insert(0,"0")
            else:
                entry_points[len(entry_points) - 1].insert(0, value.get("entry_points"))

            breakEven[len(breakEven) - 1].insert(0, value.get("breakEven"))
            timeInForce[len(timeInForce) - 1].current(Config.timeInForce.index(value.get("tif")))


            risk[len(risk) - 1].delete(0, END)
            risk[len(risk) - 1].insert(0, value.get("risk"))
            # if value.get("outsideRTH") == None:
            #     outsideRTH[len(outsideRTH) - 1].insert(0, False)
            # else:
            #     outsideRTH[len(outsideRTH) - 1].insert(0, value.get("outsideRTH"))

            status[len(status) - 1].delete(0, END)
            status[len(status) - 1].insert(0, "Execute")

            disableEntryState()




def addField(rowYPosition):
    logging.info("New Row Adding..")
    field = Frame(scrollable_frame)
    field.config(bg='#DCDCDC')
    firstEntry = Entry(field, width="10", textvariable=StringVar(field))

    firstEntry.config(width=10)
    firstEntry.pack(side=LEFT,padx=9)
    setDefaultSymbol(firstEntry)
    symbol.append(firstEntry)

    secEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.timeFrame)
    secEntry.config(width=10)
    secEntry.pack(side=LEFT,padx=9)
    secEntry.current(0)
    setDefaultTimeFrame(secEntry)
    timeFrame.append(secEntry)

    profitEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.takeProfit)
    profitEntry.config(width=10)
    profitEntry.pack(side=LEFT,padx=9)
    profitEntry.current(0)
    setDefaultProfit(profitEntry)
    takeProfit.append(profitEntry)

    stpLossEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.stopLoss)
    stpLossEntry.config(width=10)
    stpLossEntry.pack(side=LEFT,padx=9)
    stpLossEntry.current(0)
    setDefaultStp(stpLossEntry)
    stopLoss.append(stpLossEntry)

    # Hidden entry field to store custom stop loss value (not displayed in UI)
    stopLossValueEntry = Entry(field, width="0", textvariable=StringVar(field))
    stopLossValueEntry.config(width=0)
    stopLossValueEntry.pack(side=LEFT, padx=0)
    stopLossValueEntry.pack_forget()  # Hide it completely
    setDefaultstopLossValue(stopLossValueEntry)
    stopLossValue.append(stopLossValueEntry)
    # Store previous index tracking (use a list to allow modification in closure)
    previous_index_storage = [stpLossEntry.current()]
    
    # Ensure entry reflects the initial selection
    _update_stop_loss_value_field(stpLossEntry, stopLossValueEntry, reset_value=False)
    
    def on_stop_loss_change(event):
        combo = stpLossEntry
        value_entry = stopLossValueEntry
        current_selection = combo.get()
        # If selecting Custom, store the previous index
        if current_selection == Config.stopLoss[-1]:
            # The previous index is what was stored before this change
            combo._previous_index = previous_index_storage[0]
        else:
            # Update stored previous index for next time
            previous_index_storage[0] = combo.current()
        _update_stop_loss_value_field(combo, value_entry, reset_value=True)
    
    stpLossEntry.bind("<<ComboboxSelected>>", on_stop_loss_change)

    breakEvenEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.breakEven)
    breakEvenEntry.config(width=10)
    breakEvenEntry.pack(side=LEFT, padx=9)
    breakEvenEntry.current(0)
    setDefaultbreakEvenEntryType(breakEvenEntry)
    breakEven.append(breakEvenEntry)


    timeForceEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.timeInForce)
    timeForceEntry.config(width=10)
    timeForceEntry.pack(side=LEFT,padx=9)
    timeForceEntry.current(0)
    setDefaultTif(timeForceEntry)
    timeInForce.append(timeForceEntry)


    tradeTypeEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.entryTradeType)
    tradeTypeEntry.config(width=10)
    tradeTypeEntry.pack(side=LEFT,padx=9)
    tradeTypeEntry.current(0)
    setDefaultEntryType(tradeTypeEntry)
    tradeType.append(tradeTypeEntry)

    # Hidden entry field to store entry price value (not displayed in UI)
    entry_pointValueEntry = Entry(field, width="0", textvariable=StringVar(field))
    entry_pointValueEntry.config(width=0)
    entry_pointValueEntry.pack(side=LEFT, padx=0)
    entry_pointValueEntry.pack_forget()  # Hide it completely
    setDefaultEntryPointValue(entry_pointValueEntry)
    entry_points.append(entry_pointValueEntry)
    
    # Store previous index tracking for trade type (use a list to allow modification in closure)
    previous_trade_type_index = [tradeTypeEntry.current()]
    
    def on_trade_type_change(event):
        combo = tradeTypeEntry
        entry_points_entry = entry_pointValueEntry
        current_selection = combo.get()
        # If selecting Limit Order or Stop Order, show modal
        if current_selection in Config.manualOrderTypes:
            # The previous index is what was stored before this change
            combo._previous_index = previous_trade_type_index[0]
            order_type_name = current_selection
            _show_entry_price_modal(combo, entry_points_entry, order_type_name)
        else:
            # Update stored previous index for next time
            previous_trade_type_index[0] = combo.current()
            # Reset entry points for non-manual order types
            entry_points_entry.delete(0, END)
            entry_points_entry.insert(0, "0")
    
    tradeTypeEntry.bind("<<ComboboxSelected>>", on_trade_type_change)

    buysellEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.buySell)
    buysellEntry.config(width=10)
    buysellEntry.pack(side=LEFT,padx=9)
    buysellEntry.current(0)
    setDefaultBuySell(buysellEntry)
    buySell.append(buysellEntry)



    riskEntry = Entry(field, width="10", textvariable=StringVar(field))
    riskEntry.config(width=10)
    riskEntry.pack(side=LEFT,padx=9)
    setDefaultRisk(riskEntry)
    risk.append(riskEntry)

    # quantityEntry = Entry(field, width="10", textvariable=StringVar(field))
    # quantityEntry.config(width=10)
    # quantityEntry.pack(side=LEFT, padx=9)
    # setDefaultQuantity(quantityEntry)
    # quantity.append(quantityEntry)


    statusVar = StringVar(field)
    statusEntry = Entry(field, width="9", textvariable=statusVar)
    statusEntry.config(width=9)
    statusEntry.pack(side=LEFT,padx=9)
    status.append(statusEntry)

    atrVar = StringVar(field,Config.atrValue)
    atrEntry = Entry(field, width="9", textvariable=atrVar)
    atrEntry.config(width=9)
    setDefaultAtr(atrEntry)
    atrEntry.pack(side=LEFT,padx=9)
    atr.append(atrEntry)

    # outsideRTHEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.prePostBool)
    # outsideRTHEntry.config(width=10)
    # outsideRTHEntry.pack(side=LEFT, padx=9)
    # outsideRTHEntry.current(0)
    # setDefaultoutsideRTH(outsideRTHEntry)
    # outsideRTH.append(outsideRTHEntry)

    butCancle = Frame(field)
    but = Button(field, width="10", height="1", text="Cancel")
    but['command'] = lambda arg1=None, arg2=None, arg3=None: cancelEntryTrade(arg1, arg2, arg3)
    but.pack(side=LEFT, padx=9)
    cancelButton.append(but)


    field.pack(side=TOP,pady=8)



def cancelEntryTrade(asyncObj,butObj,statusObj):
    print(asyncObj)
    if asyncObj == None:
        tkinter.messagebox.showinfo('Success', "All Trade Successfully Executed/Canceled")
    else:
        asyncObj.cancel()
        butObj['command'] = lambda arg1=None,arg2=None: cancelEntryTrade(arg1,arg2)
        statusObj.config(state="normal")
        statusObj.delete(0, END)
        statusObj.insert(0, "Canceled")
        statusObj.config(state="disabled")
        tkinter.messagebox.showinfo('Success', "Successfully Canceled")

def disableEntryState():
    symbol[len(symbol) - 1].config(state="disabled")
    timeFrame[len(timeFrame) - 1].config(state="disabled")
    takeProfit[len(takeProfit) - 1].config(state="disabled")
    stopLoss[len(stopLoss) - 1].config(state="disabled")
    stopLossValue[len(stopLossValue) - 1].config(state="disabled")
    entry_points[len(entry_points) - 1].config(state="disabled")
    tradeType[len(tradeType) - 1].config(state="disabled")
    timeInForce[len(timeInForce) - 1].config(state="disabled")
    risk[len(risk) - 1].config(state="disabled")
    status[len(status) - 1].config(state="disabled")
    buySell[len(buySell) - 1].config(state="disabled")
    atr[len(atr) - 1].config(state="disabled")
    # outsideRTH[len(outsideRTH) - 1].config(state="disabled")
    # quantity[len(quantity) - 1].config(state="disabled")



# def setDefaultQuantity(quantity):
#     if Config.defaultValue.get("quantity") != None:
#         quantity.delete(0, END)
#         quantity.insert(0, Config.defaultValue.get("quantity"))


def setDefaultSymbol(symbol):
    if Config.defaultValue.get("symbol") != None:
        symbol.delete(0, END)
        symbol.insert(0, Config.defaultValue.get("symbol"))

def setDefaultTimeFrame(timeFrame):
    if Config.defaultValue.get("timeFrame") != None:
        timeFrame.current(Config.timeFrame.index(Config.defaultValue.get("timeFrame")))

def setDefaultProfit(profit):
    if Config.defaultValue.get("profit") != None:
        profit.current(Config.takeProfit.index(Config.defaultValue.get("profit")))

def setDefaultStp(stpLoss):
    if Config.defaultValue.get("stpLoss") != None:
        stpLoss.current(Config.stopLoss.index(Config.defaultValue.get("stpLoss")))
def setDefaultTif(tif):
    if Config.defaultValue.get("tif") != None:
        tif.current(Config.timeInForce.index(Config.defaultValue.get("tif")))

# def setDefaultoutsideRTH(outside):
#     if Config.defaultValue.get("outsideRTH") != None:
#         if Config.defaultValue.get("outsideRTH") == 'True':
#             outside.current(True)
#         else:
#             outside.current(False)

def setDefaultBuySell(buySellType):
    if Config.defaultValue.get("buySellType") != None:
        buySellType.current(Config.buySell.index(Config.defaultValue.get("buySellType")))

def setDefaultEntryType(entryType):
    if Config.defaultValue.get("entryType") != None:
        entryType.current(Config.entryTradeType.index(Config.defaultValue.get("entryType")))

def setDefaultRisk(risk):
    if Config.defaultValue.get("risk") != None:
        risk.delete(0, END)
        risk.insert(0, Config.defaultValue.get("risk"))

def setDefaultbreakEvenEntryType(breakEvenEntry):
    if Config.defaultValue.get("breakEven") != None:
        if Config.defaultValue.get("breakEven") == 'False':
            breakEvenEntry.current(False)
        else:
            breakEvenEntry.current(True)

def setDefaultEntryPointValue(entry_point):
    if Config.defaultValue.get("entry_points") == None:
        Config.defaultValue.update({"entry_points":"0"})
    if Config.defaultValue.get("entry_points") != None:
        entry_point.delete(0, END)
        entry_point.insert(0, Config.defaultValue.get("entry_points"))

def setDefaultstopLossValue(stopLossVal):
    if Config.defaultValue.get("stopLossValue") == None:
        Config.defaultValue.update({"stopLossValue":"0"})
    if Config.defaultValue.get("stopLossValue") != None:
        stopLossVal.delete(0, END)
        stopLossVal.insert(0, Config.defaultValue.get("stopLossValue"))
    # else:
        # stopLossValue.delete(0, END)
        # stopLossValue.insert(0, "0")

def setDefaultAtr(atr):
    if Config.defaultValue.get("atr") != None:
        atr.delete(0, END)
        atr.insert(0, Config.defaultValue.get("atr"))



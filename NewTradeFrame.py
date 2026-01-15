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
row_async_tasks = []
replayEnabled = []  # Track replay state for each row
replayButtonList = []  # Track replay buttons for each row

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

def _show_conditional_order_modal(trade_type_combo, entry_points_entry, order_type_name):
    """
    Show a modal dialog to enter conditional order parameters.
    Has two columns: Conditional Order1 and Conditional Order2.
    """
    # Get parent window from the combobox
    parent = trade_type_combo.winfo_toplevel()
    
    # Create modal dialog
    modal = tkinter.Toplevel(parent)
    modal.title(f"{order_type_name} - Conditional Order Setup")
    modal.geometry("800x500")
    modal.resizable(False, False)
    modal.transient(parent)  # Make it modal relative to parent
    modal.grab_set()  # Make it modal
    
    # Center the dialog
    modal.update_idletasks()
    x = (modal.winfo_screenwidth() // 2) - (800 // 2)
    y = (modal.winfo_screenheight() // 2) - (500 // 2)
    modal.geometry(f"800x500+{x}+{y}")
    
    # Store previous selection index
    previous_index = 0  # Default to first option
    if hasattr(trade_type_combo, '_previous_index'):
        previous_index = trade_type_combo._previous_index
    
    # Get current value if any (comma-separated: selected_order,co1_stop,co1_condition,co1_price,co2_stop,co2_cond1,co2_price1,co2_cond2,co2_price2)
    current_value = entry_points_entry.get() if entry_points_entry.get() else "0,0,Above,0,0,Above,0,Above,0"
    values = current_value.split(",")
    if len(values) < 9:
        values = ["0", "0", "Above", "0", "0", "Above", "0", "Above", "0"]
    
    # Parse values: [0]=selected_order, [1]=co1_stop, [2]=co1_condition, [3]=co1_price, [4]=co2_stop, [5]=co2_cond1, [6]=co2_price1, [7]=co2_cond2, [8]=co2_price2
    selected_order = values[0] if len(values) > 0 else "0"
    co1_values = values[1:4] if len(values) > 3 else ["0", "Above", "0"]
    co2_values = values[4:9] if len(values) > 8 else ["0", "Above", "0", "Above", "0"]
    
    # Main container frame - split in half
    main_frame = Frame(modal)
    main_frame.pack(fill=BOTH, expand=True, padx=10, pady=10)
    
    # Shared variables for mutually exclusive checkboxes (use IntVar for proper check mark display)
    co1_selected = IntVar(modal, value=1 if selected_order == "1" else 0)
    co2_selected = IntVar(modal, value=1 if selected_order == "2" else 0)
    
    # Rectangle 1: Conditional Order1 (Left Half)
    co1_rect = Frame(main_frame, relief=RAISED, borderwidth=2, bg='#F0F0F0')
    co1_rect.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5))
    
    # Title for Rectangle 1
    Label(co1_rect, text="Conditional Order 1", font=(Config.fontName2, Config.fontSize2, "bold"), bg='#F0F0F0').pack(pady=(10, 15))
    
    # Conditional Order1: Checkbox button
    co1_row = Frame(co1_rect, bg='#F0F0F0')
    co1_row.pack(fill=X, pady=8, padx=10)
    Label(co1_row, text="Conditional Order:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co1_checkbox = Checkbutton(co1_row, text="Conditional Order1", variable=co1_selected, 
                                font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0')
    co1_checkbox.pack(side=RIGHT)
    
    # Input Stop Order Price
    co1_stop_row = Frame(co1_rect, bg='#F0F0F0')
    co1_stop_row.pack(fill=X, pady=8, padx=10)
    Label(co1_stop_row, text="Input Stop Order Price:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co1_stop_price_var = StringVar(modal, value=co1_values[0] if len(co1_values) > 0 else "0")
    co1_stop_price_entry = Entry(co1_stop_row, textvariable=co1_stop_price_var, width=18, font=(Config.fontName2, Config.fontSize2))
    co1_stop_price_entry.pack(side=RIGHT)
    
    # Input Condition
    co1_cond_row = Frame(co1_rect, bg='#F0F0F0')
    co1_cond_row.pack(fill=X, pady=8, padx=10)
    Label(co1_cond_row, text="Input Condition:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co1_condition_var = StringVar(modal)
    co1_condition_combo = ttk.Combobox(co1_cond_row, textvariable=co1_condition_var, state="readonly", width=20, values=["Above", "Below"])
    co1_condition_combo.set(co1_values[1] if len(co1_values) > 1 and co1_values[1] in ["Above", "Below"] else "Above")
    co1_condition_combo.pack(side=RIGHT)
    
    # Input Price
    co1_price_row = Frame(co1_rect, bg='#F0F0F0')
    co1_price_row.pack(fill=X, pady=8, padx=10)
    Label(co1_price_row, text="Input Price:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co1_price_var = StringVar(modal, value=co1_values[2] if len(co1_values) > 2 else "0")
    co1_price_entry = Entry(co1_price_row, textvariable=co1_price_var, width=18, font=(Config.fontName2, Config.fontSize2))
    co1_price_entry.pack(side=RIGHT)
    
    # Rectangle 2: Conditional Order2 (Right Half)
    co2_rect = Frame(main_frame, relief=RAISED, borderwidth=2, bg='#F0F0F0')
    co2_rect.pack(side=LEFT, fill=BOTH, expand=True, padx=(5, 0))
    
    # Title for Rectangle 2
    Label(co2_rect, text="Conditional Order 2", font=(Config.fontName2, Config.fontSize2, "bold"), bg='#F0F0F0').pack(pady=(10, 15))
    
    # Conditional Order2: Checkbox button
    co2_row = Frame(co2_rect, bg='#F0F0F0')
    co2_row.pack(fill=X, pady=8, padx=10)
    Label(co2_row, text="Conditional Order:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co2_checkbox = Checkbutton(co2_row, text="Conditional Order2", variable=co2_selected, 
                                font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0')
    co2_checkbox.pack(side=RIGHT)
    
    # Input Stop Order Price
    co2_stop_row = Frame(co2_rect, bg='#F0F0F0')
    co2_stop_row.pack(fill=X, pady=8, padx=10)
    Label(co2_stop_row, text="Input Stop Order Price:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co2_stop_price_var = StringVar(modal, value=co2_values[0] if len(co2_values) > 0 else "0")
    co2_stop_price_entry = Entry(co2_stop_row, textvariable=co2_stop_price_var, width=18, font=(Config.fontName2, Config.fontSize2))
    co2_stop_price_entry.pack(side=RIGHT)
    
    # Input Condition 1
    co2_cond1_row = Frame(co2_rect, bg='#F0F0F0')
    co2_cond1_row.pack(fill=X, pady=8, padx=10)
    Label(co2_cond1_row, text="Input Condition 1:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co2_condition1_var = StringVar(modal)
    co2_condition1_combo = ttk.Combobox(co2_cond1_row, textvariable=co2_condition1_var, state="readonly", width=20, values=["Above", "Below"])
    co2_condition1_combo.set(co2_values[1] if len(co2_values) > 1 and co2_values[1] in ["Above", "Below"] else "Above")
    co2_condition1_combo.pack(side=RIGHT)
    
    # Input Price (for Condition 1)
    co2_price1_row = Frame(co2_rect, bg='#F0F0F0')
    co2_price1_row.pack(fill=X, pady=8, padx=10)
    Label(co2_price1_row, text="Input Price:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co2_price1_var = StringVar(modal, value=co2_values[2] if len(co2_values) > 2 else "0")
    co2_price1_entry = Entry(co2_price1_row, textvariable=co2_price1_var, width=18, font=(Config.fontName2, Config.fontSize2))
    co2_price1_entry.pack(side=RIGHT)
    
    # Input Condition 2
    co2_cond2_row = Frame(co2_rect, bg='#F0F0F0')
    co2_cond2_row.pack(fill=X, pady=8, padx=10)
    Label(co2_cond2_row, text="Input Condition 2:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co2_condition2_var = StringVar(modal)
    co2_condition2_combo = ttk.Combobox(co2_cond2_row, textvariable=co2_condition2_var, state="readonly", width=20, values=["Above", "Below"])
    co2_condition2_combo.set(co2_values[3] if len(co2_values) > 3 and co2_values[3] in ["Above", "Below"] else "Above")
    co2_condition2_combo.pack(side=RIGHT)
    
    # Input Price (for Condition 2)
    co2_price2_row = Frame(co2_rect, bg='#F0F0F0')
    co2_price2_row.pack(fill=X, pady=8, padx=10)
    Label(co2_price2_row, text="Input Price:", font=(Config.fontName2, Config.fontSize2), bg='#F0F0F0', width=20, anchor='w').pack(side=LEFT)
    co2_price2_var = StringVar(modal, value=co2_values[4] if len(co2_values) > 4 else "0")
    co2_price2_entry = Entry(co2_price2_row, textvariable=co2_price2_var, width=18, font=(Config.fontName2, Config.fontSize2))
    co2_price2_entry.pack(side=RIGHT)
    
    # Make checkboxes mutually exclusive
    def on_co1_check():
        if co1_selected.get() == 1:
            co2_selected.set(0)
    
    def on_co2_check():
        if co2_selected.get() == 1:
            co1_selected.set(0)
    
    co1_checkbox.config(command=on_co1_check)
    co2_checkbox.config(command=on_co2_check)
    
    # Buttons frame at bottom
    button_frame = Frame(modal)
    button_frame.pack(pady=10)
    
    def save_value():
        try:
            # Validate all numeric inputs
            float(co1_stop_price_var.get().strip() or "0")
            float(co1_price_var.get().strip() or "0")
            float(co2_stop_price_var.get().strip() or "0")
            float(co2_price1_var.get().strip() or "0")
            float(co2_price2_var.get().strip() or "0")
            
            # Store values in entry_points_entry (as JSON string or comma-separated)
            # Format: selected_order,co1_stop,co1_condition,co1_price,co2_stop,co2_cond1,co2_price1,co2_cond2,co2_price2
            # selected_order: "1" for CO1, "2" for CO2, "0" for none
            selected_order = "1" if co1_selected.get() == 1 else ("2" if co2_selected.get() == 1 else "0")
            values = [
                selected_order,
                co1_stop_price_var.get().strip() or "0",
                co1_condition_var.get() or "Above",
                co1_price_var.get().strip() or "0",
                co2_stop_price_var.get().strip() or "0",
                co2_condition1_var.get() or "Above",
                co2_price1_var.get().strip() or "0",
                co2_condition2_var.get() or "Above",
                co2_price2_var.get().strip() or "0"
            ]
            entry_points_entry.delete(0, END)
            entry_points_entry.insert(0, ",".join(values))
            modal.destroy()
        except ValueError:
            tkinter.messagebox.showerror("Invalid Input", "Please enter valid numbers for all price fields")
            co1_stop_price_entry.focus()
    
    def cancel_dialog():
        # Reset to previous selection if cancelled
        modal.destroy()
        trade_type_combo.current(previous_index)
    
    Button(button_frame, text="OK", width=8, command=save_value).pack(side=LEFT, padx=5)
    Button(button_frame, text="Cancel", width=8, command=cancel_dialog).pack(side=LEFT, padx=5)
    
    # Focus on first entry field
    co1_stop_price_entry.focus()
    
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
    if selection == "Custom":  # "Custom"
        # Store the current index before showing modal (which is already "Custom")
        # We'll track the previous index inside the modal function
        _show_custom_stop_loss_modal(stop_loss_combo, value_entry)
    else:
        if reset_value:
            value_entry.delete(0, END)
            value_entry.insert(0, "0")

def getScrollableframe(frame):
    container = Frame(frame)
    container.pack(fill=BOTH, expand=True)

    canvas = Canvas(container, highlightthickness=0)
    canvas.pack(side=LEFT, fill=BOTH, expand=True)

    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    scrollbar.pack(side=RIGHT, fill=Y)

    canvas.configure(yscrollcommand=scrollbar.set)

    global scrollable_frame
    scrollable_frame = ttk.Frame(canvas)

    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas_frame = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

    def _resize_canvas(event):
        canvas.itemconfig(canvas_frame, width=event.width)

    canvas.bind("<Configure>", _resize_canvas)

def NewTradeFrame(frame,connection):
    logging.info("New Trade Frame Init")
    getScrollableframe(frame)

    global IbConn
    IbConn = connection
    asyncio.ensure_future(pnl_check(IbConn))

    addOldCache()
    addField(0, "")

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
    addField(0, "")


def _log_snapshot_for_row(row_index):
    try:
        current_symbol = symbol[row_index].get()
        current_timeframe = timeFrame[row_index].get()
        contract = getContract(current_symbol, None)
        hist_bar = _get_latest_hist_bar(IbConn, contract, current_timeframe)
        if hist_bar:
            logging.info(
                "Execute snapshot -> symbol=%s timeframe=%s high=%s low=%s",
                current_symbol,
                current_timeframe,
                hist_bar.get("high"),
                hist_bar.get("low")
            )
        else:
            logging.warning(
                "Execute snapshot -> unable to fetch bar for %s %s",
                current_symbol,
                current_timeframe
            )
    except Exception as snapshot_err:
        logging.error("Execute snapshot error: %s", snapshot_err)


def _set_status(row_index, text):
    status_entry = status[row_index]
    status_entry.config(state="normal")
    status_entry.delete(0, END)
    status_entry.insert(0, text)
    status_entry.config(state="disabled")


def execute_row(row_index):
    if checkLastTradingTime():
        tkinter.messagebox.showinfo('Error', "We can not place entry trade. Trading closed...")
        return

    session = _get_current_session()
    outsideRth = session in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT')
    logging.info("Execute row %s detected session: %s, outsideRth: %s", row_index, session, outsideRth)

    current_tif = timeInForce[row_index].get()
    if outsideRth and current_tif != 'OTH':
        tkinter.messagebox.showerror(
            'Error',
            f"Orders outside trading hours (premarket/after-hours) require 'OTH' (Outside Trading Hours) in Time In Force.\n"
            f"Current session: {session}\n"
            f"Please select 'OTH' in Time In Force and try again.")
        logging.warning(
            f"Order rejected for row {row_index}: outsideRth={outsideRth}, session={session}, "
            f"but Time In Force is '{current_tif}' instead of 'OTH'")
        return

    _set_status(row_index, "Sent")
    _log_snapshot_for_row(row_index)

    current_sl_value = "0"
    if len(stopLossValue) > row_index:
        current_sl_value = stopLossValue[row_index].get()
        if current_sl_value == "":
            current_sl_value = "0"

    # Get replay state for this row
    is_replay_enabled = False
    if row_index < len(replayEnabled):
        is_replay_enabled = replayEnabled[row_index]

    # Store replay state for this trade (will be retrieved in StatusUpdate)
    trade_key = (symbol[row_index].get(), timeFrame[row_index].get(), tradeType[row_index].get(), 
                 buySell[row_index].get(), datetime.datetime.now().timestamp())
    Config.order_replay_pending[trade_key] = is_replay_enabled
    logging.info("Stored replay state for trade: key=%s, replay=%s", trade_key, is_replay_enabled)

    send_future = asyncio.ensure_future(
        SendTrade(
            IbConn,
            symbol[row_index].get(),
            timeFrame[row_index].get(),
            takeProfit[row_index].get(),
            stopLoss[row_index].get(),
            risk[row_index].get(),
            timeInForce[row_index].get(),
            tradeType[row_index].get(),
            buySell[row_index].get(),
            atr[row_index].get(),
            0,
            Config.pullBackNo,
            current_sl_value,
            breakEven[row_index].get(),
            outsideRth,
            entry_points[row_index].get(),
        )
    )

    row_async_tasks[row_index] = send_future
    disableEntryState(row_index)

    button = cancelButton[row_index]
    button.config(text="Cancel")
    button['command'] = lambda idx=row_index: cancel_row(idx)
    # Keep button enabled so user can cancel if needed


def toggle_replay(row_index):
    """Toggle replay mode for a row"""
    if row_index < len(replayEnabled):
        replayEnabled[row_index] = not replayEnabled[row_index]
        # Update button appearance
        if row_index < len(replayButtonList):
            replayButton = replayButtonList[row_index]
            if replayEnabled[row_index]:
                replayButton.config(bg='#90EE90')  # Light green when enabled
                logging.info("Replay enabled for row %s", row_index)
            else:
                replayButton.config(bg='#D3D3D3')  # Light gray when disabled
                logging.info("Replay disabled for row %s", row_index)


def cancel_row(row_index):
    async_task = row_async_tasks[row_index]
    butObj = cancelButton[row_index]
    statusObj = status[row_index]
    cancelEntryTrade(async_task, butObj, statusObj)
    row_async_tasks[row_index] = None
    _set_status(row_index, "Canceled")
    butObj.config(state="disabled")
    # Disable replay when canceling
    if row_index < len(replayEnabled):
        replayEnabled[row_index] = False
        if row_index < len(replayButtonList):
            replayButtonList[row_index].config(bg='#D3D3D3')


def addOldCache():
    for key in Config.orderStatusData:
        value = Config.orderStatusData.get(key)
        if value.get("ordType") == "Entry":
            addField(0, "Sent")
            row_idx = len(symbol) - 1
            symbol[row_idx].delete(0, END)
            symbol[row_idx].insert(0, value.get("usersymbol"))

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

            # Set trade type if available
            if value.get("barType") is not None and value.get("barType") in Config.entryTradeType:
                tradeType[len(tradeType) - 1].current(Config.entryTradeType.index(value.get("barType")))
                # If it's a pull back type, disable stop loss
                pull_back_types = ['PBe1', 'PBe2']
                if value.get("barType") in pull_back_types:
                    stopLoss[len(stopLoss) - 1].config(state="disabled")
                    stopLossValue[len(stopLossValue) - 1].config(state="disabled")

            risk[len(risk) - 1].delete(0, END)
            risk[len(risk) - 1].insert(0, value.get("risk"))
            # if value.get("outsideRTH") == None:
            #     outsideRTH[len(outsideRTH) - 1].insert(0, False)
            # else:
            #     outsideRTH[len(outsideRTH) - 1].insert(0, value.get("outsideRTH"))

            row_idx = len(status) - 1
            disableEntryState(row_idx)
            cancelButton[row_idx].config(text="Cancel")
            cancelButton[row_idx]['command'] = lambda idx=row_idx: cancel_row(idx)
            # Initialize replay state for cached rows
            if len(replayEnabled) <= row_idx:
                replayEnabled.append(False)
                # Create a dummy replay button for cached rows (won't be visible but prevents index errors)
                if len(replayButtonList) <= row_idx:
                    replayButtonList.append(None)




def addField(rowYPosition, initial_status_text=""):
    logging.info("New Row Adding..")
    field = Frame(scrollable_frame)
    field.config(bg='#DCDCDC')
    # Configure 12 columns for the new order and 2 rows (labels + fields)
    for col in range(12):
        field.columnconfigure(col, weight=1, uniform="row")
    for row in range(2):
        field.rowconfigure(row, weight=1)
    
    # Labels row (row 0)
    # 1) SYMBOL label
    symbolLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Symbol", justify=LEFT)
    symbolLbl.grid(row=0, column=0, sticky="ew", padx=5, pady=3)
    
    # 2) STOP LOSS label
    stopLossLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Stop Loss", justify=LEFT)
    stopLossLbl.grid(row=0, column=1, sticky="ew", padx=5, pady=3)
    
    # 3) TRADE TYPE label
    tradeTypeLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Trade Type", justify=LEFT)
    tradeTypeLbl.grid(row=0, column=2, sticky="ew", padx=5, pady=3)
    
    # 4) BUY / SELL label
    buySellLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Buy/Sell", justify=LEFT)
    buySellLbl.grid(row=0, column=3, sticky="ew", padx=5, pady=3)
    
    # 5) EXECUTE label (button text is already "Execute", but adding label for consistency)
    executeLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Execute", justify=LEFT)
    executeLbl.grid(row=0, column=4, sticky="ew", padx=5, pady=3)
    
    # 6) RISK label
    riskLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Risk", justify=LEFT)
    riskLbl.grid(row=0, column=5, sticky="ew", padx=5, pady=3)
    
    # 7) PROFIT label
    profitLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Profit", justify=LEFT)
    profitLbl.grid(row=0, column=6, sticky="ew", padx=5, pady=3)
    
    # 8) TIME FRAME label
    timeFrameLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Time Frame", justify=LEFT)
    timeFrameLbl.grid(row=0, column=7, sticky="ew", padx=5, pady=3)
    
    # 9) TIME IN FORCE label
    timeInForceLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Time In Force", justify=LEFT)
    timeInForceLbl.grid(row=0, column=8, sticky="ew", padx=5, pady=3)
    
    # 10) REPLAY label
    replayLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Replay", justify=LEFT)
    replayLbl.grid(row=0, column=9, sticky="ew", padx=5, pady=3)
    
    # 11) BREAK EVEN label
    breakEvenLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Break Even", justify=LEFT)
    breakEvenLbl.grid(row=0, column=10, sticky="ew", padx=5, pady=3)
    
    # 12) STATUS label
    statusLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Status", justify=LEFT)
    statusLbl.grid(row=0, column=11, sticky="ew", padx=5, pady=3)
    
    # Fields row (row 1)
    # 1) SYMBOL (column 0)
    firstEntry = Entry(field, width="10", textvariable=StringVar(field))
    firstEntry.config(width=10)
    firstEntry.grid(row=1, column=0, sticky="ew", padx=5, pady=3)
    setDefaultSymbol(firstEntry)
    symbol.append(firstEntry)

    # 2) STOP LOSS (column 1)
    stpLossEntry = ttk.Combobox(field, state="readonly", width="15", value=Config.stopLoss)
    stpLossEntry.config(width=15)
    stpLossEntry.grid(row=1, column=1, sticky="ew", padx=5, pady=3)
    stpLossEntry.current(0)
    setDefaultStp(stpLossEntry)
    stopLoss.append(stpLossEntry)

    # Hidden entry field to store custom stop loss value (not displayed in UI)
    stopLossValueEntry = Entry(field, width="0", textvariable=StringVar(field))
    stopLossValueEntry.config(width=0)
    stopLossValueEntry.grid(row=1, column=1, padx=0, pady=0)
    stopLossValueEntry.grid_remove()  # Hide it completely
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
        if current_selection == "Custom":
            # The previous index is what was stored before this change
            combo._previous_index = previous_index_storage[0]
        else:
            # Update stored previous index for next time
            previous_index_storage[0] = combo.current()
        _update_stop_loss_value_field(combo, value_entry, reset_value=True)
    
    stpLossEntry.bind("<<ComboboxSelected>>", on_stop_loss_change)

    # 3) TRADE TYPE (column 2)
    tradeTypeEntry = ttk.Combobox(field, state="readonly", width="18", value=Config.entryTradeType)
    tradeTypeEntry.config(width=18)
    tradeTypeEntry.grid(row=1, column=2, sticky="ew", padx=5, pady=3)
    tradeTypeEntry.current(0)
    setDefaultEntryType(tradeTypeEntry)
    tradeType.append(tradeTypeEntry)

    # Hidden entry field to store entry price value (not displayed in UI)
    entry_pointValueEntry = Entry(field, width="0", textvariable=StringVar(field))
    entry_pointValueEntry.config(width=0)
    entry_pointValueEntry.grid(row=1, column=2, padx=0, pady=0)
    entry_pointValueEntry.grid_remove()  # Hide it completely
    setDefaultEntryPointValue(entry_pointValueEntry)
    entry_points.append(entry_pointValueEntry)
    
    # Store previous index tracking for trade type (use a list to allow modification in closure)
    previous_trade_type_index = [tradeTypeEntry.current()]
    
    def on_trade_type_change(event):
        combo = tradeTypeEntry
        entry_points_entry = entry_pointValueEntry
        current_selection = combo.get()
        
        # For pull back trade types (PBe1, PBe2, PBe1e2): disable/hide stop loss dropdown
        pull_back_types = ['PBe1', 'PBe2', 'PBe1e2']
        if current_selection in pull_back_types:
            # Disable stop loss dropdown for pull back types
            stpLossEntry.config(state="disabled")
            stopLossValueEntry.config(state="disabled")
        else:
            # Re-enable stop loss dropdown for other trade types
            stpLossEntry.config(state="readonly")
            stopLossValueEntry.config(state="normal")
        
        # If selecting Limit Order or Stop Order, show modal
        if current_selection in Config.manualOrderTypes:
            # The previous index is what was stored before this change
            combo._previous_index = previous_trade_type_index[0]
            order_type_name = current_selection
            _show_entry_price_modal(combo, entry_points_entry, order_type_name)
        elif current_selection == "Conditional Order":
            # The previous index is what was stored before this change
            combo._previous_index = previous_trade_type_index[0]
            order_type_name = current_selection
            _show_conditional_order_modal(combo, entry_points_entry, order_type_name)
        else:
            # Update stored previous index for next time
            previous_trade_type_index[0] = combo.current()
            # Reset entry points for non-manual order types
            entry_points_entry.delete(0, END)
            entry_points_entry.insert(0, "0")
    
    tradeTypeEntry.bind("<<ComboboxSelected>>", on_trade_type_change)

    # 4) BUY / SELL (column 3)
    buysellEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.buySell)
    buysellEntry.config(width=10)
    buysellEntry.grid(row=1, column=3, sticky="ew", padx=5, pady=3)
    buysellEntry.current(0)
    setDefaultBuySell(buysellEntry)
    buySell.append(buysellEntry)

    # 5) EXECUTE (column 4)
    row_index = len(symbol) - 1
    if len(row_async_tasks) <= row_index:
        row_async_tasks.append(None)

    but = Button(field, width="10", height="1", text="Execute")
    but['command'] = lambda idx=row_index: execute_row(idx)
    but.grid(row=1, column=4, sticky="ew", padx=5, pady=3)
    cancelButton.append(but)

    # 6) RISK (column 5)
    riskEntry = Entry(field, width="10", textvariable=StringVar(field))
    riskEntry.config(width=10)
    riskEntry.grid(row=1, column=5, sticky="ew", padx=5, pady=3)
    setDefaultRisk(riskEntry)
    risk.append(riskEntry)

    # 7) PROFIT (column 6)
    profitEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.takeProfit)
    profitEntry.config(width=10)
    profitEntry.grid(row=1, column=6, sticky="ew", padx=5, pady=3)
    profitEntry.current(0)
    setDefaultProfit(profitEntry)
    takeProfit.append(profitEntry)

    # 8) TIME FRAME (column 7)
    secEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.timeFrame)
    secEntry.config(width=10)
    secEntry.grid(row=1, column=7, sticky="ew", padx=5, pady=3)
    secEntry.current(0)
    setDefaultTimeFrame(secEntry)
    timeFrame.append(secEntry)

    # 9) TIME IN FORCE (column 8)
    timeForceEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.timeInForce)
    timeForceEntry.config(width=10)
    timeForceEntry.grid(row=1, column=8, sticky="ew", padx=5, pady=3)
    timeForceEntry.current(0)
    setDefaultTif(timeForceEntry)
    timeInForce.append(timeForceEntry)

    # 10) REPLAY (column 9)
    row_index_for_replay = len(symbol) - 1
    replayButton = Button(field, width="10", height="1", text="Replay", bg='#D3D3D3')
    replayButton.grid(row=1, column=9, sticky="ew", padx=5, pady=3)
    replayEnabled.append(False)  # Initialize replay as disabled
    replayButtonList.append(replayButton)  # Store button reference
    replayButton['command'] = lambda idx=row_index_for_replay: toggle_replay(idx)

    # 11) BREAK EVEN (column 10)
    breakEvenEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.breakEven)
    breakEvenEntry.config(width=10)
    breakEvenEntry.grid(row=1, column=10, sticky="ew", padx=5, pady=3)
    breakEvenEntry.current(0)
    setDefaultbreakEvenEntryType(breakEvenEntry)
    breakEven.append(breakEvenEntry)

    # 12) STATUS (column 11)
    statusVar = StringVar(field)
    statusEntry = Entry(field, width="9", textvariable=statusVar)
    statusEntry.config(width=9)
    statusEntry.grid(row=1, column=11, sticky="ew", padx=5, pady=3)
    status.append(statusEntry)
    _set_status(len(status) - 1, initial_status_text)

    # ATR % field removed - functionality removed
    # Create a hidden entry with default value to maintain compatibility
    atrVar = StringVar(field, "0")
    atrEntry = Entry(field, width="0")  # Hidden entry
    atr.append(atrEntry)


    field.pack(side=TOP, pady=8, fill=X, expand=True)



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

def disableEntryState(row_index=None):
    if row_index is None:
        row_index = len(symbol) - 1
    # Disable all Entry fields
    symbol[row_index].config(state="disabled")
    risk[row_index].config(state="disabled")
    status[row_index].config(state="disabled")
    # ATR field disabled - functionality removed
    # atr[row_index].config(state="disabled")
    stopLossValue[row_index].config(state="disabled")
    entry_points[row_index].config(state="disabled")
    
    # Disable all Combobox fields (use "disabled" for ttk.Combobox to prevent any interaction)
    timeFrame[row_index].config(state="disabled")
    takeProfit[row_index].config(state="disabled")
    stopLoss[row_index].config(state="disabled")
    tradeType[row_index].config(state="disabled")
    timeInForce[row_index].config(state="disabled")
    buySell[row_index].config(state="disabled")
    breakEven[row_index].config(state="disabled")
    # outsideRTH[row_index].config(state="disabled")
    # quantity[row_index].config(state="disabled")


def enableEntryState(row_index):
    symbol[row_index].config(state="normal")
    timeFrame[row_index].config(state="readonly")
    takeProfit[row_index].config(state="readonly")
    stopLoss[row_index].config(state="readonly")
    stopLossValue[row_index].config(state="normal")
    entry_points[row_index].config(state="normal")
    tradeType[row_index].config(state="readonly")
    timeInForce[row_index].config(state="readonly")
    risk[row_index].config(state="normal")
    status[row_index].config(state="normal")
    buySell[row_index].config(state="readonly")
    # ATR field disabled - functionality removed
    # atr[row_index].config(state="normal")



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
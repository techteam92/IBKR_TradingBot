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
atrEnabled = []  # Track ATR guard state for each row
atrButtonList = []  # Store ATR button reference for each row
quantity=[]
cancelButton = []
row_async_tasks = []
replayEnabled = []  # Track replay state for each row
replayButtonList = []  # Track replay buttons for each row
optionEnabled = []  # Track option trading state for each row
optionButtonList = []  # Store Option button reference for each row (for bg update)
optionContract = []  # Store option contract (strike selection code) for each row
optionExpire = []  # Store option expiration selection (weeks out or YYYYMMDD) for each row
optionEntryOrderType = []  # Store option entry order type for each row
optionStopLossOrderType = []  # Store option stop loss order type for each row
optionProfitOrderType = []  # Store option profit order type for each row
optionRiskAmount = []  # Store option risk amount ($) for each row

rowPosition=0
buttonRely=0.3
addButton = None
IbConn = None
scrollable_frame = None
scroll_canvas = None  # Canvas that contains scrollable_frame; used to scroll to bottom after Execute


def _canonical_stop_loss_type(display_text):
    """Map stop-loss combobox display to a Config.stopLoss value for SendTrade."""
    if not display_text:
        return Config.stopLoss[0]
    s = display_text.strip()
    if s.startswith("Custom"):
        return "Custom"
    if s in Config.stopLoss:
        return s
    return s


def _canonical_trade_type(display_text):
    """Map trade-type combobox display to a Config.entryTradeType value for SendTrade."""
    if not display_text:
        return Config.entryTradeType[0]
    s = display_text.strip()
    if s.startswith("Custom ("):
        return "Custom"
    if s == "Custom" or (s.startswith("Custom") and "(" not in s):
        return "Custom"
    if s.startswith("Limit Order ("):
        return "Limit Order"
    if s == "Limit Order":
        return "Limit Order"
    if s.startswith("Conditional Order"):
        return "Conditional Order"
    if s in Config.entryTradeType:
        return s
    return s


def _set_stop_loss_combo_display(stop_loss_combo, numeric_value):
    """Show Custom (price) in the stop-loss combobox; numeric value stays in hidden entry."""
    val = (numeric_value or "").strip()
    stop_loss_combo.config(state="normal")
    if not val or val == "0":
        stop_loss_combo.set("Custom")
    else:
        stop_loss_combo.set("Custom ({})".format(val))
    stop_loss_combo.config(state="readonly")


def _set_trade_type_combo_display(trade_type_combo, base_type, price_str):
    """Show Custom/Limit (price) in the trade-type combobox; base_type is canonical."""
    ps = (price_str or "").strip()
    trade_type_combo.config(state="normal")
    if base_type in ("Custom", "Limit Order") and ps and ps != "0":
        trade_type_combo.set("{} ({})".format(base_type, ps))
    elif base_type == "Conditional Order":
        trade_type_combo.set("Conditional Order")
    elif base_type in Config.entryTradeType:
        trade_type_combo.current(Config.entryTradeType.index(base_type))
    else:
        trade_type_combo.set(base_type)
    trade_type_combo.config(state="readonly")


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
            _set_trade_type_combo_display(trade_type_combo, order_type_name, entry_points_entry.get())
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

def _show_bo_modal(trade_type_combo, entry_points_entry, order_type_name):
    """Modal for BO e1 / BO e2 - collects ATR% (base range filter) and Trigger Price.

    Stored in entry_points as "<atr_pct>,<trigger_price>" (e.g. "10,200").
    LONG only trades if current price >= trigger; SHORT only if current price <= trigger.
    Trigger 0 disables the price filter.
    """
    parent = trade_type_combo.winfo_toplevel()
    modal = tkinter.Toplevel(parent)
    modal.title(f"{order_type_name} - Parameters")
    modal.geometry("380x240")
    modal.resizable(False, False)
    modal.transient(parent)
    modal.grab_set()
    modal.update_idletasks()
    x = (modal.winfo_screenwidth() // 2) - (380 // 2)
    y = (modal.winfo_screenheight() // 2) - (240 // 2)
    modal.geometry(f"380x240+{x}+{y}")

    current = entry_points_entry.get() if entry_points_entry.get() else f"{Config.bo_default_atr.rstrip('%')},0"
    parts = current.split(",")
    cur_atr = parts[0] if parts and parts[0] else Config.bo_default_atr.rstrip('%')
    cur_trig = parts[1] if len(parts) > 1 and parts[1] else "0"

    previous_index = getattr(trade_type_combo, '_previous_index', 0)

    Label(modal, text=f"{order_type_name} parameters",
          font=(Config.fontName2, Config.fontSize2, "bold")).pack(pady=(10, 6))

    f1 = Frame(modal)
    f1.pack(fill=X, padx=15, pady=4)
    Label(f1, text="ATR % (base range):", font=(Config.fontName2, Config.fontSize2),
          width=22, anchor="w").pack(side=LEFT)
    atr_var = StringVar(modal)
    pre = f"{cur_atr}%"
    atr_combo = ttk.Combobox(f1, textvariable=atr_var, state="readonly",
                              width=10, values=Config.bo_atr_options)
    atr_combo.set(pre if pre in Config.bo_atr_options else Config.bo_default_atr)
    atr_combo.pack(side=LEFT)

    f2 = Frame(modal)
    f2.pack(fill=X, padx=15, pady=4)
    Label(f2, text="Trigger Price (X):", font=(Config.fontName2, Config.fontSize2),
          width=22, anchor="w").pack(side=LEFT)
    trig_var = StringVar(modal, value=cur_trig)
    trig_entry = Entry(f2, textvariable=trig_var, width=12,
                        font=(Config.fontName2, Config.fontSize2))
    trig_entry.pack(side=LEFT)

    Label(modal,
          text="LONG: trade only if price >= trigger.\nSHORT: trade only if price <= trigger.\nTrigger = 0 disables the filter.",
          font=(Config.fontName2, max(8, Config.fontSize2 - 2)),
          fg="#555", justify=LEFT).pack(pady=(8, 4), padx=15, anchor="w")

    def save():
        try:
            atr_pct = atr_var.get().rstrip('%').strip() or "10"
            int(atr_pct)
            trig = trig_var.get().strip() or "0"
            float(trig)
            entry_points_entry.delete(0, END)
            entry_points_entry.insert(0, f"{atr_pct},{trig}")
            _set_trade_type_combo_display(trade_type_combo, order_type_name, entry_points_entry.get())
            modal.destroy()
        except ValueError:
            tkinter.messagebox.showerror("Invalid Input", "Please enter valid numbers for ATR % and Trigger Price.")
            trig_entry.focus()

    def cancel():
        modal.destroy()
        try:
            trade_type_combo.current(previous_index)
        except Exception:
            pass

    btnf = Frame(modal)
    btnf.pack(pady=10)
    Button(btnf, text="OK", width=8, command=save).pack(side=LEFT, padx=5)
    Button(btnf, text="Cancel", width=8, command=cancel).pack(side=LEFT, padx=5)
    trig_entry.bind("<Return>", lambda e: save())
    trig_entry.bind("<Escape>", lambda e: cancel())
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
            trade_type_combo.config(state="normal")
            trade_type_combo.set("Conditional Order")
            trade_type_combo.config(state="readonly")
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
            _set_stop_loss_combo_display(stop_loss_combo, value_entry.get())
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

def _update_stop_loss_value_field(stop_loss_combo, value_entry, reset_value=False, open_modal_if_custom=True):
    """
    Handle stop loss selection change. Optionally shows modal for Custom option.
    """
    canon = _canonical_stop_loss_type(stop_loss_combo.get())
    if canon == "Custom":
        if open_modal_if_custom:
            _show_custom_stop_loss_modal(stop_loss_combo, value_entry)
        else:
            _set_stop_loss_combo_display(stop_loss_combo, value_entry.get())
    else:
        if reset_value:
            value_entry.delete(0, END)
            value_entry.insert(0, "0")
        if stop_loss_combo.get() != canon:
            stop_loss_combo.config(state="normal")
            stop_loss_combo.set(canon)
            stop_loss_combo.config(state="readonly")

def getScrollableframe(frame):
    container = Frame(frame)
    container.pack(fill=BOTH, expand=True)

    global scroll_canvas
    scroll_canvas = Canvas(container, highlightthickness=0)
    scroll_canvas.pack(side=LEFT, fill=BOTH, expand=True)

    scrollbar = ttk.Scrollbar(container, orient="vertical", command=scroll_canvas.yview)
    scrollbar.pack(side=RIGHT, fill=Y)

    scroll_canvas.configure(yscrollcommand=scrollbar.set)

    global scrollable_frame
    scrollable_frame = ttk.Frame(scroll_canvas)

    scrollable_frame.bind(
        "<Configure>",
        lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))
    )

    canvas_frame = scroll_canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

    def _resize_canvas(event):
        scroll_canvas.itemconfig(canvas_frame, width=event.width)

    scroll_canvas.bind("<Configure>", _resize_canvas)

def _get_active_row_index():
    """Return the row index to apply hotkey settings to (last row, or 0 if none)."""
    if not symbol:
        return -1
    return len(symbol) - 1


def _setup_hotkeys(root):
    """
    Bind hotkeys to the root window. Settings apply to the active row (last row).
    HOT KEYS:
      SHIFT+ENTER = Execute
      Stop Loss: SHIFT+C=Custom, SHIFT+L=LOD, SHIFT+H=HOD, SHIFT+0/1/2/3/4 = 10/15/20/25/33% ATR
      Trade Type: ALT+C=Custom entry, ALT+A=ASK+1/2, ALT+B=BID-1/2, ALT+F=FB, ALT+L=Last Bar (LB),
                  ALT+R=RBB, ALT+P=PBe1, CTRL+P=PBe2, CTRL+L=Limit Order, CTRL+C=Conditional
      Buy/Sell: CTRL+B=BUY, CTRL+S=SELL
      Time Frame: CTRL+1/2/3/5/6/7 = 1/2/3/5/10/15 min
      Profit: ALT+1/2/3 = 1:1, 2:1, 3:1
      Time: SHIFT+O=OTH, SHIFT+D=DAY
    """
    def apply_stop_loss(idx, config_index):
        if idx < 0 or idx >= len(stopLoss):
            return
        stopLoss[idx].current(config_index)
        if idx < len(stopLossValue):
            _update_stop_loss_value_field(stopLoss[idx], stopLossValue[idx], reset_value=True)

    def apply_trade_type(idx, config_index, entry_point_value=None, show_modal_if_custom=True):
        if idx < 0 or idx >= len(tradeType):
            return
        tradeType[idx].current(config_index)
        if entry_point_value is not None and idx < len(entry_points):
            entry_points[idx].delete(0, END)
            entry_points[idx].insert(0, entry_point_value)
        # When setting to Custom or Limit Order via hotkey, show the entry price modal (same as when user selects from dropdown)
        if show_modal_if_custom and idx < len(entry_points):
            sel = Config.entryTradeType[config_index] if 0 <= config_index < len(Config.entryTradeType) else ""
            if sel == "Custom":
                _show_entry_price_modal(tradeType[idx], entry_points[idx], "Custom")
            elif sel == "Limit Order" and entry_point_value is None:
                _show_entry_price_modal(tradeType[idx], entry_points[idx], "Limit Order")
            elif sel == "Conditional Order":
                _show_conditional_order_modal(tradeType[idx], entry_points[idx], "Conditional Order")

    def apply_time_frame(idx, config_index):
        if idx < 0 or idx >= len(timeFrame):
            return
        timeFrame[idx].current(config_index)

    def apply_profit(idx, config_index):
        if idx < 0 or idx >= len(takeProfit):
            return
        takeProfit[idx].current(config_index)

    def apply_tif(idx, config_index):
        if idx < 0 or idx >= len(timeInForce):
            return
        timeInForce[idx].current(config_index)

    def apply_buy_sell(idx, config_index):
        if idx < 0 or idx >= len(buySell):
            return
        buySell[idx].current(config_index)

    def execute_active(event=None):
        idx = _get_active_row_index()
        if idx >= 0:
            execute_row(idx)
        return "break"

    def on_key(handler):
        def f(event):
            idx = _get_active_row_index()
            if idx >= 0:
                handler(idx)
            return "break"
        return f

    # Execute: SHIFT+ENTER
    root.bind("<Shift-Return>", execute_active)

    # Stop Loss: SHIFT+C=Custom(1), SHIFT+L=LOD(4), SHIFT+H=HOD(3), SHIFT+0/1/2/3/4 = 10/15/20/25/33% ATR
    root.bind("<Shift-c>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("Custom"))))
    root.bind("<Shift-C>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("Custom"))))
    root.bind("<Shift-l>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("LOD"))))
    root.bind("<Shift-L>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("LOD"))))
    root.bind("<Shift-h>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("HOD"))))
    root.bind("<Shift-H>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("HOD"))))
    # Shift+number: on Windows Tk sends shifted character keysyms (! @ # $ )), not numeric keys.
    root.bind("<Shift-parenright>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("10% ATR"))))  # Shift+0
    root.bind("<Shift-0>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("10% ATR"))))  # Fallback for platforms that report Shift+0
    root.bind("<Shift-exclam>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("15% ATR"))))   # Shift+1
    root.bind("<Shift-at>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("20% ATR"))))     # Shift+2
    root.bind("<Shift-numbersign>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("25% ATR"))))  # Shift+3
    root.bind("<Shift-dollar>", on_key(lambda i: apply_stop_loss(i, Config.stopLoss.index("33% ATR"))))  # Shift+4

    # Trade Type: ALT+C=Custom entry, ALT+A=Ask+.05, ALT+B=Bid-.05, ALT+F=FB, ALT+L=Last Bar (LB), ALT+R=RBB, ALT+P=PBe1, CTRL+P=PBe2, CTRL+L=Limit Order, CTRL+C=Conditional
    root.bind("<Alt-c>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("Custom"))))
    root.bind("<Alt-C>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("Custom"))))
    root.bind("<Alt-a>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("ASK + 1/2"))))
    root.bind("<Alt-A>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("ASK + 1/2"))))
    root.bind("<Alt-b>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("BID - 1/2"))))
    root.bind("<Alt-B>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("BID - 1/2"))))
    root.bind("<Alt-f>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("FB"))))
    root.bind("<Alt-F>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("FB"))))
    root.bind("<Alt-l>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("LB"))))
    root.bind("<Alt-L>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("LB"))))
    root.bind("<Alt-r>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("RBB"))))
    root.bind("<Alt-R>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("RBB"))))
    root.bind("<Alt-p>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("PBe1"))))
    root.bind("<Alt-P>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("PBe1"))))
    root.bind("<Control-p>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("PBe2"))))
    root.bind("<Control-P>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("PBe2"))))
    root.bind("<Control-l>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("Limit Order"))))
    root.bind("<Control-L>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("Limit Order"))))
    root.bind("<Control-c>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("Conditional Order"))))
    root.bind("<Control-C>", on_key(lambda i: apply_trade_type(i, Config.entryTradeType.index("Conditional Order"))))

    # Buy/Sell: CTRL+B=BUY(0), CTRL+S=SELL(1)
    root.bind("<Control-b>", on_key(lambda i: apply_buy_sell(i, Config.buySell.index("BUY"))))
    root.bind("<Control-B>", on_key(lambda i: apply_buy_sell(i, Config.buySell.index("BUY"))))
    root.bind("<Control-s>", on_key(lambda i: apply_buy_sell(i, Config.buySell.index("SELL"))))
    root.bind("<Control-S>", on_key(lambda i: apply_buy_sell(i, Config.buySell.index("SELL"))))

    # Time Frame: CTRL+1=1min(0), CTRL+2=2min(1), CTRL+3=3min(2), CTRL+5=5min(3), CTRL+6=10min(4), CTRL+7=15min(5)
    root.bind("<Control-Key-1>", on_key(lambda i: apply_time_frame(i, 0)))
    root.bind("<Control-Key-2>", on_key(lambda i: apply_time_frame(i, 1)))
    root.bind("<Control-Key-3>", on_key(lambda i: apply_time_frame(i, 2)))
    root.bind("<Control-Key-5>", on_key(lambda i: apply_time_frame(i, 3)))
    root.bind("<Control-Key-6>", on_key(lambda i: apply_time_frame(i, 4)))
    root.bind("<Control-Key-7>", on_key(lambda i: apply_time_frame(i, 5)))

    # Profit: ALT+1=1:1(0), ALT+2=2:1(2), ALT+3=3:1(4)
    root.bind("<Alt-Key-1>", on_key(lambda i: apply_profit(i, 0)))
    root.bind("<Alt-Key-2>", on_key(lambda i: apply_profit(i, 2)))
    root.bind("<Alt-Key-3>", on_key(lambda i: apply_profit(i, 4)))

    # Time: SHIFT+O=OTH(1), SHIFT+D=DAY(0)
    root.bind("<Shift-o>", on_key(lambda i: apply_tif(i, Config.timeInForce.index("OTH"))))
    root.bind("<Shift-O>", on_key(lambda i: apply_tif(i, Config.timeInForce.index("OTH"))))
    root.bind("<Shift-d>", on_key(lambda i: apply_tif(i, Config.timeInForce.index("DAY"))))
    root.bind("<Shift-D>", on_key(lambda i: apply_tif(i, Config.timeInForce.index("DAY"))))

    logging.info("Hotkeys bound: Shift+Enter=Execute, Shift+C=Custom stop, Shift+L/H/0-4=Stop Loss, Alt+C=Custom entry, Alt+A/B/F/L/R/P=Trade Type, Ctrl+P/L/C=Trade Type, Ctrl+B=BUY, Ctrl+S=SELL, Ctrl+1-7=Time Frame, Alt+1-3=Profit, Shift+O/D=Time")


def NewTradeFrame(frame,connection):
    logging.debug("New Trade Frame Init")
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
    _add_btn_row = Frame(addButton)
    _add_btn_row.pack(side=BOTTOM)
    Button(_add_btn_row, width="14", height="1", text="ADD", command=add).pack(side=LEFT, padx=(0, 6))
    Button(_add_btn_row, width="14", height="1", text="Duplicate", command=duplicate_last).pack(side=LEFT)
    addButton.pack(side=BOTTOM)
    # Log that all trading row elements are displayed (for debugging visibility, e.g. Option button)
    _log_ui_elements_displayed()
    # Hotkeys: apply to active (last) row
    _setup_hotkeys(frame)
    # addButtonbt.place(relx=0.5, rely=0.3, anchor=CENTER)
    # global managePositionButton
    # managePositionButton = Button(frame, width="15", height="1", text="Manage Position", command=openManagePosition)
    # managePositionButton.place(relx=0.7, rely=0.3, anchor=CENTER)


def _log_ui_elements_displayed():
    """Log that all trading row UI elements are present (Symbol through Option, Status)."""
    elements = [
        "Symbol", "Stop Loss", "Trade Type", "Buy/Sell", "Execute", "Risk", "Profit",
        "Time Frame", "Time In Force", "Replay", "ATR", "Break Even", "Option", "Status"
    ]
    logging.debug("UI: All elements displayed: %s", ", ".join(elements))


def _get_current_session():
    """Detect current US equity session (same logic as SendTrade) using US/Eastern."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.datetime.now(ZoneInfo('US/Eastern')).time().replace(microsecond=0)
    except Exception:
        now_et = datetime.datetime.now().time().replace(microsecond=0)
    pre_start = datetime.time(4, 0, 0)
    rth_start = datetime.time(9, 30, 0)
    rth_end = datetime.time(16, 0, 0)
    after_end = datetime.time(20, 0, 0)
    if rth_start <= now_et < rth_end:
        return 'RTH'
    if pre_start <= now_et < rth_start:
        return 'PREMARKET'
    if rth_end <= now_et < after_end:
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
    """Add a new trade row when user clicks ADD button."""
    addField(0, "")
    _scroll_to_bottom()


def duplicate_last():
    """
    Copy the template row onto another row.

    If the bottom row is blank (e.g. auto-added after Execute), fill that row instead of
    appending — avoids an empty row stuck between the template and the duplicate.
    If the bottom row already has a symbol, append a new row and copy the bottom row there.
    """
    n = len(symbol)
    if n < 1:
        return

    tail_blank = n >= 2 and not (symbol[n - 1].get() or "").strip()
    if tail_blank:
        src = n - 2
        dst = n - 1
    else:
        if n == 1 and not (symbol[0].get() or "").strip():
            return
        src = n - 1
        addField(0, "")
        dst = len(symbol) - 1

    pull_back_trade_types = ['PBe1', 'PBe2', 'PBe1e2', 'PBe1 (3)', 'PBe2 (3)']

    symbol[dst].delete(0, END)
    symbol[dst].insert(0, symbol[src].get())

    timeFrame[dst].current(timeFrame[src].current())
    takeProfit[dst].current(takeProfit[src].current())
    buySell[dst].current(buySell[src].current())
    timeInForce[dst].current(timeInForce[src].current())
    breakEven[dst].current(breakEven[src].current())

    risk[dst].delete(0, END)
    risk[dst].insert(0, risk[src].get())

    stopLossValue[dst].config(state="normal")
    stopLossValue[dst].delete(0, END)
    stopLossValue[dst].insert(0, stopLossValue[src].get())

    sl_canon = _canonical_stop_loss_type(stopLoss[src].get())
    if sl_canon == "Custom":
        _set_stop_loss_combo_display(stopLoss[dst], stopLossValue[dst].get())
    else:
        stopLoss[dst].current(Config.stopLoss.index(sl_canon))

    tt_canon = _canonical_trade_type(tradeType[src].get())
    entry_points[dst].delete(0, END)
    entry_points[dst].insert(0, entry_points[src].get())
    if tt_canon in ("Custom", "Limit Order"):
        _set_trade_type_combo_display(tradeType[dst], tt_canon, entry_points[dst].get())
    elif tt_canon == "Conditional Order":
        tradeType[dst].config(state="normal")
        tradeType[dst].set("Conditional Order")
        tradeType[dst].config(state="readonly")
    else:
        tradeType[dst].current(Config.entryTradeType.index(tt_canon))

    if tt_canon in pull_back_trade_types:
        stopLoss[dst].config(state="disabled")
        stopLossValue[dst].config(state="disabled")
    else:
        stopLoss[dst].config(state="readonly")
        stopLossValue[dst].config(state="normal")

    replayEnabled[dst] = replayEnabled[src]
    if dst < len(replayButtonList) and replayButtonList[dst] is not None:
        replayButtonList[dst].config(bg='#90EE90' if replayEnabled[dst] else '#D3D3D3')

    atrEnabled[dst] = atrEnabled[src]
    if dst < len(atr) and src < len(atr):
        atr[dst].config(state="normal")
        atr[dst].delete(0, END)
        atr[dst].insert(0, atr[src].get())
        atr[dst].config(state="disabled")
    if dst < len(atrButtonList) and atrButtonList[dst] is not None:
        atrButtonList[dst].config(bg='#90EE90' if atrEnabled[dst] else '#D3D3D3')

    optionEnabled[dst] = optionEnabled[src]
    if dst < len(optionContract) and src < len(optionContract):
        optionContract[dst].set(optionContract[src].get())
        optionExpire[dst].set(optionExpire[src].get())
        optionEntryOrderType[dst].set(optionEntryOrderType[src].get())
        optionStopLossOrderType[dst].set(optionStopLossOrderType[src].get())
        optionProfitOrderType[dst].set(optionProfitOrderType[src].get())
        optionRiskAmount[dst].set(optionRiskAmount[src].get())
    if dst < len(optionButtonList) and optionButtonList[dst] is not None:
        optionButtonList[dst].config(bg='#90EE90' if optionEnabled[dst] else '#D3D3D3')

    _scroll_to_bottom()


def _scroll_to_bottom():
    """Scroll trade list to the most recently added row."""
    global scroll_canvas
    if scroll_canvas is not None:
        scroll_canvas.update_idletasks()
        scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))
        scroll_canvas.yview_moveto(1.0)


def _log_snapshot_for_row(row_index):
    try:
        current_symbol = symbol[row_index].get()
        current_timeframe = timeFrame[row_index].get()
        contract = getContract(current_symbol, None)
        hist_bar = _get_latest_hist_bar(IbConn, contract, current_timeframe)
        if hist_bar:
            logging.debug(
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
    logging.debug("Execute row %s detected session: %s, outsideRth: %s", row_index, session, outsideRth)

    current_tif = timeInForce[row_index].get()
    # Premarket behavior: if user selects DAY, schedule submit for 09:30 ET (market open).
    # This avoids changing SendTrade logic and avoids warnings for the intended workflow.
    if outsideRth and current_tif == 'DAY':
        def _seconds_until_next_rth_open():
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo('US/Eastern')
            except Exception:
                tz = None
            now = datetime.datetime.now(tz) if tz else datetime.datetime.now()
            today = now.date()
            rth_open = datetime.datetime.combine(today, datetime.time(9, 30, 0))
            if tz:
                rth_open = rth_open.replace(tzinfo=tz)
            # If it's already past 09:30 ET today, schedule for next day 09:30.
            if now >= rth_open:
                rth_open = rth_open + datetime.timedelta(days=1)
            return max(0, int((rth_open - now).total_seconds()))

        delay_sec = _seconds_until_next_rth_open()
        _set_status(row_index, "Scheduled 09:30")
        tkinter.messagebox.showinfo(
            'Scheduled',
            f"Current session is {session}.\n\n"
            f"Time in Force is DAY.\n\n"
            f"Order will be submitted automatically at 09:30 ET (market open)."
        )
        logging.info("Row %s: session=%s, TIF=DAY -> scheduling SendTrade for 09:30 ET in %ss", row_index, session, delay_sec)
    else:
        # When in extended hours but user chose Day/GTC, keep existing warning behavior
        if outsideRth and current_tif != 'OTH':
            tkinter.messagebox.showwarning(
                'Session / Time in Force',
                f"Current session is {session} (extended hours).\n\n"
                f"You selected Time in Force: {current_tif}.\n\n"
                f"For extended hours trading, use 'OTH' (Outside Trading Hours).\n"
                f"Proceeding with current selection. Order will use stop-limit for entry.")
            logging.warning(
                f"Row {row_index}: session={session}, Time in Force='{current_tif}' (consider OTH for extended hours); allowing trade.")

    if not (outsideRth and current_tif == 'DAY'):
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

    # Get option trading state and parameters for this row
    is_option_enabled = False
    option_contract = ""
    option_expire = ""
    option_entry_order_type = "Market"
    option_sl_order_type = "Market"
    option_tp_order_type = "Market"
    option_risk_amount = ""
    
    if row_index < len(optionEnabled):
        is_option_enabled = optionEnabled[row_index]
        if is_option_enabled:
            if row_index < len(optionContract):
                option_contract = optionContract[row_index].get()
            if row_index < len(optionExpire):
                option_expire = optionExpire[row_index].get()
            if row_index < len(optionEntryOrderType):
                option_entry_order_type = optionEntryOrderType[row_index].get()
            if row_index < len(optionStopLossOrderType):
                option_sl_order_type = optionStopLossOrderType[row_index].get()
            if row_index < len(optionProfitOrderType):
                option_tp_order_type = optionProfitOrderType[row_index].get()
            if row_index < len(optionRiskAmount):
                option_risk_amount = optionRiskAmount[row_index].get()
            
            # Validate option parameters (strike selection and expiration selection)
            if not option_contract or not option_expire:
                tkinter.messagebox.showerror('Error', "Option trading requires Strike and Expiration settings")
                return

    stop_loss_canon = _canonical_stop_loss_type(stopLoss[row_index].get())
    trade_type_canon = _canonical_trade_type(tradeType[row_index].get())

    # Store replay state for this trade (will be retrieved in StatusUpdate)
    trade_key = (symbol[row_index].get(), timeFrame[row_index].get(), trade_type_canon,
                 buySell[row_index].get(), datetime.datetime.now().timestamp())
    Config.order_replay_pending[trade_key] = is_replay_enabled
    logging.info("Stored replay state for trade: key=%s, replay=%s", trade_key, is_replay_enabled)

    atr_value_for_send = ""
    if row_index < len(atr) and row_index < len(atrEnabled) and atrEnabled[row_index]:
        atr_value_for_send = atr[row_index].get().strip()

    async def _send_now_or_at_rth_open():
        # If scheduled for 09:30 ET, wait then submit as regular-hours (outsideRth=False) with TIF=DAY.
        if outsideRth and current_tif == 'DAY':
            await asyncio.sleep(delay_sec)
            logging.info("Row %s: submitting scheduled DAY order at/after 09:30 ET", row_index)
            return await SendTrade(
                IbConn,
                symbol[row_index].get(),
                timeFrame[row_index].get(),
                takeProfit[row_index].get(),
                stop_loss_canon,
                risk[row_index].get(),
                'DAY',
                trade_type_canon,
                buySell[row_index].get(),
                atr_value_for_send,
                0,
                Config.pullBackNo,
                current_sl_value,
                breakEven[row_index].get(),
                False,  # outsideRth=False at RTH open
                entry_points[row_index].get(),
                is_option_enabled,
                option_contract,
                option_expire,
                option_entry_order_type,
                option_sl_order_type,
                option_tp_order_type,
                option_risk_amount,
            )

        # Default: submit immediately with current flags
        return await SendTrade(
            IbConn,
            symbol[row_index].get(),
            timeFrame[row_index].get(),
            takeProfit[row_index].get(),
            stop_loss_canon,
            risk[row_index].get(),
            timeInForce[row_index].get(),
            trade_type_canon,
            buySell[row_index].get(),
            atr_value_for_send,
            0,
            Config.pullBackNo,
            current_sl_value,
            breakEven[row_index].get(),
            outsideRth,
            entry_points[row_index].get(),
            is_option_enabled,
            option_contract,
            option_expire,
            option_entry_order_type,
            option_sl_order_type,
            option_tp_order_type,
            option_risk_amount,
        )

    send_future = asyncio.ensure_future(_send_now_or_at_rth_open())

    def _handle_send_done(fut):
        try:
            result = fut.result()
            if isinstance(result, dict) and result.get("status") == "ATR_BLOCKED":
                _set_status(row_index, "ATR Blocked")
                enableEntryState(row_index)
                row_async_tasks[row_index] = None
                btn = cancelButton[row_index]
                btn.config(text="Execute")
                btn['command'] = lambda idx=row_index: execute_row(idx)
                logging.info(
                    "Row %s blocked by ATR gate: strategy=%s stop_size=%s atr_percentage=%s",
                    row_index, result.get("strategy"), result.get("stop_size"), result.get("atr_percentage")
                )
        except asyncio.CancelledError:
            pass
        except Exception as done_err:
            logging.error("Error handling SendTrade completion for row %s: %s", row_index, done_err)

    send_future.add_done_callback(_handle_send_done)

    row_async_tasks[row_index] = send_future
    disableEntryState(row_index)

    button = cancelButton[row_index]
    button.config(text="Cancel")
    button['command'] = lambda idx=row_index: cancel_row(idx)
    # Keep button enabled so user can cancel if needed

    # Add a new row after Execute so user can enter next trade
    addField(0, "")

    # Scroll to bottom so the new row and status are visible
    _scroll_to_bottom()


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


def _toggle_atr_fields(row_index):
    """Toggle ATR guard for a row and show modal to configure ATR% when enabled."""
    if row_index < len(atrEnabled):
        atrEnabled[row_index] = not atrEnabled[row_index]
        if atrEnabled[row_index]:
            _show_atr_config_modal(row_index)
            if row_index < len(atrButtonList) and atrButtonList[row_index] is not None:
                atrButtonList[row_index].config(bg='#90EE90' if atrEnabled[row_index] else '#D3D3D3')
        else:
            # Disable ATR guard and clear stored ATR value
            if row_index < len(atr):
                atr[row_index].config(state="normal")
                atr[row_index].delete(0, END)
                atr[row_index].insert(0, "")
                atr[row_index].config(state="disabled")
            if row_index < len(atrButtonList) and atrButtonList[row_index] is not None:
                atrButtonList[row_index].config(bg='#D3D3D3')


def _show_atr_config_modal(row_index):
    """Show modal dialog to configure ATR percentage threshold for guard logic."""
    parent = scrollable_frame.winfo_toplevel()

    modal = tkinter.Toplevel(parent)
    modal.title("ATR Guard Configuration")
    modal.geometry("320x170")
    modal.resizable(False, False)
    modal.transient(parent)
    modal.grab_set()

    modal.update_idletasks()
    x = (modal.winfo_screenwidth() // 2) - (320 // 2)
    y = (modal.winfo_screenheight() // 2) - (170 // 2)
    modal.geometry(f"320x170+{x}+{y}")

    current_value = ""
    if row_index < len(atr):
        current_value = atr[row_index].get() if atr[row_index].get() else str(Config.defaultValue.get("atr", ""))

    Label(modal, text="Input ATR % threshold:", font=(Config.fontName2, Config.fontSize2)).pack(pady=(16, 8))
    atr_var = StringVar(modal, value=current_value)
    atr_entry = Entry(modal, textvariable=atr_var, width=16, font=(Config.fontName2, Config.fontSize2))
    atr_entry.pack(pady=4)
    atr_entry.select_range(0, END)
    atr_entry.focus()

    button_frame = Frame(modal)
    button_frame.pack(pady=12)

    def save_atr_config():
        val = atr_var.get().strip()
        if val == "":
            tkinter.messagebox.showerror("Invalid Input", "Please enter ATR % value (example: 20)")
            atr_entry.focus()
            return
        try:
            val_num = float(val)
            if val_num <= 0:
                raise ValueError()
            val_clean = str(int(val_num)) if val_num.is_integer() else str(val_num)
            if row_index < len(atr):
                atr[row_index].config(state="normal")
                atr[row_index].delete(0, END)
                atr[row_index].insert(0, val_clean)
                atr[row_index].config(state="disabled")
            modal.destroy()
        except ValueError:
            tkinter.messagebox.showerror("Invalid Input", "Please enter a valid positive number (example: 20)")
            atr_entry.focus()

    def cancel_atr_config():
        # If user cancels while enabling, revert to disabled state
        if row_index < len(atrEnabled):
            atrEnabled[row_index] = False
        if row_index < len(atr):
            atr[row_index].config(state="normal")
            atr[row_index].delete(0, END)
            atr[row_index].insert(0, "")
            atr[row_index].config(state="disabled")
        if row_index < len(atrButtonList) and atrButtonList[row_index] is not None:
            atrButtonList[row_index].config(bg='#D3D3D3')
        modal.destroy()

    Button(button_frame, text="OK", width=8, command=save_atr_config).pack(side=LEFT, padx=5)
    Button(button_frame, text="Cancel", width=8, command=cancel_atr_config).pack(side=LEFT, padx=5)

    atr_entry.bind("<Return>", lambda e: save_atr_config())
    atr_entry.bind("<Escape>", lambda e: cancel_atr_config())


def _toggle_option_fields(row_index):
    """Show/hide option configuration modal when option button is clicked"""
    if row_index < len(optionEnabled):
        optionEnabled[row_index] = not optionEnabled[row_index]
        if optionEnabled[row_index]:
            # Show option configuration modal
            _show_option_config_modal(row_index)
            # Update button appearance after modal closes (green if OK, gray if Cancel)
            if row_index < len(optionButtonList) and optionButtonList[row_index]:
                optionButtonList[row_index].config(
                    bg='#90EE90' if optionEnabled[row_index] else '#D3D3D3'
                )
        else:
            # Clear option fields when disabled
            if row_index < len(optionContract):
                optionContract[row_index].set("")
            if row_index < len(optionExpire):
                optionExpire[row_index].set("")
            if row_index < len(optionEntryOrderType):
                optionEntryOrderType[row_index].set("Market")
            if row_index < len(optionStopLossOrderType):
                optionStopLossOrderType[row_index].set("Market")
            if row_index < len(optionProfitOrderType):
                optionProfitOrderType[row_index].set("Market")
            if row_index < len(optionButtonList) and optionButtonList[row_index]:
                optionButtonList[row_index].config(bg='#D3D3D3')  # Light gray when disabled

def _show_option_config_modal(row_index):
    """Show modal dialog to configure option trading parameters"""
    # Get parent window
    parent = scrollable_frame.winfo_toplevel()
    
    # Create modal dialog (wider so right column is not clipped)
    modal = tkinter.Toplevel(parent)
    modal.title("Option Trading Configuration")
    modal_width = 520
    modal_height = 380
    modal.geometry(f"{modal_width}x{modal_height}")
    modal.resizable(True, True)
    modal.minsize(440, 360)
    modal.transient(parent)
    modal.grab_set()
    
    # Ensure grid columns get space: label column and value column
    modal.grid_columnconfigure(0, minsize=200)
    modal.grid_columnconfigure(1, minsize=180)
    
    # Center the dialog
    modal.update_idletasks()
    x = (modal.winfo_screenwidth() // 2) - (modal_width // 2)
    y = (modal.winfo_screenheight() // 2) - (modal_height // 2)
    modal.geometry(f"{modal_width}x{modal_height}+{x}+{y}")
    
    # Contract (Strike Price) - dropdown: ATM, OTM 1, OTM 2, OTM 3
    Label(modal, text="Strike (Contract):", font=(Config.fontName2, Config.fontSize2)).grid(row=0, column=0, sticky="w", padx=10, pady=5)
    strike_options = ["ATM", "OTM 1", "OTM 2", "OTM 3"]
    contract_combo = ttk.Combobox(modal, state="readonly", width=12, values=strike_options)
    contract_combo.grid(row=0, column=1, padx=10, pady=5)
    if row_index < len(optionContract):
        current_val = optionContract[row_index].get()
        # Map stored internal codes back to combo text
        mapping = {
            "ATM": "ATM",
            "OTM1": "OTM 1",
            "OTM2": "OTM 2",
            "OTM3": "OTM 3",
        }
        display_val = mapping.get(current_val, "ATM")
        if display_val in strike_options:
            contract_combo.set(display_val)
        else:
            contract_combo.set("ATM")
    else:
        contract_combo.set("ATM")
    
    # Expiration: 0 = current Friday, 1 = next Friday, 2 = next next, 3 = next next next
    Label(modal, text="Expiration:", font=(Config.fontName2, Config.fontSize2)).grid(row=1, column=0, sticky="w", padx=10, pady=5)
    expiry_options_display = ["0 = Current Friday", "1 = Next Friday", "2 = Next Next Friday", "3 = Next Next Next Friday"]
    expiry_options_values = ["0", "1", "2", "3"]
    expire_combo = ttk.Combobox(modal, state="readonly", width=24, values=expiry_options_display)
    expire_combo.grid(row=1, column=1, padx=10, pady=5)
    if row_index < len(optionExpire):
        current_exp = optionExpire[row_index].get() or "0"
        if current_exp in expiry_options_values:
            idx = expiry_options_values.index(current_exp)
            expire_combo.set(expiry_options_display[idx])
        else:
            expire_combo.set(expiry_options_display[0])

    # Risk amount ($) for options
    Label(modal, text="Risk Amount ($):", font=(Config.fontName2, Config.fontSize2)).grid(row=2, column=0, sticky="w", padx=10, pady=5)
    risk_entry = Entry(modal, width=15, font=(Config.fontName2, Config.fontSize2))
    risk_entry.grid(row=2, column=1, padx=10, pady=5)
    if row_index < len(optionRiskAmount):
        risk_entry.insert(0, optionRiskAmount[row_index].get())
    
    # Entry Order Type
    Label(modal, text="Entry Order Type:", font=(Config.fontName2, Config.fontSize2)).grid(row=3, column=0, sticky="w", padx=10, pady=5)
    entry_order_combo = ttk.Combobox(modal, state="readonly", width=12, values=Config.optionOrderTypes)
    entry_order_combo.grid(row=3, column=1, padx=10, pady=5)
    if row_index < len(optionEntryOrderType):
        current_value = optionEntryOrderType[row_index].get()
        if current_value in Config.optionOrderTypes:
            entry_order_combo.current(Config.optionOrderTypes.index(current_value))
        else:
            entry_order_combo.current(0)
    
    # Stop Loss Order Type
    Label(modal, text="Stop Loss Order Type:", font=(Config.fontName2, Config.fontSize2)).grid(row=4, column=0, sticky="w", padx=10, pady=5)
    sl_order_combo = ttk.Combobox(modal, state="readonly", width=12, values=Config.optionOrderTypes)
    sl_order_combo.grid(row=4, column=1, padx=10, pady=5)
    if row_index < len(optionStopLossOrderType):
        current_value = optionStopLossOrderType[row_index].get()
        if current_value in Config.optionOrderTypes:
            sl_order_combo.current(Config.optionOrderTypes.index(current_value))
        else:
            sl_order_combo.current(0)
    
    # Profit Order Type
    Label(modal, text="Profit Order Type:", font=(Config.fontName2, Config.fontSize2)).grid(row=5, column=0, sticky="w", padx=10, pady=5)
    profit_order_combo = ttk.Combobox(modal, state="readonly", width=12, values=Config.optionOrderTypes)
    profit_order_combo.grid(row=5, column=1, padx=10, pady=5)
    if row_index < len(optionProfitOrderType):
        current_value = optionProfitOrderType[row_index].get()
        if current_value in Config.optionOrderTypes:
            profit_order_combo.current(Config.optionOrderTypes.index(current_value))
        else:
            profit_order_combo.current(0)
    
    # Info label (wraplength so text wraps and doesn't clip)
    info_text = "Bid+: Start at bid, +$0.05 every 2s until fill (max 20). Ask-: Start at ask, -$0.05 every 2s until fill (max 20, floor $0.01)."
    info_label = Label(modal, text=info_text, font=(Config.fontName2, 9), justify=LEFT, wraplength=modal_width - 40)
    info_label.grid(row=6, column=0, columnspan=2, sticky="w", padx=10, pady=10)
    
    # Buttons frame
    button_frame = Frame(modal)
    button_frame.grid(row=7, column=0, columnspan=2, pady=10)
    
    def save_option_config():
        try:
            # Save contract selection as internal code
            selected_strike = contract_combo.get()
            strike_map_reverse = {
                "ATM": "ATM",
                "OTM 1": "OTM1",
                "OTM 2": "OTM2",
                "OTM 3": "OTM3",
            }
            internal_strike = strike_map_reverse.get(selected_strike, "ATM")
            if row_index < len(optionContract):
                optionContract[row_index].set(internal_strike)
            
            # Save expiration: 0=current Friday, 1=next, 2=next next, 3=next next next
            expire_display = expire_combo.get().strip()
            expire_val = "0"
            if expire_display and "=" in expire_display:
                expire_val = expire_display.split("=")[0].strip()
            if expire_val not in ("0", "1", "2", "3"):
                expire_val = "0"
            if row_index < len(optionExpire):
                optionExpire[row_index].set(expire_val)

            # Save risk amount (optional)
            risk_val = risk_entry.get().strip()
            if row_index < len(optionRiskAmount):
                optionRiskAmount[row_index].set(risk_val)
            
            # Save order types
            if row_index < len(optionEntryOrderType):
                optionEntryOrderType[row_index].set(entry_order_combo.get())
            if row_index < len(optionStopLossOrderType):
                optionStopLossOrderType[row_index].set(sl_order_combo.get())
            if row_index < len(optionProfitOrderType):
                optionProfitOrderType[row_index].set(profit_order_combo.get())
            
            modal.destroy()
        except ValueError:
            tkinter.messagebox.showerror("Invalid Input", "Contract (Strike Price) must be a valid number")
            contract_entry.focus()
    
    def cancel_option_config():
        # Disable option if cancelled and reset button appearance
        if row_index < len(optionEnabled):
            optionEnabled[row_index] = False
        if row_index < len(optionButtonList) and optionButtonList[row_index]:
            optionButtonList[row_index].config(bg='#D3D3D3')
        modal.destroy()
    
    Button(button_frame, text="OK", width=8, command=save_option_config).pack(side=LEFT, padx=5)
    Button(button_frame, text="Cancel", width=8, command=cancel_option_config).pack(side=LEFT, padx=5)
    
    # Bind Enter key to save
    contract_combo.bind("<Return>", lambda e: save_option_config())
    expire_combo.bind("<Return>", lambda e: save_option_config())
    risk_entry.bind("<Return>", lambda e: save_option_config())
    contract_combo.focus()

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
    if row_index < len(atrEnabled):
        atrEnabled[row_index] = False
        if row_index < len(atrButtonList):
            atrButtonList[row_index].config(bg='#D3D3D3')


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
                _update_stop_loss_value_field(stopLoss[len(stopLoss) - 1], stopLossValue[len(stopLossValue) - 1], reset_value=False, open_modal_if_custom=False)
            if value.get("entry_points")==None:
                entry_points[len(entry_points) - 1].insert(0,"0")
            else:
                entry_points[len(entry_points) - 1].insert(0, value.get("entry_points"))

            breakEven[len(breakEven) - 1].insert(0, value.get("breakEven"))
            timeInForce[len(timeInForce) - 1].current(Config.timeInForce.index(value.get("tif")))

            # Set trade type if available
            if value.get("barType") is not None and value.get("barType") in Config.entryTradeType:
                bt = value.get("barType")
                tradeType[len(tradeType) - 1].current(Config.entryTradeType.index(bt))
                if bt in ("Custom", "Limit Order"):
                    _set_trade_type_combo_display(tradeType[len(tradeType) - 1], bt, entry_points[len(entry_points) - 1].get())
                elif bt == "Conditional Order":
                    _co = tradeType[len(tradeType) - 1]
                    _co.config(state="normal")
                    _co.set("Conditional Order")
                    _co.config(state="readonly")
                # If it's a pull back type, disable stop loss
                pull_back_types = ['PBe1', 'PBe2', 'PBe1 (3)', 'PBe2 (3)']
                if bt in pull_back_types:
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
            # Replay state: use cached value if present, else keep default from addField
            if len(replayEnabled) <= row_idx:
                replayEnabled.append(False)
                if len(replayButtonList) <= row_idx:
                    replayButtonList.append(None)
            if row_idx < len(replayEnabled) and value.get("replayEnabled") is not None:
                replayEnabled[row_idx] = bool(value.get("replayEnabled"))
                if row_idx < len(replayButtonList) and replayButtonList[row_idx] is not None:
                    replayButtonList[row_idx].config(bg='#90EE90' if replayEnabled[row_idx] else '#D3D3D3')

            # ATR state/value: if cached entry has userAtr value, restore and enable ATR button
            if row_idx < len(atr) and value.get("userAtr") is not None and str(value.get("userAtr")).strip() != "":
                atr[row_idx].config(state="normal")
                atr[row_idx].delete(0, END)
                atr[row_idx].insert(0, str(value.get("userAtr")).strip())
                atr[row_idx].config(state="disabled")
                if row_idx < len(atrEnabled):
                    atrEnabled[row_idx] = True
                if row_idx < len(atrButtonList) and atrButtonList[row_idx] is not None:
                    atrButtonList[row_idx].config(bg='#90EE90')




def addField(rowYPosition, initial_status_text=""):
    logging.debug("New Row Adding..")
    field = Frame(scrollable_frame)
    field.config(bg='#DCDCDC')
    # 14 columns (0–13): Symbol … Status. Do not configure a 15th column or empty space appears at the right edge.
    # Weighted columns so wide combos (Stop Loss, Trade Type, TIF) get more space; buttons stay narrower.
    _col_weights = {
        0: 1,   # Symbol
        1: 2,   # Stop Loss
        2: 3,   # Trade Type
        3: 1,   # Buy/Sell
        4: 1,   # Execute
        5: 1,   # Risk
        6: 1,   # Profit
        7: 2,   # Time Frame (e.g. "15 mins")
        8: 2,   # Time In Force (was clipped with equal tiny columns)
        9: 1,   # Replay
        10: 1,  # ATR
        11: 1,  # Break Even
        12: 1,  # Option
        13: 2,  # Status
    }
    for col in range(14):
        field.columnconfigure(col, weight=_col_weights.get(col, 1), minsize=36)
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
    
    # 11) ATR label (toggle button + modal, no inline input)
    atrLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="ATR", justify=LEFT)
    atrLbl.grid(row=0, column=10, sticky="ew", padx=5, pady=3)
    
    # 12) BREAK EVEN label
    breakEvenLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Break Even", justify=LEFT)
    breakEvenLbl.grid(row=0, column=11, sticky="ew", padx=5, pady=3)
    
    # 13) OPTION label
    optionLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Option", justify=LEFT)
    optionLbl.grid(row=0, column=12, sticky="ew", padx=5, pady=3)
    
    # 14) STATUS label
    statusLbl = Label(field, font=(Config.fontName2, Config.fontSize2), text="Status", justify=LEFT)
    statusLbl.grid(row=0, column=13, sticky="ew", padx=5, pady=3)
    
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
    
    # Ensure entry reflects the initial selection (no modal on row create)
    _update_stop_loss_value_field(stpLossEntry, stopLossValueEntry, reset_value=False, open_modal_if_custom=False)
    
    def on_stop_loss_change(event):
        combo = stpLossEntry
        value_entry = stopLossValueEntry
        current_selection = combo.get()
        canon_sl = _canonical_stop_loss_type(current_selection)
        if canon_sl == "Custom":
            combo._previous_index = previous_index_storage[0]
        else:
            idx = combo.current()
            if idx >= 0:
                previous_index_storage[0] = idx
        _update_stop_loss_value_field(combo, value_entry, reset_value=True, open_modal_if_custom=True)
    
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
    pull_back_trade_types = ['PBe1', 'PBe2', 'PBe1e2', 'PBe1 (3)', 'PBe2 (3)']
    bo_trade_types = ['BO e1', 'BO e2']
    fixed_sl_trade_types = set(pull_back_trade_types) | set(bo_trade_types)
    
    def on_trade_type_change(event):
        combo = tradeTypeEntry
        entry_points_entry = entry_pointValueEntry
        current_selection = combo.get()
        canon_tt = _canonical_trade_type(current_selection)

        if canon_tt in fixed_sl_trade_types:
            stpLossEntry.config(state="disabled")
            stopLossValueEntry.config(state="disabled")
        else:
            stpLossEntry.config(state="readonly")
            stopLossValueEntry.config(state="normal")

        if canon_tt in Config.manualOrderTypes and canon_tt not in ('ASK + 1/2', 'BID - 1/2'):
            combo._previous_index = previous_trade_type_index[0]
            _show_entry_price_modal(combo, entry_points_entry, canon_tt)
            previous_trade_type_index[0] = Config.entryTradeType.index(canon_tt)
        elif canon_tt == "Conditional Order":
            combo._previous_index = previous_trade_type_index[0]
            _show_conditional_order_modal(combo, entry_points_entry, "Conditional Order")
            previous_trade_type_index[0] = Config.entryTradeType.index("Conditional Order")
        elif canon_tt in bo_trade_types:
            combo._previous_index = previous_trade_type_index[0]
            _show_bo_modal(combo, entry_points_entry, canon_tt)
            previous_trade_type_index[0] = Config.entryTradeType.index(canon_tt)
        else:
            idx = combo.current()
            if idx >= 0:
                previous_trade_type_index[0] = idx
            entry_points_entry.delete(0, END)
            entry_points_entry.insert(0, "0")
    
    tradeTypeEntry.bind("<<ComboboxSelected>>", on_trade_type_change)

    # Show saved custom/limit prices in the combo after load (dropdown uses canonical labels only)
    _tt_init = _canonical_trade_type(tradeTypeEntry.get())
    if _tt_init in ("Custom", "Limit Order"):
        _set_trade_type_combo_display(tradeTypeEntry, _tt_init, entry_pointValueEntry.get())
    elif _tt_init == "Conditional Order":
        tradeTypeEntry.config(state="normal")
        tradeTypeEntry.set("Conditional Order")
        tradeTypeEntry.config(state="readonly")
    if _tt_init in pull_back_trade_types or _tt_init in bo_trade_types:
        stpLossEntry.config(state="disabled")
        stopLossValueEntry.config(state="disabled")

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
    secEntry = ttk.Combobox(field, state="readonly", width=11, value=Config.timeFrame)
    secEntry.config(width=11)
    secEntry.grid(row=1, column=7, sticky="ew", padx=5, pady=3)
    secEntry.current(0)
    setDefaultTimeFrame(secEntry)
    timeFrame.append(secEntry)

    # 9) TIME IN FORCE (column 8) — wider so "DAY" / "OTH" / "GTC" are not clipped
    timeForceEntry = ttk.Combobox(field, state="readonly", width=12, value=Config.timeInForce)
    timeForceEntry.config(width=12)
    timeForceEntry.grid(row=1, column=8, sticky="ew", padx=5, pady=3)
    timeForceEntry.current(0)
    setDefaultTif(timeForceEntry)
    timeInForce.append(timeForceEntry)

    # 10) REPLAY (column 9)
    row_index_for_replay = len(symbol) - 1
    replayButton = Button(field, width="10", height="1", text="Replay", bg='#D3D3D3')
    replayButton.grid(row=1, column=9, sticky="ew", padx=5, pady=3)
    # Use default Replay from Setting (same as UI: when On, SL fill re-enters the trade)
    default_replay = Config.defaultValue.get("replay", False)
    if isinstance(default_replay, str):
        default_replay = default_replay.lower() in ("true", "1", "yes", "on")
    replayEnabled.append(bool(default_replay))
    replayButtonList.append(replayButton)  # Store button reference
    if default_replay:
        replayButton.config(bg='#90EE90')  # Light green when enabled
    replayButton['command'] = lambda idx=row_index_for_replay: toggle_replay(idx)

    # 11) ATR (column 10) - toggle + modal (no inline input box)
    row_index_for_atr = len(symbol) - 1
    atrButton = Button(field, width="10", height="1", text="ATR", bg='#D3D3D3')
    atrButton.grid(row=1, column=10, sticky="ew", padx=5, pady=3)
    atrEnabled.append(False)
    atrButtonList.append(atrButton)
    atrButton['command'] = lambda idx=row_index_for_atr: _toggle_atr_fields(idx)

    # Hidden entry field to store ATR % value (shown only in ATR modal)
    atrEntry = Entry(field, width="0", textvariable=StringVar(field))
    atrEntry.config(width=0)
    atrEntry.grid(row=1, column=10, padx=0, pady=0)
    atrEntry.grid_remove()
    setDefaultAtr(atrEntry)
    # Keep ATR disabled by default; user must toggle ATR button on.
    atrEntry.config(state="disabled")
    atr.append(atrEntry)

    # 12) BREAK EVEN (column 11)
    breakEvenEntry = ttk.Combobox(field, state="readonly", width="10", value=Config.breakEven)
    breakEvenEntry.config(width=10)
    breakEvenEntry.grid(row=1, column=11, sticky="ew", padx=5, pady=3)
    breakEvenEntry.current(0)
    setDefaultbreakEvenEntryType(breakEvenEntry)
    breakEven.append(breakEvenEntry)

    # 13) OPTION (column 12) - use Button (like Replay) so it is always visible in frozen exe
    row_index_for_option = len(symbol) - 1
    optionButton = Button(field, width="10", height="1", text="Option", bg='#D3D3D3')
    optionButton.grid(row=1, column=12, sticky="ew", padx=5, pady=3)
    optionEnabled.append(False)  # Initialize option as disabled
    optionButtonList.append(optionButton)
    optionButton['command'] = lambda idx=row_index_for_option: _toggle_option_fields(idx)
    _od = Config.defaultValue
    _opt_strike = str(_od.get("optStrike") or "").strip()
    _opt_expire_raw = _od.get("optExpire", "")
    _opt_expire = str(_opt_expire_raw).strip() if _opt_expire_raw is not None else ""

    def _norm_opt_ot(key, fallback="Market"):
        v = _od.get(key, fallback)
        if v not in Config.optionOrderTypes:
            return fallback
        return v

    _opt_eot = _norm_opt_ot("optEntryOT")
    _opt_sot = _norm_opt_ot("optSlOT")
    _opt_tot = _norm_opt_ot("optTpOT")
    _opt_risk = _od.get("optRisk")
    _opt_risk_s = "" if _opt_risk is None else str(_opt_risk).strip()

    optionContract.append(StringVar(field, _opt_strike))
    optionExpire.append(StringVar(field, _opt_expire))
    optionEntryOrderType.append(StringVar(field, _opt_eot))
    optionStopLossOrderType.append(StringVar(field, _opt_sot))
    optionProfitOrderType.append(StringVar(field, _opt_tot))
    optionRiskAmount.append(StringVar(field, _opt_risk_s))
    
    # 14) STATUS (column 13)
    statusVar = StringVar(field)
    statusEntry = Entry(field, width="9", textvariable=statusVar)
    statusEntry.config(width=9)
    statusEntry.grid(row=1, column=13, sticky="ew", padx=5, pady=3)
    status.append(statusEntry)
    _set_status(len(status) - 1, initial_status_text)

    # ATR is configured via ATR button modal; value is stored in hidden atr entry


    # Fill full canvas width so the row stretches to the right edge (no dead gap after Status)
    field.pack(side=TOP, pady=8, fill=BOTH, expand=True)



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
    atr[row_index].config(state="disabled")
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
    atr[row_index].config(state="normal")



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
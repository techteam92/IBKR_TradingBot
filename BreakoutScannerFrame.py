"""
UI window for the 5-Bar Consolidation Breakout Scanner.

Opens from the top menu. Lets the user enter symbols, thresholds, optional
filters, then Start/Stop the scanner. Detected signals are appended to a
table inside the window and also logged to IB.log.
"""

import asyncio
import datetime

import Config
from header import *
import BreakoutScanner


scannerFrame = None
_state = {
    'running': False,
    'symbols': [],
    'params': {},
}
_task = None
_connection_ref = None


def _on_close():
    global scannerFrame, _task
    try:
        _state['running'] = False
    except Exception:
        pass
    if _task is not None:
        try:
            _task.cancel()
        except Exception:
            pass
        _task = None
    if scannerFrame is not None:
        try:
            scannerFrame.destroy()
        except Exception:
            pass
        scannerFrame = None


def BreakoutScannerFrame(connection):
    """Open the scanner window (single instance)."""
    global scannerFrame, _connection_ref
    _connection_ref = connection

    if scannerFrame is not None:
        try:
            scannerFrame.lift()
            scannerFrame.focus_force()
            return
        except Exception:
            scannerFrame = None

    logging.info("Open Breakout Scanner")

    scannerFrame = Tk()
    s = ttk.Style(scannerFrame)
    try:
        s.theme_use('clam')
    except Exception:
        pass

    scannerFrame.title('5-Bar Breakout Scanner')
    scannerFrame.protocol("WM_DELETE_WINDOW", _on_close)
    width, height = 820, 560
    pos_x = int((scannerFrame.winfo_screenwidth() / 2) - width / 2)
    pos_y = int((scannerFrame.winfo_screenheight() / 2) - height / 2)
    scannerFrame.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

    title_font = (Config.fontName2, Config.fontSize2 + 2, 'bold')
    body_font = (Config.fontName2, Config.fontSize2)

    Label(scannerFrame, text="5-Bar Consolidation + Breakout Scanner", font=title_font).pack(pady=(10, 2))
    Label(
        scannerFrame,
        text="Detects tight 5-bar bases on 5-min candles and flags breakouts.",
        font=body_font,
        fg="#555555",
    ).pack(pady=(0, 8))

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    form = LabelFrame(scannerFrame, text="Settings", padx=10, pady=8, bd=1, relief=GROOVE)
    form.pack(fill=X, padx=12, pady=4)

    Label(form, text="Symbols (comma separated):", font=body_font).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
    symbols_var = StringVar(value=Config.scanner_default_symbols)
    symbols_entry = Entry(form, textvariable=symbols_var, width=60)
    symbols_entry.grid(row=0, column=1, columnspan=3, sticky="we", pady=3)

    Label(form, text="Timeframe:", font=body_font).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
    Label(form, text=Config.scanner_timeframe + "  (fixed for this strategy)", font=body_font, fg="#555555").grid(row=1, column=1, sticky="w", pady=3)

    Label(form, text="ATR Factor:", font=body_font).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=3)
    atr_var = StringVar(value=str(Config.scanner_atr_factor))
    Entry(form, textvariable=atr_var, width=8).grid(row=2, column=1, sticky="w", pady=3)
    Label(form, text="Body Factor:", font=body_font).grid(row=2, column=2, sticky="w", padx=(20, 8), pady=3)
    body_var = StringVar(value=str(Config.scanner_body_factor))
    Entry(form, textvariable=body_var, width=8).grid(row=2, column=3, sticky="w", pady=3)

    vol_var = BooleanVar(value=Config.scanner_require_volume_decline)
    vwap_var = BooleanVar(value=Config.scanner_require_above_vwap)
    Checkbutton(form, text="Require declining volume", variable=vol_var, font=body_font).grid(
        row=3, column=0, columnspan=2, sticky="w", pady=3)
    Checkbutton(form, text="Require price > VWAP for LONG", variable=vwap_var, font=body_font).grid(
        row=3, column=2, columnspan=2, sticky="w", pady=3)

    # ------------------------------------------------------------------
    # Buttons + status
    # ------------------------------------------------------------------
    btn_row = Frame(scannerFrame)
    btn_row.pack(fill=X, padx=12, pady=(4, 0))

    status_var = StringVar(value="Stopped")
    status_lbl = Label(btn_row, textvariable=status_var, font=body_font, fg="#a00")
    status_lbl.pack(side=RIGHT)

    start_btn = Button(btn_row, text="Start Scanner", width=16)
    start_btn.pack(side=LEFT)
    stop_btn = Button(btn_row, text="Stop", width=10)
    stop_btn.pack(side=LEFT, padx=(8, 0))
    scan_now_btn = Button(btn_row, text="Scan Now", width=10)
    scan_now_btn.pack(side=LEFT, padx=(8, 0))
    clear_btn = Button(btn_row, text="Clear", width=8)
    clear_btn.pack(side=LEFT, padx=(8, 0))

    # ------------------------------------------------------------------
    # Results table
    # ------------------------------------------------------------------
    table_frame = LabelFrame(scannerFrame, text="Detected Signals", padx=6, pady=6, bd=1, relief=GROOVE)
    table_frame.pack(fill=BOTH, expand=True, padx=12, pady=(8, 8))

    cols = ("time", "symbol", "direction", "base_high", "base_low", "breakout_price", "atr14")
    tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=12)
    headings = {
        "time": "Detected At",
        "symbol": "Symbol",
        "direction": "Direction",
        "base_high": "Base High",
        "base_low": "Base Low",
        "breakout_price": "Breakout Price",
        "atr14": "ATR(14)",
    }
    widths = {
        "time": 150, "symbol": 90, "direction": 90,
        "base_high": 110, "base_low": 110, "breakout_price": 130, "atr14": 100,
    }
    for c in cols:
        tree.heading(c, text=headings[c])
        tree.column(c, width=widths[c], anchor="center")
    vscroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vscroll.set)
    tree.pack(side=LEFT, fill=BOTH, expand=True)
    vscroll.pack(side=RIGHT, fill=Y)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _parse_symbols():
        raw = symbols_var.get() or ""
        out = []
        for part in raw.replace(';', ',').split(','):
            s = part.strip().upper()
            if s:
                out.append(s)
        return out

    def _read_params():
        try:
            atr_f = float(atr_var.get())
        except Exception:
            atr_f = Config.scanner_atr_factor
        try:
            body_f = float(body_var.get())
        except Exception:
            body_f = Config.scanner_body_factor
        return {
            'atr_factor': atr_f,
            'body_factor': body_f,
            'require_volume_decline': bool(vol_var.get()),
            'require_above_vwap': bool(vwap_var.get()),
        }

    def _set_status(text, color="#a00"):
        try:
            status_var.set(text)
            status_lbl.configure(fg=color)
        except Exception:
            pass

    def _on_signal(sig):
        # Called from the asyncio loop; Tk is driven by _tkLoop in the same loop,
        # so direct widget updates are safe here.
        try:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tree.insert(
                "", 0, values=(
                    now_str,
                    sig.get('symbol', ''),
                    sig.get('direction', ''),
                    sig.get('base_high', ''),
                    sig.get('base_low', ''),
                    sig.get('breakout_price', ''),
                    sig.get('atr14', ''),
                ),
            )
        except Exception as e:
            logging.warning("Scanner UI insert failed: %s", e)

    def _get_state():
        return _state

    def _start():
        global _task
        if _state['running']:
            return
        symbols = _parse_symbols()
        if not symbols:
            tkinter.messagebox.showwarning('Scanner', 'Enter at least one symbol.')
            return
        _state['symbols'] = symbols
        _state['params'] = _read_params()
        _state['running'] = True
        _set_status("Running", color="#0a7d00")
        try:
            _task = asyncio.ensure_future(
                BreakoutScanner.scanner_loop(_connection_ref, _get_state, _on_signal)
            )
        except Exception as e:
            _state['running'] = False
            _set_status("Stopped", color="#a00")
            logging.error("Scanner: failed to start loop: %s", e)
            tkinter.messagebox.showerror('Scanner', f'Failed to start: {e}')

    def _stop():
        global _task
        _state['running'] = False
        if _task is not None:
            try:
                _task.cancel()
            except Exception:
                pass
            _task = None
        _set_status("Stopped", color="#a00")

    def _scan_now():
        # Single one-shot pass across the current symbols using current params,
        # independent of whether the loop is running.
        symbols = _parse_symbols()
        if not symbols:
            tkinter.messagebox.showwarning('Scanner', 'Enter at least one symbol.')
            return
        params = _read_params()

        async def _once():
            for sym in symbols:
                await BreakoutScanner.scan_once(_connection_ref, sym, params, _on_signal)
                await asyncio.sleep(0.3)
        try:
            asyncio.ensure_future(_once())
        except Exception as e:
            logging.error("Scanner: scan_now failed: %s", e)

    def _clear():
        for iid in tree.get_children():
            try:
                tree.delete(iid)
            except Exception:
                pass

    start_btn.configure(command=_start)
    stop_btn.configure(command=_stop)
    scan_now_btn.configure(command=_scan_now)
    clear_btn.configure(command=_clear)

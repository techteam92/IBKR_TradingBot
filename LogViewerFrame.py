"""
Log Viewer window for IB.log.

- Live tail of IB.log (refreshes every ~500ms).
- Filters: free-text search, log level checkboxes, function / module name,
  and a symbol substring. All filters AND together.
- Color tags per level (ERROR red, WARNING orange, DEBUG gray, INFO black).
- Pause / Resume, Auto-scroll, Clear, Reload-all, Save Filtered.
- No edits to existing logging or strategy logic. Read-only on IB.log.
"""

import os
import re

import Config
from header import *


LOG_PATH = 'IB.log'
MAX_BUFFER = 5000          # cap on lines kept in memory
TICK_MS = 500              # refresh cadence

logViewerFrame = None
_after_id = None

# Pre-compiled parser for "TS  - module - func - line - LEVEL - message".
_LINE_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3})\s+-\s+'
    r'(?P<mod>\S+)\s+-\s+(?P<func>\S+)\s+-\s+(?P<lineno>\d+)\s+-\s+'
    r'(?P<level>\w+)\s+-\s+(?P<msg>.*)$'
)

_LEVEL_COLORS = {
    'ERROR': '#b40000',
    'WARNING': '#b76e00',
    'INFO': '#222222',
    'DEBUG': '#888888',
    'CRITICAL': '#b40000',
}


def _on_close():
    global logViewerFrame, _after_id
    try:
        if _after_id is not None and logViewerFrame is not None:
            try:
                logViewerFrame.after_cancel(_after_id)
            except Exception:
                pass
        _after_id = None
    except Exception:
        pass
    if logViewerFrame is not None:
        try:
            logViewerFrame.destroy()
        except Exception:
            pass
        logViewerFrame = None


def LogViewerFrame():
    """Open the Log Viewer (single-instance)."""
    global logViewerFrame, _after_id

    if logViewerFrame is not None:
        try:
            logViewerFrame.lift()
            logViewerFrame.focus_force()
            return
        except Exception:
            logViewerFrame = None

    logging.info("Open Log Viewer")

    logViewerFrame = Tk()
    s = ttk.Style(logViewerFrame)
    try:
        s.theme_use('clam')
    except Exception:
        pass
    logViewerFrame.title('Log Viewer - IB.log')
    logViewerFrame.protocol("WM_DELETE_WINDOW", _on_close)

    width, height = 1100, 640
    pos_x = int((logViewerFrame.winfo_screenwidth() / 2) - width / 2)
    pos_y = int((logViewerFrame.winfo_screenheight() / 2) - height / 2)
    logViewerFrame.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

    title_font = (Config.fontName2, Config.fontSize2 + 2, 'bold')
    body_font = (Config.fontName2, Config.fontSize2)
    mono_font = ('Consolas', max(10, Config.fontSize2 - 1))

    Label(logViewerFrame, text="IB.log - Live Filter", font=title_font).pack(pady=(8, 2))

    # --------------------------------------------------------------
    # State
    # --------------------------------------------------------------
    state = {
        'lines': [],            # full parsed lines kept in memory (raw text + parsed)
        'paused': False,
        'autoscroll': True,
        'last_size': 0,         # bytes already read from file
        'truncated_warned': False,
    }

    # --------------------------------------------------------------
    # Filter inputs
    # --------------------------------------------------------------
    filt = LabelFrame(logViewerFrame, text="Filters", padx=8, pady=6, bd=1, relief=GROOVE)
    filt.pack(fill=X, padx=10, pady=4)

    # Row 1
    r1 = Frame(filt)
    r1.pack(fill=X, pady=2)
    Label(r1, text="Search:", font=body_font, width=10, anchor='w').pack(side=LEFT)
    search_var = StringVar(value="")
    Entry(r1, textvariable=search_var, font=body_font, width=40).pack(side=LEFT, padx=(0, 12))

    Label(r1, text="Symbol:", font=body_font, width=8, anchor='w').pack(side=LEFT)
    symbol_var = StringVar(value="")
    Entry(r1, textvariable=symbol_var, font=body_font, width=10).pack(side=LEFT, padx=(0, 12))

    Label(r1, text="Function:", font=body_font, width=9, anchor='w').pack(side=LEFT)
    func_var = StringVar(value="")
    Entry(r1, textvariable=func_var, font=body_font, width=22).pack(side=LEFT)

    # Row 2
    r2 = Frame(filt)
    r2.pack(fill=X, pady=2)
    Label(r2, text="Levels:", font=body_font, width=10, anchor='w').pack(side=LEFT)
    lvl_vars = {
        'INFO': BooleanVar(value=True),
        'WARNING': BooleanVar(value=True),
        'ERROR': BooleanVar(value=True),
        'DEBUG': BooleanVar(value=False),
    }
    for lvl in ('INFO', 'WARNING', 'ERROR', 'DEBUG'):
        Checkbutton(r2, text=lvl, variable=lvl_vars[lvl], font=body_font).pack(side=LEFT, padx=(0, 6))

    pause_var = BooleanVar(value=False)
    autoscroll_var = BooleanVar(value=True)
    Checkbutton(r2, text="Pause", variable=pause_var, font=body_font).pack(side=LEFT, padx=(20, 6))
    Checkbutton(r2, text="Auto-scroll", variable=autoscroll_var, font=body_font).pack(side=LEFT, padx=(0, 6))

    # Buttons
    btn_row = Frame(filt)
    btn_row.pack(fill=X, pady=(4, 0))
    apply_btn = Button(btn_row, text="Apply", width=10)
    apply_btn.pack(side=LEFT)
    clear_btn = Button(btn_row, text="Clear Display", width=12)
    clear_btn.pack(side=LEFT, padx=(8, 0))
    reload_btn = Button(btn_row, text="Reload All", width=10)
    reload_btn.pack(side=LEFT, padx=(8, 0))
    save_btn = Button(btn_row, text="Save Filtered…", width=14)
    save_btn.pack(side=LEFT, padx=(8, 0))

    counter_var = StringVar(value="0 / 0 lines shown")
    Label(btn_row, textvariable=counter_var, font=body_font, fg="#555").pack(side=RIGHT)

    # --------------------------------------------------------------
    # Text view
    # --------------------------------------------------------------
    text_frame = Frame(logViewerFrame)
    text_frame.pack(fill=BOTH, expand=True, padx=10, pady=(4, 10))

    text = Text(text_frame, wrap='none', font=mono_font, bg='#fafafa', fg='#222')
    yscroll = ttk.Scrollbar(text_frame, orient='vertical', command=text.yview)
    xscroll = ttk.Scrollbar(text_frame, orient='horizontal', command=text.xview)
    text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set, state='disabled')
    text.grid(row=0, column=0, sticky='nsew')
    yscroll.grid(row=0, column=1, sticky='ns')
    xscroll.grid(row=1, column=0, sticky='ew')
    text_frame.rowconfigure(0, weight=1)
    text_frame.columnconfigure(0, weight=1)

    for lvl, color in _LEVEL_COLORS.items():
        text.tag_configure(f'lvl_{lvl}', foreground=color)
    text.tag_configure('hit', background='#fff39a')

    # --------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------
    def _parse_line(raw):
        m = _LINE_RE.match(raw.rstrip('\n'))
        if not m:
            return {'raw': raw, 'level': None, 'func': None, 'mod': None,
                    'msg': raw.rstrip('\n'), 'parsed': False}
        d = m.groupdict()
        d['raw'] = raw
        d['parsed'] = True
        return d

    def _line_passes(d, search, symbol, func_q, levels_on):
        if d.get('parsed'):
            lvl = d.get('level') or ''
            if levels_on and lvl in levels_on and not levels_on.get(lvl, True):
                return False
            if func_q and func_q not in (d.get('func') or '').lower():
                return False
        # Apply text filters to full raw line so multiline/continuation are still searchable.
        raw_low = d['raw'].lower()
        if search and search not in raw_low:
            return False
        if symbol and symbol not in raw_low:
            return False
        return True

    def _current_filters():
        search = (search_var.get() or '').strip().lower()
        symbol = (symbol_var.get() or '').strip().lower()
        func_q = (func_var.get() or '').strip().lower()
        levels_on = {lvl: bool(v.get()) for lvl, v in lvl_vars.items()}
        return search, symbol, func_q, levels_on

    def _render(lines_subset):
        text.configure(state='normal')
        text.delete('1.0', 'end')
        search = (search_var.get() or '').strip().lower()
        for d in lines_subset:
            lvl = d.get('level') or 'INFO'
            tag = f'lvl_{lvl}' if lvl in _LEVEL_COLORS else 'lvl_INFO'
            start_idx = text.index('end-1c')
            text.insert('end', d['raw'] if d['raw'].endswith('\n') else d['raw'] + '\n', tag)
            if search:
                line_text = d['raw']
                low = line_text.lower()
                # Highlight matches within the just-inserted line.
                idx = 0
                while True:
                    p = low.find(search, idx)
                    if p == -1:
                        break
                    s = f"{start_idx}+{p}c"
                    e = f"{start_idx}+{p + len(search)}c"
                    text.tag_add('hit', s, e)
                    idx = p + len(search)
        if autoscroll_var.get() and not pause_var.get():
            text.see('end')
        text.configure(state='disabled')

    def _apply_filter():
        search, symbol, func_q, levels_on = _current_filters()
        subset = [d for d in state['lines']
                  if _line_passes(d, search, symbol, func_q, levels_on)]
        _render(subset)
        counter_var.set(f"{len(subset)} / {len(state['lines'])} lines shown")

    def _append_new_lines(new_text):
        if not new_text:
            return
        # Split keeping trailing newline per line.
        for raw in new_text.splitlines(keepends=True):
            d = _parse_line(raw)
            state['lines'].append(d)
        if len(state['lines']) > MAX_BUFFER:
            # Trim from the front; rendering will re-build display from buffer.
            state['lines'] = state['lines'][-MAX_BUFFER:]

    def _read_initial():
        state['lines'] = []
        state['last_size'] = 0
        if not os.path.exists(LOG_PATH):
            counter_var.set("IB.log not found")
            return
        try:
            size = os.path.getsize(LOG_PATH)
        except Exception:
            size = 0
        # On first load, read only the tail (last ~MAX_BUFFER lines).
        try:
            with open(LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
                # Read everything; we'll trim.
                data = f.read()
                state['last_size'] = size
        except Exception as e:
            logging.warning("LogViewer: initial read failed: %s", e)
            return
        all_lines = data.splitlines(keepends=True)
        if len(all_lines) > MAX_BUFFER:
            all_lines = all_lines[-MAX_BUFFER:]
        for raw in all_lines:
            d = _parse_line(raw)
            state['lines'].append(d)
        _apply_filter()

    def _tick():
        global _after_id
        try:
            if pause_var.get():
                return
            if not os.path.exists(LOG_PATH):
                return
            try:
                cur_size = os.path.getsize(LOG_PATH)
            except Exception:
                return

            if cur_size < state['last_size']:
                # Log rotated / truncated - read from start.
                if not state['truncated_warned']:
                    logging.info("LogViewer: detected log truncation/rotation, reloading.")
                    state['truncated_warned'] = True
                _read_initial()
                return
            if cur_size == state['last_size']:
                return

            # Read only the new bytes since last position.
            try:
                with open(LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(state['last_size'])
                    new_text = f.read()
                state['last_size'] = cur_size
            except Exception as e:
                logging.debug("LogViewer: tick read failed: %s", e)
                return

            if new_text:
                _append_new_lines(new_text)
                _apply_filter()
                state['truncated_warned'] = False
        finally:
            # Reschedule
            try:
                _after_id = logViewerFrame.after(TICK_MS, _tick)
            except Exception:
                pass

    # --------------------------------------------------------------
    # Wire buttons / events
    # --------------------------------------------------------------
    def _on_apply():
        _apply_filter()

    def _on_clear():
        text.configure(state='normal')
        text.delete('1.0', 'end')
        text.configure(state='disabled')
        counter_var.set(f"0 / {len(state['lines'])} lines shown")

    def _on_reload():
        _read_initial()

    def _on_save():
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                title="Save Filtered Log",
                defaultextension=".log",
                filetypes=[("Log files", "*.log"), ("All files", "*.*")],
                initialfile="IB.filtered.log",
            )
            if not path:
                return
            search, symbol, func_q, levels_on = _current_filters()
            count = 0
            with open(path, 'w', encoding='utf-8') as fout:
                for d in state['lines']:
                    if _line_passes(d, search, symbol, func_q, levels_on):
                        fout.write(d['raw'] if d['raw'].endswith('\n') else d['raw'] + '\n')
                        count += 1
            tkinter.messagebox.showinfo("Saved", f"{count} lines saved to:\n{path}")
        except Exception as e:
            tkinter.messagebox.showerror("Save Failed", str(e))

    apply_btn.configure(command=_on_apply)
    clear_btn.configure(command=_on_clear)
    reload_btn.configure(command=_on_reload)
    save_btn.configure(command=_on_save)

    # Live filter as user types (debounced via _apply_filter directly).
    search_var.trace_add('write', lambda *a: _apply_filter())
    symbol_var.trace_add('write', lambda *a: _apply_filter())
    func_var.trace_add('write', lambda *a: _apply_filter())
    for v in lvl_vars.values():
        v.trace_add('write', lambda *a: _apply_filter())

    # Enter in any text entry triggers apply (no-op since we trace, but feels right).
    logViewerFrame.bind('<Return>', lambda e: _apply_filter())

    # Initial load + start tick.
    _read_initial()
    _after_id = logViewerFrame.after(TICK_MS, _tick)

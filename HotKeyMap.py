import Config
from header import *

hotKeyFrame = None


def _on_close():
    global hotKeyFrame
    if hotKeyFrame is not None:
        try:
            hotKeyFrame.destroy()
        except Exception:
            pass
        hotKeyFrame = None


def HotKeyMap():
    """Open a popup window listing all keyboard shortcuts.

    Settings apply to the active (last) trade row, matching `_setup_hotkeys`
    in NewTradeFrame.py. Use this as a quick reference for users.
    """
    global hotKeyFrame
    if hotKeyFrame is not None:
        try:
            hotKeyFrame.lift()
            hotKeyFrame.focus_force()
            return
        except Exception:
            hotKeyFrame = None

    logging.info("Open Hot Key Map")

    hotKeyFrame = Tk()
    s = ttk.Style(hotKeyFrame)
    try:
        s.theme_use('clam')
    except Exception:
        pass

    hotKeyFrame.title('Hot Keys')
    hotKeyFrame.protocol("WM_DELETE_WINDOW", _on_close)

    width, height = 520, 560
    pos_x = int((hotKeyFrame.winfo_screenwidth() / 2) - 250)
    pos_y = int((hotKeyFrame.winfo_screenheight() / 2) - 280)
    hotKeyFrame.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
    hotKeyFrame.attributes('-topmost', True)

    title_font = (Config.fontName2, Config.fontSize2 + 2, 'bold')
    section_font = (Config.fontName2, Config.fontSize2, 'bold')
    body_font = (Config.fontName2, Config.fontSize2)

    Label(hotKeyFrame, text="Keyboard Shortcuts", font=title_font).pack(pady=(10, 2))
    Label(
        hotKeyFrame,
        text="(applies to the active / last trade row)",
        font=body_font,
        fg="#555555",
    ).pack(pady=(0, 8))

    container = Frame(hotKeyFrame)
    container.pack(fill=BOTH, expand=True, padx=12, pady=4)

    canvas = Canvas(container, borderwidth=0, highlightthickness=0)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    inner = Frame(canvas)

    inner.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=LEFT, fill=BOTH, expand=True)
    scrollbar.pack(side=RIGHT, fill=Y)

    def _on_mousewheel(event):
        try:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    sections = [
        (
            "Execute",
            [("Shift + Enter", "Execute active row")],
        ),
        (
            "Stop Loss",
            [
                ("Shift + C", "Custom"),
                ("Shift + L", "LOD"),
                ("Shift + H", "HOD"),
                ("Shift + 0", "10% ATR"),
                ("Shift + 1", "15% ATR"),
                ("Shift + 2", "20% ATR"),
                ("Shift + 3", "25% ATR"),
                ("Shift + 4", "33% ATR"),
            ],
        ),
        (
            "Trade Type",
            [
                ("Alt + C", "Custom (entry)"),
                ("Alt + A", "ASK + 1/2"),
                ("Alt + B", "BID - 1/2"),
                ("Alt + F", "FB"),
                ("Alt + L", "LB"),
                ("Alt + R", "RBB"),
                ("Alt + P", "PBe1"),
                ("Ctrl + P", "PBe2"),
                ("Ctrl + L", "Limit Order"),
                ("Ctrl + C", "Conditional Order"),
            ],
        ),
        (
            "Buy / Sell",
            [
                ("Ctrl + B", "BUY"),
                ("Ctrl + S", "SELL"),
            ],
        ),
        (
            "Time Frame",
            [
                ("Ctrl + 1", "1 min"),
                ("Ctrl + 2", "2 mins"),
                ("Ctrl + 3", "3 mins"),
                ("Ctrl + 5", "5 mins"),
                ("Ctrl + 6", "10 mins"),
                ("Ctrl + 7", "15 mins"),
            ],
        ),
        (
            "Profit",
            [
                ("Alt + 1", "1:1"),
                ("Alt + 2", "2:1"),
                ("Alt + 3", "3:1"),
            ],
        ),
        (
            "Time In Force",
            [
                ("Shift + O", "OTH"),
                ("Shift + D", "DAY"),
                ("—", "OTH-1: next 04:00 ET premarket (overnight only)"),
            ],
        ),
    ]

    for section_title, rows in sections:
        section_frame = LabelFrame(
            inner,
            text=section_title,
            font=section_font,
            padx=10,
            pady=6,
            bd=1,
            relief=GROOVE,
        )
        section_frame.pack(fill=X, padx=4, pady=6)

        for r, (key, desc) in enumerate(rows):
            Label(
                section_frame,
                text=key,
                font=body_font,
                width=14,
                anchor="w",
                fg="#1f4e79",
            ).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=1)
            Label(
                section_frame,
                text=desc,
                font=body_font,
                anchor="w",
            ).grid(row=r, column=1, sticky="w", pady=1)

    btn_row = Frame(hotKeyFrame)
    btn_row.pack(side=BOTTOM, pady=8)
    Button(btn_row, text="Close", width=12, command=_on_close).pack()

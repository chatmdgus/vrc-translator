"""테마에 맞춘 공용 위젯 (설정 창, 메인 창이 같이 쓴다)."""
import tkinter as tk
from tkinter import ttk

from .theme import FONT, P, mix

BG = P["night"]
PANEL = P["band"]
FIELD = mix(P["band"], P["night"], 0.5)
MUTED = mix(P["dim"], P["night"], 0.45)


def setup_style(root):
    """ttk 위젯(콤보박스, 스크롤바)을 테마 색으로."""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure("Theme.TCombobox", fieldbackground=FIELD, background=PANEL, foreground=P["text"],
                 arrowcolor=P["corona"], bordercolor=P["rim"], lightcolor=P["rim"],
                 darkcolor=P["rim"], selectbackground=FIELD, selectforeground=P["text"],
                 padding=4)
    st.map("Theme.TCombobox",
           fieldbackground=[("readonly", FIELD), ("disabled", PANEL)],
           foreground=[("disabled", MUTED)],
           bordercolor=[("focus", P["corona"]), ("hover", mix(P["rim"], P["corona"], 0.5))],
           arrowcolor=[("disabled", MUTED)])
    # 콤보박스를 펼쳤을 때 목록
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", P["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", P["corona"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    st.configure("Theme.Vertical.TScrollbar", gripcount=0, background=P["rim"],
                 troughcolor=BG, bordercolor=BG, lightcolor=P["rim"], darkcolor=P["rim"],
                 arrowcolor=P["dim"], arrowsize=11, relief="flat")
    st.map("Theme.Vertical.TScrollbar",
           background=[("active", mix(P["rim"], P["corona"], 0.45))])
    return st


class Pill(tk.Label):
    """알약 모양 버튼. toggle=True면 켜기/끄기, check=True면 앞에 ●/○ 표시."""

    def __init__(self, master, text, command=None, toggle=False, value=False, bg=PANEL,
                 check=False, size=9):
        super().__init__(master, font=(FONT, size, "bold"), padx=10, pady=4,
                         cursor="hand2", bd=0, highlightthickness=1)
        self.label = text
        self.toggle, self.value, self.command, self.base = toggle, value, command, bg
        self.check = check
        self.hover = False
        self.enabled = True
        self.selected = False          # Segmented 안에서 쓸 때
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Button-1>", self._click)
        self._paint()

    def _hover(self, on):
        self.hover = on
        self._paint()

    def _click(self, _e):
        if not self.enabled:
            return
        if self.toggle:
            self.value = not self.value
            self._paint()
            if self.command:
                self.command(self.value)
        elif self.command:
            self.command()

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self._paint()

    def set_text(self, text):
        self.label = text
        self._paint()

    def set_enabled(self, on):
        self.enabled = on
        self.config(cursor="hand2" if on else "arrow")
        self._paint()

    def _paint(self):
        on = (self.toggle and self.value) or self.selected
        text = self.label
        if self.check:
            text = ("● " if self.value else "○ ") + text
        if not self.enabled:
            bg, fg, bd = self.base, mix(P["rim"], P["dim"], 0.35), P["rim"]
        elif on:
            bg = mix(self.base, P["corona"], 0.32 if self.hover else 0.22)
            fg, bd = "#FFFFFF", P["corona"]
        else:
            bg = mix(self.base, P["rim"], 0.9 if self.hover else 0.4)
            fg, bd = P["dim"], P["rim"]
        self.config(text=text, bg=bg, fg=fg, highlightbackground=bd, highlightcolor=bd)


class Segmented(tk.Frame):
    """여러 개 중 하나를 고르는 버튼 묶음."""

    def __init__(self, master, options, value=None, command=None, bg=PANEL):
        super().__init__(master, bg=bg)
        self.base = bg
        self.command = command
        self.pills = {}
        self.value = None
        self.enabled = True
        self.set_options(options, value)

    def set_options(self, options, value=None):
        for p in self.pills.values():
            p.destroy()
        self.pills = {}
        for label, val in options:
            p = Pill(self, label, command=lambda v=val: self._pick(v), bg=self.base)
            p.pack(side="left", padx=(0, 4))
            self.pills[val] = p
        vals = [v for _, v in options]
        self.set(value if value in vals else (vals[0] if vals else None))
        self.set_enabled(self.enabled)

    def _pick(self, v):
        if not self.enabled:
            return
        self.set(v)
        if self.command:
            self.command(v)

    def get(self):
        return self.value

    def set(self, v):
        self.value = v
        for val, p in self.pills.items():
            p.selected = val == v
            p._paint()

    def set_enabled(self, on):
        self.enabled = on
        for p in self.pills.values():
            p.set_enabled(on)


class Card(tk.Frame):
    """제목이 있는 패널. .body 에 내용을 넣는다. .head_right 에 오른쪽 위 위젯."""

    def __init__(self, master, title, subtitle=None):
        super().__init__(master, bg=PANEL, highlightthickness=1, highlightbackground=P["rim"])
        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x", padx=14, pady=(12, 4))
        tk.Frame(head, bg=P["corona"], width=4, height=16).pack(side="left", padx=(0, 8))
        tk.Label(head, text=title, bg=PANEL, fg=P["text"], font=(FONT, 11, "bold")).pack(side="left")
        if subtitle:
            tk.Label(head, text=subtitle, bg=PANEL, fg=MUTED, font=(FONT, 8)).pack(
                side="left", padx=(8, 0))
        self.head_right = tk.Frame(head, bg=PANEL)
        self.head_right.pack(side="right")
        self.body = tk.Frame(self, bg=PANEL)
        self.body.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        self.body.columnconfigure(1, weight=1)
        self._row = 0

    def row(self, label, widget_factory, hint=None):
        """왼쪽에 라벨, 오른쪽에 위젯 한 줄. widget_factory(parent) → 위젯."""
        tk.Label(self.body, text=label, bg=PANEL, fg=P["dim"], font=(FONT, 9),
                 anchor="w").grid(row=self._row, column=0, sticky="nw", pady=5, padx=(0, 12))
        w = widget_factory(self.body)
        w.grid(row=self._row, column=1, sticky="w", pady=4)
        self._row += 1
        h = None
        if hint is not None:
            h = tk.Label(self.body, text=hint, bg=PANEL, fg=MUTED, font=(FONT, 8), anchor="w",
                         justify="left")
            h.grid(row=self._row, column=1, sticky="w", pady=(0, 4))
            self._row += 1
        return w, h

    def full(self, widget_factory, pady=4):
        """라벨 없이 가로 전체."""
        w = widget_factory(self.body)
        w.grid(row=self._row, column=0, columnspan=2, sticky="w", pady=pady)
        self._row += 1
        return w


def entry(master, var, show=None, width=34):
    return tk.Entry(master, textvariable=var, show=show, width=width, bg=FIELD, fg=P["text"],
                    insertbackground=P["text"], relief="flat", font=(FONT, 10),
                    highlightthickness=1, highlightbackground=P["rim"], highlightcolor=P["corona"],
                    disabledbackground=PANEL)


def combo(master, values, width=34):
    return ttk.Combobox(master, state="readonly", values=values, width=width,
                        style="Theme.TCombobox", font=(FONT, 10))


class PrimaryButton(tk.Label):
    """빨간 큰 버튼 (시작)."""

    def __init__(self, master, text, command, bg=BG):
        super().__init__(master, text=text, font=(FONT, 12, "bold"), padx=34, pady=9,
                         cursor="hand2", fg="#FFFFFF", bd=0, highlightthickness=0)
        self.command = command
        self.enabled = True
        self.hover = False
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        self.bind("<Button-1>", lambda e: self.enabled and self.command())
        self._paint()

    def _h(self, on):
        self.hover = on
        self._paint()

    def set_enabled(self, on, text=None):
        self.enabled = on
        if text:
            self.config(text=text)
        self.config(cursor="hand2" if on else "watch")
        self._paint()

    def _paint(self):
        if not self.enabled:
            bg = mix(P["corona"], P["night"], 0.55)
        else:
            bg = mix(P["corona"], "#FFFFFF", 0.12) if self.hover else P["corona"]
        self.config(bg=bg)

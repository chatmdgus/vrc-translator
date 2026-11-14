"""자막 표시: 데스크톱 창(tkinter) 또는 SteamVR 오버레이(HMD 고정)."""
import threading
import time
from collections import deque

from .theme import P as THEME
from .theme import rgba


class SubtitleBoard:
    """번역 결과를 모아두는 스레드 안전 버퍼.
    항목: [시각, 원문, 번역|None, 원문언어, 번역언어, id, 번역 대기 중?]"""

    def __init__(self, max_lines=4, timeout=10):
        self.lock = threading.Lock()
        self.items = deque(maxlen=max_lines)
        self.timeout = timeout
        self.version = 0

    def push(self, original, translated, lang, target, uid=None, pending=False):
        with self.lock:
            self.items.append([time.time(), original, translated, lang, target, uid, pending])
            self.version += 1

    def update(self, uid, translated):
        """원문만 먼저 띄워둔 항목에 번역을 채운다 (실패하면 translated=None → 원문만 남김)."""
        with self.lock:
            for it in self.items:
                if it[5] == uid:
                    it[0], it[2], it[6] = time.time(), translated, False
                    self.version += 1
                    return

    def snapshot(self):
        now = time.time()
        with self.lock:
            while self.items and now - self.items[0][0] > self.timeout:
                self.items.popleft()
                self.version += 1
            return self.version, [list(it) for it in self.items]


def _lines_of(item, show_original):
    """(텍스트, 언어, 보조줄 여부) 목록."""
    orig, trans, lang, tgt = item[1], item[2], item[3], item[4]
    pending = len(item) > 6 and item[6]
    if pending:
        return [(orig, lang, True), ("…", tgt, False)]
    if trans is None:
        return [(orig, lang, False)]
    out = [(orig, lang, True)] if show_original else []
    out.append((trans, tgt, False))
    return out


# ---------------------------------------------------------------- 데스크톱

class DesktopSubtitles:
    """항상 위에 떠 있는 자막 창 (메인 창의 Toplevel). 드래그로 이동, 우클릭으로 숨김."""

    BG = THEME["night"]
    FG = THEME["text"]
    SUB = THEME["dim"]
    RIM = THEME["rim"]

    def __init__(self, master, board, cfg, show_original):
        import tkinter as tk
        self.tk = tk
        self.board = board
        self.show_original = show_original
        self.width = int(cfg.get("desktop_width", 900))
        size = int(cfg.get("font_size", 28))
        self.f_main = ("Malgun Gothic", size, "bold")
        self.f_sub = ("Malgun Gothic", max(10, int(size * 0.6)))
        self.visible = True
        self.on_hide = None
        self.on_move = None

        self.win = tk.Toplevel(master)
        self.win.title("VRC Translator 자막")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.0)
        self.win.configure(bg=self.BG, highlightthickness=1, highlightbackground=self.RIM)
        tk.Frame(self.win, bg=THEME["corona2"], height=3).pack(fill="x")   # 위쪽 빨간 띠
        self.frame = tk.Frame(self.win, bg=self.BG, padx=16, pady=10)
        self.frame.pack(fill="both", expand=True)

        self.pos = {"x": (self.win.winfo_screenwidth() - self.width) // 2,
                    "bottom": self.win.winfo_screenheight() - 120}
        self._drag = {}
        self._bind(self.win)
        self._bind(self.frame)
        self._v = -1
        self._tick()

    def _bind(self, w):
        w.bind("<Button-1>", self._start_drag)
        w.bind("<B1-Motion>", self._on_drag)
        w.bind("<ButtonRelease-1>", lambda e: self.on_move and self.on_move(dict(self.pos)))
        w.bind("<Button-3>", lambda e: self.set_visible(False))

    def _start_drag(self, e):
        self._drag.update(x=e.x_root, y=e.y_root, wx=self.pos["x"], wb=self.pos["bottom"])

    def _on_drag(self, e):
        self.pos["x"] = self._drag["wx"] + e.x_root - self._drag["x"]
        self.pos["bottom"] = self._drag["wb"] + e.y_root - self._drag["y"]
        self._place()

    def _place(self):
        self.win.update_idletasks()
        h = self.win.winfo_reqheight()
        self.win.geometry(f"{self.width}x{h}+{self.pos['x']}+{self.pos['bottom'] - h}")

    def set_position(self, pos):
        """저장된 위치로 옮긴다 (화면 밖이면 무시)."""
        try:
            x, b = int(pos["x"]), int(pos["bottom"])
        except (TypeError, KeyError, ValueError):
            return
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        if -self.width // 2 < x < sw - 50 and 50 < b <= sh + 20:
            self.pos.update(x=x, bottom=b)
            self._place()

    def set_visible(self, on):
        self.visible = on
        self._v = -1          # 다음 tick에서 다시 그림
        if not on and self.on_hide:
            self.on_hide()

    def _tick(self):
        v, items = self.board.snapshot()
        if v != self._v:
            self._v = v
            for c in self.frame.winfo_children():
                c.destroy()
            if items and self.visible:
                for item in items:
                    for text, _, sub in _lines_of(item, self.show_original):
                        lbl = self.tk.Label(self.frame, text=text, bg=self.BG, anchor="w",
                                            justify="left", fg=self.SUB if sub else self.FG,
                                            font=self.f_sub if sub else self.f_main,
                                            wraplength=self.width - 40)
                        lbl.pack(fill="x", pady=(4, 0) if sub else (0, 4))
                        self._bind(lbl)
                self.win.attributes("-alpha", 0.85)
            else:
                self.win.attributes("-alpha", 0.0)
            self._place()
        self.win.after(150, self._tick)


# ---------------------------------------------------------------- VR

class SubtitleRenderer:
    """자막을 RGBA 이미지로 그림 (언어별 폰트, CJK 글자 단위 줄바꿈)."""

    def __init__(self, fonts, size=28, width=1024, height=440):
        self.fonts = fonts or {}
        self.size = size * 2          # VR은 해상도를 두 배로
        self.width, self.height = width, height
        self.cache = {}

    def font(self, lang, size):
        from PIL import ImageFont
        path = self.fonts.get(lang) or self.fonts.get("default")
        key = (path, size)
        if key not in self.cache:
            try:
                self.cache[key] = ImageFont.truetype(path, size)
            except (OSError, TypeError):
                print(f"[overlay] 폰트를 열 수 없음: {path}")
                self.cache[key] = ImageFont.load_default(size)
        return self.cache[key]

    @staticmethod
    def wrap(text, font, maxw):
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if font.getlength(cur + ch) <= maxw:
                cur += ch
                continue
            sp = cur.rfind(" ")
            if sp > len(cur) // 2:          # 띄어쓰기 언어는 단어 단위로
                lines.append(cur[:sp])
                cur = cur[sp + 1:] + ch
            else:                           # 일본어 등은 글자 단위로
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return [l for l in lines if l.strip()]

    def render(self, items, show_original):
        from PIL import Image, ImageDraw
        pad = 24
        maxw = self.width - pad * 2
        sub_size = int(self.size * 0.6)
        rows = []   # (텍스트, 폰트, 색, 높이)
        for item in items:
            for text, lang, sub in _lines_of(item, show_original):
                sz = sub_size if sub else self.size
                f = self.font(lang, sz)
                color = rgba(THEME["dim"], 255) if sub else rgba(THEME["text"], 255)
                for line in self.wrap(text, f, maxw):
                    rows.append((line, f, color, int(sz * 1.3)))
            rows.append(("", None, None, int(self.size * 0.3)))
        if rows:
            rows.pop()

        content_h = sum(r[3] for r in rows) + pad * 2
        # 넘치면 최신(아래쪽)만 남김
        while content_h > self.height and len(rows) > 1:
            content_h -= rows.pop(0)[3]

        img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        top = self.height - content_h
        d.rounded_rectangle((0, top, self.width - 1, self.height - 1), radius=24,
                            fill=rgba(THEME["night"], 210), outline=rgba(THEME["rim"], 255), width=2)
        d.rectangle((24, top, self.width - 25, top + 4), fill=rgba(THEME["corona2"], 255))
        y = top + pad
        for text, f, color, h in rows:
            if f is not None:
                d.text((pad, y), text, font=f, fill=color,
                       stroke_width=2, stroke_fill=(0, 0, 0, 255))
            y += h
        return img


def run_vr(board, cfg, show_original):
    import ctypes
    import openvr

    openvr.init(openvr.VRApplication_Overlay)
    ov = openvr.IVROverlay()
    handle = ov.createOverlay("vrc_translator.subtitles", "VRC Translator")
    ov.setOverlayWidthInMeters(handle, float(cfg.get("vr_width_m", 0.9)))

    x, y, z = cfg.get("vr_offset", [0.0, -0.35, -1.2])
    t = openvr.HmdMatrix34_t()
    t.m[0][0] = t.m[1][1] = t.m[2][2] = 1.0
    t.m[0][3], t.m[1][3], t.m[2][3] = float(x), float(y), float(z)
    ov.setOverlayTransformTrackedDeviceRelative(handle, openvr.k_unTrackedDeviceIndex_Hmd, t)

    renderer = SubtitleRenderer(cfg.get("fonts"), int(cfg.get("font_size", 28)))
    print("[overlay] SteamVR 오버레이 표시 중")
    last = -1
    buf = None
    try:
        while True:
            v, items = board.snapshot()
            if v != last:
                last = v
                if items:
                    img = renderer.render(items, show_original)
                    data = img.tobytes()
                    buf = (ctypes.c_char * len(data)).from_buffer_copy(data)
                    ov.setOverlayRaw(handle, buf, img.width, img.height, 4)
                    ov.showOverlay(handle)
                else:
                    ov.hideOverlay(handle)
            time.sleep(0.1)
    finally:
        openvr.shutdown()

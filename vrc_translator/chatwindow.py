"""메인 창 (일식 테마): 번역 기록을 카드 형태의 채팅으로 보여주고,
①/② 켜기·끄기, 자막 창, 맨 앞 고정, 사용량 막대를 제공한다. 창을 닫으면 프로그램이 종료된다."""
import gc
import os
import queue
import re
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

from . import events, settings
from .theme import FONT, P, mix
from .ui import Pill, setup_style
from .i18n import t
from .update_ui import UpdateBar
from .version import VERSION

MAX_CARDS = 300
BG = P["night"]
PANEL = P["band"]
MUTED = mix(P["dim"], P["night"], 0.45)
META = mix(P["dim"], P["night"], 0.35)


class MainWindow:
    def __init__(self, log_path=None, cfg=None, on_settings=None):
        self.log_path = log_path
        self.cfg = cfg or settings.load()
        self.ui = self.cfg.setdefault("ui", {})
        self.subtitles = None
        self.started = time.time()
        self.used = {"mic": 0, "listen": 0}
        self.usage_info = {}
        self.cards = deque()
        self.pending = {}          # id → 번역을 기다리는 카드 정보
        self.card_labels = []      # 창 크기가 바뀔 때 줄바꿈 폭을 맞출 라벨들

        self.root = tk.Tk()
        self.root.title("VRC Translator")
        self.root.configure(bg=BG)
        self.root.minsize(380, 420)
        self._restore_geometry()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._style()

        # ---------------- 헤더 (DPS 미터기처럼 맨 위 빨간 띠)
        tk.Frame(self.root, bg=P["corona2"], height=3).pack(fill="x")
        head = tk.Frame(self.root, bg=PANEL, padx=14, pady=10)
        head.pack(fill="x")
        row = tk.Frame(head, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="VRC Translator", bg=PANEL, fg=P["text"],
                 font=(FONT, 12, "bold")).pack(side="left")
        tk.Label(row, text=f"v{VERSION}", bg=PANEL, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(6, 0), pady=(4, 0))
        if on_settings:
            Pill(row, t("m.settings"), lambda: self._open_settings(on_settings)).pack(
                side="right", padx=(6, 0))
        self.pin = Pill(row, t("m.pin"), self._set_topmost, toggle=True,
                        value=bool(self.ui.get("topmost")))
        self.pin.pack(side="right")

        srow = tk.Frame(head, bg=PANEL)
        srow.pack(fill="x", pady=(6, 0))
        self.dot = tk.Label(srow, text="●", bg=PANEL, fg=P["corona2"], font=(FONT, 9))
        self.dot.pack(side="left")
        self.status = tk.Label(srow, text=t("s.preparing"), bg=PANEL, fg=P["text"],
                               font=(FONT, 10, "bold"), anchor="w")
        self.status.pack(side="left", padx=(4, 0))
        self.osc = tk.Label(srow, text="", bg=PANEL, fg=MUTED, font=(FONT, 8, "bold"))
        self.osc.pack(side="right")

        self.bar = tk.Canvas(head, height=6, bg=PANEL, highlightthickness=0, bd=0)
        self.bar.pack(fill="x", pady=(8, 2))
        self.bar.bind("<Configure>", lambda e: self._draw_bar())
        self.usage = tk.Label(head, text="", bg=PANEL, fg=MUTED, font=(FONT, 8),
                              anchor="w", justify="left")
        self.usage.pack(fill="x")
        tk.Frame(self.root, bg=P["rim"], height=1).pack(fill="x")

        # ---------------- 스위치
        bar = tk.Frame(self.root, bg=BG, padx=10, pady=8)
        bar.pack(fill="x")
        self.mic_pill = Pill(bar, t("m.mic"), lambda v: self._set_control("mic", v),
                             toggle=True, value=True, bg=BG)
        self.mic_pill.pack(side="left")
        self.lis_pill = Pill(bar, t("m.lis"), lambda v: self._set_control("listen", v),
                             toggle=True, value=True, bg=BG)
        self.lis_pill.pack(side="left", padx=(6, 0))
        self.sub_pill = Pill(bar, t("m.subwin"), self._toggle_subtitles, toggle=True, value=True, bg=BG)
        self.sub_pill.set_enabled(False)
        self.sub_pill.pack(side="right")
        # 업데이트 / GPU 가속 파일 안내 줄 (필요할 때만 스위치 줄 위에 나타남)
        self.updates = UpdateBar(self, before=bar)

        # ---------------- 채팅 영역
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, bg=BG, fg=P["text"], wrap="word", bd=0, padx=6, pady=6,
                            font=(FONT, 10), cursor="arrow", highlightthickness=0,
                            insertwidth=0, selectbackground=P["rim"])
        self.follow = True          # 맨 아래를 따라가는 중인지 (사용자가 위로 올리면 멈춤)
        sb = ttk.Scrollbar(body, command=self._user_scroll_cmd, style="Theme.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.bind("<Key>", lambda e: None if (e.state & 0x4 and e.keysym.lower() == "c") else "break")
        self.text.bind("<Configure>", self._rewrap)
        self._forward_wheel(self.text)
        # 위로 올려 둔 상태에서 새 메시지가 오면 나타나는 버튼
        self.jump = tk.Label(body, text=t("m.new"), bg=P["corona2"],
                             fg="#FFFFFF", font=(FONT, 9, "bold"), padx=10, pady=3,
                             cursor="hand2", highlightthickness=1,
                             highlightbackground=P["corona"])
        self.jump.bind("<Button-1>", lambda e: self._jump_to_end())
        tag = self.text.tag_configure
        tag("sys", foreground=MUTED, font=(FONT, 8), justify="center", spacing1=2, spacing3=2)
        tag("err", foreground=P["blood"], font=(FONT, 9, "bold"), justify="center",
          spacing1=4, spacing3=4)
        tag("me", justify="right")
        tag("ot", justify="left")

        # ---------------- 하단
        tk.Frame(self.root, bg=P["rim"], height=1).pack(fill="x")
        foot = tk.Frame(self.root, bg=PANEL, padx=10, pady=6)
        foot.pack(fill="x")
        Pill(foot, t("m.clear"), self._clear).pack(side="left")
        tk.Label(foot, text=t("m.close_hint"), bg=PANEL, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=10)
        if log_path:
            Pill(foot, t("m.log"), self._open_log).pack(side="right")
        self.toast = tk.Label(foot, text="", bg=PANEL, fg=P["mine"], font=(FONT, 8, "bold"))
        self.toast.pack(side="right", padx=8)

        if self.ui.get("topmost"):
            self.root.attributes("-topmost", True)
        self.root.after(100, self._poll)
        self.root.after(30000, self._usage_tick)
        self.root.after(5000, self._sweep_pending)
        self.root.after(2000, self._osc_tick)
        self.root.after(15000, self._gc_tick)

    # ------------------------------------------------------------ 창 설정
    def _style(self):
        setup_style(self.root)

    def _restore_geometry(self):
        g = self.ui.get("geometry")
        m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", g or "")
        if m:
            w, h, x, y = map(int, m.groups())
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            if -50 < x < sw - 100 and 0 <= y < sh - 100:      # 화면 밖이면 무시
                self.root.geometry(g)
                return
        self.root.geometry("460x680")

    def _save_ui(self):
        try:
            settings.save(self.cfg)
        except Exception as e:
            print(f"[ui] 설정 저장 실패: {e}")

    def _on_close(self):
        self.ui["geometry"] = self.root.geometry()
        self._save_ui()
        self.root.destroy()

    def _open_settings(self, fn):
        self.ui["geometry"] = self.root.geometry()
        self._save_ui()
        fn()

    def _set_topmost(self, on):
        self.root.attributes("-topmost", on)
        self.ui["topmost"] = on
        self._save_ui()
        self._toast(t("m.pinned") if on else t("m.unpinned"))

    def _toast(self, text):
        self.toast.config(text=text)
        self.root.after(2000, lambda: self.toast.config(text=""))

    # ------------------------------------------------------------ 조작
    def set_controls(self, mic, listen):
        events.CONTROLS["mic"], events.CONTROLS["listen"] = mic, listen
        self.mic_pill.set(mic)
        self.lis_pill.set(listen)

    def _set_control(self, name, on):
        events.CONTROLS[name] = on

    def _toggle_subtitles(self, on):
        if self.subtitles:
            self.subtitles.set_visible(on)

    def _clear(self):
        for c in self.cards:
            c.destroy()
        self.cards.clear()
        self.card_labels.clear()
        self.pending.clear()
        self.text.delete("1.0", "end")
        self._jump_to_end()

    def _open_log(self):
        try:
            os.startfile(self.log_path)
        except Exception as e:
            messagebox.showerror("VRC Translator", t("m.log_fail", e=e))

    def attach_subtitles(self, subtitles):
        self.subtitles = subtitles
        if self.ui.get("subtitle_pos"):
            subtitles.set_position(self.ui["subtitle_pos"])
        subtitles.on_move = lambda pos: self.ui.__setitem__("subtitle_pos", pos)
        subtitles.on_hide = lambda: self.sub_pill.set(False)
        self.sub_pill.set_enabled(True)
        self.sub_pill.set(subtitles.visible)

    # ------------------------------------------------------------ 사용량
    def _session_total(self):
        return self.used["mic"] + self.used["listen"]

    def _draw_bar(self):
        c = self.bar
        c.delete("all")
        w, h = c.winfo_width(), int(c["height"])
        if w < 10:
            return
        u = self.usage_info
        if u.get("daily_limit"):
            frac = (u.get("today_used", 0) + self._session_total()) / u["daily_limit"]
        elif u.get("limit"):
            frac = (u.get("count", 0) + self._session_total()) / u["limit"]
        else:
            frac = 0
        frac = max(0.0, min(1.0, frac))
        c.create_rectangle(0, 0, w, h, fill=mix(P["night"], P["rim"], 0.5), width=0)
        fill_w = int(w * frac)
        if fill_w <= 0:
            return
        steps = max(1, fill_w // 3)
        for i in range(steps):                       # 주황 → 금색 그라데이션
            x0 = i * fill_w / steps
            x1 = (i + 1) * fill_w / steps
            col = P["blood"] if frac > 0.9 else mix(P["corona2"], P["corona"], i / max(1, steps - 1))
            c.create_rectangle(x0, 0, x1 + 1, h, fill=col, width=0)

    def _refresh_usage(self):
        u = self.usage_info
        total = self._session_total()
        lines = []
        if u.get("limit"):
            month = u.get("count", 0) + total
            lines.append(t("m.u_month", used=month, limit=u["limit"]))
            if u.get("daily_limit"):
                lines[-1] += t("m.u_today", used=u.get("today_used", 0) + total, limit=u["daily_limit"])
        elif u.get("text"):
            lines.append(u["text"])
        hours = (time.time() - self.started) / 3600
        s = t("m.u_run", total=total, mic=self.used["mic"], lis=self.used["listen"])
        if hours >= 0.25:
            s += t("m.u_rate", n=int(total / hours))
        lines.append(s)
        self.usage.config(text="\n".join(lines))
        self._draw_bar()

    def _usage_tick(self):
        self._refresh_usage()
        self.root.after(30000, self._usage_tick)

    # ------------------------------------------------------------ 채팅 카드
    def _wrap_width(self):
        return max(160, int(self.text.winfo_width() * 0.74) - 30)

    def _rewrap(self, _e=None):
        w = self._wrap_width()
        for lbl in self.card_labels:
            try:
                lbl.config(wraplength=w)
            except tk.TclError:
                pass

    def _forward_wheel(self, widget):
        widget.bind("<MouseWheel>", lambda e: self._user_scroll(int(-e.delta / 120)) or "break")
        widget.bind("<Button-4>", lambda e: self._user_scroll(-1) or "break")
        widget.bind("<Button-5>", lambda e: self._user_scroll(1) or "break")

    def _user_scroll(self, units):
        if units:
            self.text.yview_scroll(units, "units")
        self.root.after_idle(self._update_follow)

    def _user_scroll_cmd(self, *args):
        self.text.yview(*args)
        self.root.after_idle(self._update_follow)

    def _update_follow(self):
        self.follow = self.text.yview()[1] >= 0.999
        if self.follow:
            self.jump.place_forget()

    def _scroll_to_end(self):
        # 카드(내장 위젯)는 그려진 뒤에야 높이가 정해지므로 한 박자 늦게 한 번 더 내린다
        self.text.yview_moveto(1.0)
        self.root.after(30, lambda: self.follow and self.text.yview_moveto(1.0))
        self.root.after(120, lambda: self.follow and self.text.yview_moveto(1.0))

    def _jump_to_end(self):
        self.follow = True
        self.jump.place_forget()
        self._scroll_to_end()

    def _after_insert(self):
        if self.follow:
            self.root.after_idle(self._scroll_to_end)
        else:
            self.jump.place(relx=0.5, rely=1.0, y=-10, anchor="s")

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._toast(t("m.copied"))

    def add_system(self, text, tag="sys"):
        self.text.insert("end", text + "\n", tag)
        self._after_insert()

    def add_utterance(self, source, original, translated, t_stt, t_tr, uid=None, pending=False,
                      lang=None, engine=None):
        me = source == "mic"

        if me:
            card_bg = mix(P["band"], P["mine"], 0.06)
            border = mix(P["rim"], P["mine"], 0.45)
            accent, main_fg = P["mine"], P["mine"]
            who = t("m.me")
        else:
            card_bg, border = P["band"], P["rim"]
            accent, main_fg = P["corona"], P["text"]
            who = t("m.other")
        anchor, justify = ("e", "right") if me else ("w", "left")

        tag = f"  ·  {lang.upper()}" if lang else ""
        meta_base = f"{who}{tag}  ·  {time.strftime('%H:%M:%S')}  ·  " + t("m.stt", s=t_stt) + \
            (f" ({engine})" if engine and engine != "-" else "")
        if pending:
            meta = meta_base + "  ·  " + t("m.pending")
        elif translated is not None:
            meta = meta_base + t("m.tr", s=t_tr) + "  ·  " + t("m.chars", n=len(original))
            self.used[source] = self.used.get(source, 0) + len(original)
            self._refresh_usage()
        else:
            meta = meta_base + "  ·  " + t("m.no_tr")

        card = tk.Frame(self.text, bg=card_bg, highlightthickness=1,
                        highlightbackground=border, bd=0)
        tk.Frame(card, bg=accent, width=3).pack(side="right" if me else "left", fill="y")
        inner = tk.Frame(card, bg=card_bg, padx=10, pady=6)
        inner.pack(side="left", fill="both", expand=True)

        wl = self._wrap_width()
        widgets = [card, inner]
        meta_lbl = tk.Label(inner, text=meta, bg=card_bg, fg=META, font=(FONT, 7), anchor=anchor)
        meta_lbl.pack(fill="x")
        widgets.append(meta_lbl)
        if translated is not None or pending:
            lbl = tk.Label(inner, text=original, bg=card_bg, fg=MUTED, font=(FONT, 9),
                           anchor=anchor, justify=justify, wraplength=wl)
            lbl.pack(fill="x", pady=(2, 0))
            widgets.append(lbl)
            self.card_labels.append(lbl)
        main = translated if translated is not None else ("…" if pending else original)
        main_lbl = tk.Label(inner, text=main, bg=card_bg, fg=META if pending else main_fg,
                            font=(FONT, 12, "bold"), anchor=anchor, justify=justify, wraplength=wl)
        main_lbl.pack(fill="x", pady=(1, 0))
        widgets.append(main_lbl)
        self.card_labels.append(main_lbl)

        copy_text = {"text": main}
        for w in widgets:
            self._forward_wheel(w)
            w.bind("<Button-3>", lambda e: self._copy(copy_text["text"]))
        if pending and uid:
            self.pending[uid] = dict(main=main_lbl, meta=meta_lbl, base=meta_base, fg=main_fg,
                                     source=source, copy=copy_text, card=card, at=time.time())

        start = self.text.index("end-1c")
        self.text.window_create("end", window=card, padx=6, pady=4)
        self.text.insert("end", "\n")
        self.text.tag_add("me" if me else "ot", start, "end-1c")
        self.cards.append(card)

        while len(self.cards) > MAX_CARDS:                  # 오래된 카드 정리
            old = self.cards.popleft()
            try:
                idx = self.text.index(old)
                self.text.delete("1.0", f"{idx} +1 line linestart")
            except tk.TclError:
                pass
            for l in old.winfo_children():
                for ll in l.winfo_children():
                    if ll in self.card_labels:
                        self.card_labels.remove(ll)
            for k in [k for k, v in self.pending.items() if v["card"] is old]:
                self.pending.pop(k, None)
            old.destroy()

        self._after_insert()

    def _gc_tick(self):
        """메모리 정리를 메인 스레드에서만 (자동 GC는 꺼 둠 — tkinter 객체 안전 때문)."""
        gc.collect()
        self.root.after(15000, self._gc_tick)

    def _osc_tick(self):
        """VRChat에서 OSC 신호가 오는지 표시. 안 오면 VRChat 설정에서 OSC가 꺼져 있을 가능성이 크다."""
        st = events.OSC
        if st["listening"] is None:
            text, fg = "", MUTED
        elif st["listening"] is False:
            text, fg = t("m.osc_na"), MUTED
        elif time.time() - st["last"] < 60:
            text, fg = t("m.osc_ok"), P["ok"]
        else:
            text, fg = t("m.osc_none"), P["blood"]
        self.osc.config(text=text, fg=fg)
        self.root.after(2000, self._osc_tick)

    def _sweep_pending(self):
        now = time.time()
        for uid, info in list(self.pending.items()):
            if now - info["at"] > 20:
                self.pending.pop(uid, None)
                try:
                    info["main"].config(text=t("m.tr_timeout"), fg=P["blood"])
                    info["meta"].config(text=info["base"] + "  ·  " + t("m.no_resp"))
                except tk.TclError:
                    pass
        self.root.after(5000, self._sweep_pending)

    def finish_utterance(self, uid, source, original, translated, t_tr, error=None, engine=None):
        """원문만 먼저 띄운 카드에 번역을 채운다."""
        info = self.pending.pop(uid, None)
        if not info:
            return
        try:
            if error or translated is None:
                info["main"].config(text=t("m.tr_fail"), fg=P["blood"])
                info["meta"].config(text=info["base"] + "  ·  " + t("m.tr_fail"))
                return
            info["main"].config(text=translated, fg=info["fg"])
            tag = f" ({engine})" if engine else ""
            chars = "" if engine == "Google" else "  ·  " + t("m.chars", n=len(original))
            info["meta"].config(text=info["base"] + t("m.tr", s=t_tr) + tag + chars)
            info["copy"]["text"] = translated
        except tk.TclError:
            return                      # 이미 정리된 카드
        if engine != "Google":          # DeepL 사용량만 센다 (Google 번역은 무료 한도와 무관)
            self.used[source] = self.used.get(source, 0) + len(original)
            self._refresh_usage()
        if self.follow:
            self.root.after_idle(self._scroll_to_end)

    # ------------------------------------------------------------ 이벤트 처리
    def _poll(self):
        for _ in range(200):
            try:
                kind, d = events.Q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.add_system(d["text"])
            elif kind == "status":
                self.status.config(text=d["text"])
                self.dot.config(fg={"running": P["ok"], "error": P["blood"]}.get(d.get("state"), P["corona"]))
            elif kind == "usage":
                self.usage_info = d
                self._refresh_usage()
            elif kind == "utt":
                self.add_utterance(d["source"], d["original"], d["translated"],
                                   d["t_stt"], d["t_tr"], d.get("id"), d.get("pending", False),
                                   d.get("lang"), d.get("engine"))
            elif kind == "utt_done":
                self.finish_utterance(d["id"], d["source"], d["original"], d["translated"],
                                      d["t_tr"], d.get("error"), d.get("engine"))
            elif kind == "call":
                d["fn"](self)
            elif kind == "error":
                self.status.config(text=t("s.error"))
                self.dot.config(fg=P["blood"])
                self.add_system((d.get("brief") or d["text"]).strip(), "err")
                messagebox.showerror("VRC Translator", (d.get("brief") or d["text"])[:500])
        self.root.after(100, self._poll)

    def mainloop(self):
        self.root.mainloop()

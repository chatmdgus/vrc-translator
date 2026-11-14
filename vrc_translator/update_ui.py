"""메인 창 위쪽의 업데이트 / GPU 가속 파일 안내 줄."""
import threading
import tkinter as tk

from . import events, updater
from .i18n import t
from .theme import FONT, P, mix
from .ui import Pill

CHECK_EVERY_MS = 3 * 60 * 60 * 1000     # 켜 둔 동안 3시간마다 다시 확인
BG = mix(P["band"], P["corona2"], 0.18)


class UpdateBar:
    def __init__(self, win, before):
        self.win = win
        self.root = win.root
        self.before = before            # 이 위젯 앞에 끼워 넣는다
        self.busy = False
        self.result = None
        self.frame = tk.Frame(self.root, bg=BG, padx=12, pady=7)
        self.msg = tk.Label(self.frame, text="", bg=BG, fg=P["text"], font=(FONT, 9, "bold"),
                            anchor="w", justify="left")
        self.msg.pack(side="left", fill="x", expand=True)
        self.btns = tk.Frame(self.frame, bg=BG)
        self.btns.pack(side="right")
        self.prog = tk.Canvas(self.root, height=3, bg=BG, highlightthickness=0, bd=0)
        self.prog.bind("<Configure>", lambda e: self._draw_prog())
        self.frac = 0.0
        self.shown = False
        self.local = updater.load_local()
        if self.local:
            updater.cleanup_async()
            self.root.after(3000, self.check)
            self.root.after(CHECK_EVERY_MS, self._periodic)
        done = updater.pop_updated()
        if done:
            self.root.after(500, lambda: win.add_system(t("u.done", v=done.get("to", "?"))))

    # ------------------------------------------------ 표시
    def _show(self, text, buttons=(), progress=None):
        self.msg.config(text=text)
        for w in self.btns.winfo_children():
            w.destroy()
        for label, cmd in buttons:
            Pill(self.btns, label, cmd, bg=BG).pack(side="left", padx=(6, 0))
        if not self.shown:
            self.frame.pack(fill="x", before=self.before)
            self.shown = True
        if progress is None:
            self.prog.pack_forget()
        else:
            if not self.prog.winfo_ismapped():
                self.prog.pack(fill="x", before=self.before)
            self.frac = max(0.0, min(1.0, progress))
            self._draw_prog()
        self.msg.config(wraplength=max(200, self.root.winfo_width() - 160))

    def _draw_prog(self):
        self.prog.delete("all")
        w = self.prog.winfo_width()
        if w > 1:
            self.prog.create_rectangle(0, 0, int(w * self.frac), 3, fill=P["mine"], width=0)

    def _hide(self):
        if self.shown:
            self.frame.pack_forget()
            self.prog.pack_forget()
            self.shown = False

    def _call(self, fn):
        events.emit("call", fn=lambda _win: fn())

    # ------------------------------------------------ 확인
    def _periodic(self):
        if not self.busy:
            self.check()
        self.root.after(CHECK_EVERY_MS, self._periodic)

    def check(self):
        if self.busy:
            return
        threading.Thread(target=self._check_bg, daemon=True, name="update-check").start()

    def _check_bg(self):
        try:
            res = updater.check(self.local)
        except Exception as e:
            print(f"[update] check failed: {e}")
            return
        self._call(lambda: self._offer(res))

    def _offer(self, res):
        self.result = res
        if self.busy or res is None:
            return
        if res.update:
            p = res.update
            print(f"[update] v{updater.VERSION} → v{p.version}: {p}")
            self._show(t("u.new", v=p.version, size=updater.fmt_size(p.size)),
                       [(t("u.btn"), lambda: self.start(p))])
        elif res.gpu and updater.nvidia_present() and not self.win.ui.get("gpu_offer_off"):
            p = res.gpu
            self._show(t("u.gpu", size=updater.fmt_size(p.size)),
                       [(t("u.gpu_btn"), lambda: self.start(p)), ("✕", self._dismiss_gpu)])
        else:
            self._hide()

    def _dismiss_gpu(self):
        self.win.ui["gpu_offer_off"] = True
        self.win._save_ui()
        self._hide()

    # ------------------------------------------------ 받기·적용
    def start(self, plan):
        if self.busy:
            return
        self.busy = True
        self._show(t("u.dl", pct=0, done="0 MB", total=updater.fmt_size(plan.size)), progress=0)
        threading.Thread(target=self._run, args=(plan,), daemon=True, name="update-dl").start()

    def _run(self, plan):
        def progress(stage, done, total):
            if stage == "dl":
                frac = done / total if total else 1.0
                text = t("u.dl", pct=int(frac * 100), done=updater.fmt_size(done),
                         total=updater.fmt_size(total))
            else:
                frac, text = 1.0, t("u.unpack")
            self._call(lambda: self._show(text, progress=frac))
        try:
            updater.download(plan, progress)
        except updater.UpdateError as e:
            self._call(lambda: self._fail(e, plan))
            return
        except Exception as e:
            self._call(lambda: self._fail(updater.UpdateError("net", f"{type(e).__name__}: {e}"), plan))
            return
        self._call(lambda: self._apply(plan))

    def _apply(self, plan):
        self._show(t("u.apply"), progress=1.0)
        self.root.update_idletasks()
        try:
            self.win.ui["geometry"] = self.root.geometry()
            self.win._save_ui()
        except Exception:
            pass
        old = updater.VERSION
        try:
            how = updater.apply(plan)
        except Exception as e:
            self._fail(updater.UpdateError("perm", f"{type(e).__name__}: {e}"), plan)
            return
        updater.mark_updated(old, plan.version)
        print(f"[update] applied ({how}) → v{plan.version}, restarting")
        try:
            import sys
            sys.stdout.flush()
        except Exception:
            pass
        if how == "helper":
            import os
            os._exit(0)                 # 도우미가 파일을 옮기고 다시 켠다
        updater.restart()

    def _fail(self, e, plan):
        self.busy = False
        print(f"[update] failed: {e}")
        key = {"perm": "u.perm", "disk": "u.disk", "mismatch": "u.uploading"}.get(e.code, "u.fail")
        text = t(key, e=e.detail or e.code, need=e.detail)
        self._show(text, [(t("u.retry"), lambda: self.start(plan))])

"""VRC Translator: ① 내 말 → 챗박스, ② 상대 말 → 자막.
실행: python app.py   (설정 창 없이: python app.py --no-gui)

스레드 구조: 메인 스레드 = tkinter(설정 창 → 메인 창/자막 창),
백그라운드 = 모델 로딩, 오디오 캡처, 인식·번역. 둘 사이는 events.Q로 주고받는다."""
import collections
import gc
import itertools
import os
import queue
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from . import events, settings
from .audio import AudioCapture, Segmenter
from .chatbox import Chatbox, MuteWatcher, compose
from .chatwindow import MainWindow
from .i18n import set_lang, t
from .launcher import run_launcher
from .overlay import DesktopSubtitles, SubtitleBoard, run_vr
from .runtime_env import setup_dll_paths
from .stt import STT
from .translate import make_translator, translate_ex

MAX_BACKLOG = 6   # 인식 대기 발화가 이보다 많으면 오래된 것부터 버림
CONTEXT_LINES = 4      # 맥락으로 넘길 최근 문장 수
TRANSLATE_TIMEOUT = 12  # 번역이 이보다 오래 걸리면 포기하고 다음 발화로
_TR_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="translate")
# 자막(②)용: 번역을 기다리지 않고 다음 발화 인식으로 넘어가기 위한 별도 풀
_ASYNC_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="translate-async")
CONTEXT_SECONDS = 90   # 이보다 오래된 문장은 맥락에서 제외


class Pipeline:
    """캡처 → 발화 분할 → 인식 → (원문 먼저 표시) → 번역 → 결과 전달."""

    def __init__(self, name, capture, vad_cfg, stt, translator, source_lang, target_lang,
                 on_result, gate=None, on_original=None, on_speech=None, on_idle=None,
                 allowed=None, async_translate=False, hotwords=None, fixes=None, beam=None):
        self.name = name
        self.capture = capture
        self.vad_cfg = vad_cfg
        self.stt = stt
        self.translator = translator
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.on_result = on_result        # (id, 원문, 번역|None, 언어) — 번역까지 끝난 뒤
        self.on_original = on_original    # (id, 원문, 언어, 번역 대기?) — 인식 직후
        self.on_speech = on_speech        # 말하기 시작 감지
        self.on_idle = on_idle            # 이번 발화 처리 끝 (버려진 경우 포함)
        self.gate = gate
        self.allowed = allowed            # 이 파이프라인이 인식할 언어 (None=공통 설정)
        # True면 번역을 백그라운드로 보내고 바로 다음 발화를 인식한다 (여럿이 동시에 말하는 자막용).
        # 챗박스(①)는 보내는 순서가 중요해서 False (한 문장씩 차례대로)
        self.async_translate = async_translate
        self.hotwords = [w for w in (hotwords or []) if w.strip()]   # 인식 힌트 (이름, 자주 쓰는 말)
        self.fixes = [(a, b) for a, b in (fixes or []) if a]         # 인식 후 고쳐 쓰기 (틀린 말 → 맞는 말)
        self.beam = beam
        self.work = queue.Queue()
        self._ids = itertools.count(1)
        self.history = collections.deque(maxlen=CONTEXT_LINES)   # (시각, 언어, 문장)

    def _context(self, lang):
        """같은 언어로 최근 말한 문장들 → DeepL context (번역 품질↑, 글자 수 안 셈)."""
        now = time.time()
        lines = [t for ts, l, t in self.history if l == lang and now - ts < CONTEXT_SECONDS]
        return "\n".join(lines) or None

    def _active(self):
        if not events.CONTROLS.get(self.name, True):
            return False
        return not (self.gate and not self.gate())

    def _translate(self, text, lang, ctx):
        fut = _TR_POOL.submit(translate_ex, self.translator, text, lang, self.target_lang, ctx)
        try:
            return fut.result(timeout=TRANSLATE_TIMEOUT)
        except FutureTimeout:
            raise TimeoutError(t("e.timeout", n=TRANSLATE_TIMEOUT))

    def _translate_later(self, uid, text, lang, ctx, t1):
        """백그라운드 번역 (requests 타임아웃으로 최대 십몇 초 안에 끝남)."""
        try:
            translated, tr_engine = translate_ex(self.translator, text, lang, self.target_lang, ctx)
            self.on_result(uid, text, translated, lang)
            events.emit("utt_done", id=uid, source=self.name, original=text,
                        translated=translated, t_tr=time.time() - t1, engine=tr_engine)
        except Exception as e:
            print(t("log.err", name=self.name, e=f"{type(e).__name__}: {e}"))
            events.emit("utt_done", id=uid, source=self.name, original="",
                        translated=None, t_tr=0.0, error=str(e))
            self.on_result(uid, "", None, "")

    def _idle(self):
        if self.on_idle:
            self.on_idle()

    def start(self):
        self.capture.start()
        self.seg = Segmenter(self.capture.rate, self.vad_cfg,
                             on_start=lambda: self._active() and self.on_speech and self.on_speech(),
                             on_discard=self._idle)
        threading.Thread(target=self._seg_loop, daemon=True).start()
        threading.Thread(target=self._work_loop, daemon=True).start()

    def _enqueue(self, audio):
        if not self._active():
            self._idle()
            return
        self.work.put(audio)
        while self.work.qsize() > MAX_BACKLOG:
            try:
                self.work.get_nowait()
                print(t("log.skip", name=self.name))
            except queue.Empty:
                break

    def _seg_loop(self):
        while True:
            mono = self.capture.read(timeout=0.3)
            if mono is None:
                u = self.seg.flush()
                if u is not None:
                    self._enqueue(u)
                continue
            for u in self.seg.feed(mono):
                self._enqueue(u)

    def _work_loop(self):
        while True:
            audio = self.work.get()
            uid = f"{self.name}-{next(self._ids)}"
            t0 = time.time()
            shown = False
            try:
                text, lang, engine = self.stt.transcribe_ex(
                    audio, self.source_lang, self.allowed, hotwords=self.hotwords, beam=self.beam,
                    who=self.name)
                for wrong, right in self.fixes:
                    text = text.replace(wrong, right)
                if not text.strip():
                    continue
                t1 = time.time()
                needs = lang != self.target_lang
                # 번역을 기다리지 않고 원문부터 화면에 띄운다
                events.emit("utt", id=uid, source=self.name, lang=lang, engine=engine, original=text,
                            translated=None, pending=needs, t_stt=t1 - t0, t_tr=0.0)
                shown = True
                if self.on_original:
                    self.on_original(uid, text, lang, needs)
                ctx = self._context(lang)
                self.history.append((time.time(), lang, text))
                if needs and self.async_translate:
                    _ASYNC_POOL.submit(self._translate_later, uid, text, lang, ctx, t1)
                    continue
                translated, tr_engine = self._translate(text, lang, ctx) if needs else (None, None)
                t2 = time.time()
                self.on_result(uid, text, translated, lang)
                if needs:
                    events.emit("utt_done", id=uid, source=self.name, original=text,
                                translated=translated, t_tr=t2 - t1, engine=tr_engine)
            except Exception as e:
                print(t("log.err", name=self.name, e=f"{type(e).__name__}: {e}"))
                if shown:
                    events.emit("utt_done", id=uid, source=self.name, original="",
                                translated=None, t_tr=0.0, error=str(e))
                    if self.on_original:
                        self.on_result(uid, "", None, "")
            finally:
                self._idle()


def backend(cfg):
    """백그라운드 스레드에서 실행. 준비가 끝나면 파이프라인이 계속 돈다."""
    mic_cfg = cfg.get("mic", {})
    lis_cfg = cfg.get("listen", {})
    ov_cfg = cfg.get("overlay", {})
    base_vad = cfg.get("vad", {})

    if not mic_cfg.get("enabled") and not lis_cfg.get("enabled"):
        raise SystemExit(t("s.both_off"))
    my_lang = cfg.get("my_lang", "ko")
    vocab = cfg.get("vocab") or {}
    fixes = [tuple(x) for x in (vocab.get("fix") or []) if isinstance(x, (list, tuple)) and len(x) == 2]

    events.status(t("s.tr_prep"))
    translator = make_translator(cfg.get("translator", {}))
    events.status(t("s.stt_prep"))
    stt = _make_stt(cfg)

    pipelines = []
    if mic_cfg.get("enabled"):
        host = mic_cfg.get("osc_host", "127.0.0.1")
        chatbox = Chatbox(host, int(mic_cfg.get("osc_send_port", 9000)),
                          float(mic_cfg.get("chatbox_interval_s", 1.6)))
        gate = None
        if mic_cfg.get("respect_vrchat_mute", True):
            mute = MuteWatcher(host, int(mic_cfg.get("osc_listen_port", 9001)))
            gate = lambda: not mute.muted
        show_orig = bool(mic_cfg.get("chatbox_show_original", True))
        typing = bool(mic_cfg.get("typing_indicator", True))

        def to_chatbox(uid, original, translated, lang):
            # 번역문 아래 줄에 원문. 이미 일본어로 말했으면 그대로 한 줄
            if original:
                chatbox.send(compose(translated, original, show_orig))

        mic_target = mic_cfg.get("target_lang", "ja")
        if mic_target == my_lang:                     # 설정이 꼬였을 때 대비
            mic_target = "ja" if my_lang != "ja" else "ko"
        # 내 언어만(mine): 언어 감지를 건너뛰어 빠름 (Google에도 한 번만 요청)
        # 둘 다(both): 상대 언어로 말해도 받아적음 (예: 일본인이 한국어로 말하면 카타카나가 아니라 한글로,
        #   번역 없이 그대로 챗박스에). 결과 고르기는 cloud_stt.pick_lang (Whisper 언어 감지 + 글자 검사)
        if mic_cfg.get("speak", "both") == "mine":
            mic_source, mic_allowed = my_lang, [my_lang]
        else:
            mic_source, mic_allowed = None, [my_lang, mic_target]
        pipelines.append(Pipeline(
            "mic", AudioCapture("mic", mic_cfg.get("device")),
            {**base_vad, **mic_cfg.get("vad", {})}, stt, translator,
            mic_source, mic_target,
            to_chatbox, gate,
            on_speech=(lambda: chatbox.typing(True)) if typing else None,
            on_idle=(lambda: chatbox.typing(False)) if typing else None,
            allowed=mic_allowed, hotwords=vocab.get("mic"), fixes=fixes,
            beam=int(cfg.get("whisper", {}).get("mic_beam_size", 5))))

    if lis_cfg.get("enabled"):
        board = SubtitleBoard(int(ov_cfg.get("max_lines", 4)),
                              float(ov_cfg.get("line_timeout_s", 10)))
        target = my_lang
        # 내 언어(그대로 표시) + 번역해서 볼 언어들
        lis_allowed = [my_lang] + [l for l in (lis_cfg.get("langs") or ["ja"]) if l != my_lang]

        def sub_original(uid, original, lang, pending):
            board.push(original, None, lang, target, uid=uid, pending=pending)

        def sub_result(uid, original, translated, lang):
            board.update(uid, translated)

        pipelines.append(Pipeline(
            "listen", AudioCapture("loopback", lis_cfg.get("device")),
            {**base_vad, **lis_cfg.get("vad", {})}, stt, translator,
            lis_cfg.get("source_lang"), target,
            sub_result, on_original=sub_original, allowed=lis_allowed, async_translate=True,
            hotwords=vocab.get("listen"), fixes=fixes))
        show = bool(lis_cfg.get("show_original", True))
        if ov_cfg.get("mode", "desktop") == "vr":
            threading.Thread(target=_run_vr_safe, args=(board, ov_cfg, show), daemon=True).start()
        else:
            # tkinter 창은 메인 스레드에서 만들어야 하므로 메인 창에 부탁한다
            events.emit("call", fn=lambda win: win.attach_subtitles(
                DesktopSubtitles(win.root, board, ov_cfg, show)))

    for p in pipelines:
        p.start()
    events.status(t("s.running", label=stt.label), state="running")
    print(t("s.ready_log"))


def _make_stt(cfg):
    """음성인식 준비. 클라우드 우선이면 Google → PC 순서로 쓰는 라우터."""
    wcfg = cfg.get("whisper", {})
    scfg = cfg.get("stt", {})
    if scfg.get("mode", "cloud") != "cloud":
        return STT(wcfg)
    from .cloud_stt import GoogleSTT, STTRouter
    google = None
    try:
        google = GoogleSTT(float(scfg.get("timeout", 4)))
    except Exception as e:
        print(t("log.cloud_google_na", e=e))
    return STTRouter(google, local_factory=lambda: STT(wcfg))


def _run_vr_safe(board, ov_cfg, show):
    try:
        run_vr(board, ov_cfg, show)
    except Exception as e:
        events.error(traceback.format_exc(),
                     t("s.vr_fail", e=e))


def _backend_safe(cfg):
    try:
        backend(cfg)
    except SystemExit as e:
        events.error(str(e.code), str(e.code))
    except Exception as e:
        events.error(traceback.format_exc(), t("s.start_fail", e=e))


def _check_in_background(cfg):
    """설정 창 없이 바로 시작했을 때: 번역 연결·사용량을 뒤에서 확인해서 메인 창에 보여준다."""
    from .launcher import check_translator
    ok, msg, info = check_translator(cfg["translator"])
    if ok:
        print(t("log.usage", msg=msg))
        events.emit("usage", text=msg, **{k: v for k, v in info.items() if k != "text"})
    else:
        events.error(msg, msg)


def restart_to_settings():
    """메인 창의 「설정」 버튼: 설정 창으로 다시 시작한다."""
    import subprocess
    if getattr(sys, "frozen", False):
        args, cwd = [sys.executable, "--settings"], None
    else:
        args = [sys.executable, "-m", "vrc_translator.main", "--settings"]
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.Popen(args, cwd=cwd, close_fds=True)
    os._exit(0)


def _dpi_aware():
    """고해상도 모니터에서 글자가 흐릿하게 보이지 않게."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _fatal(text):
    """치명적 오류를 반드시 사용자에게 보여준다 (tkinter가 망가져 있어도 Windows 기본 창으로)."""
    print(text, file=sys.stderr)
    msg = "VRC Translator 오류 / エラー\n\n" + text[-1500:] + \
          f"\n\nlog: {settings.config_path().parent / 'log.txt'}"
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "VRC Translator", 0x10)
        return
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("VRC Translator", msg)
        r.destroy()
    except Exception:
        pass


def _tk_errors(root):
    """tkinter 버튼 콜백 안에서 난 오류도 로그에 남기고 알려준다."""
    def report(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        print(text, file=sys.stderr)
        _fatal(text)
    root.report_callback_exception = report


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "--probe-cuda":
        # GPU 시험용 자식 프로세스: 창도 로그도 건드리지 않고 결과만 종료 코드로 돌려준다
        setup_dll_paths()
        try:
            from .stt import probe_main
            code = probe_main(sys.argv[2], sys.argv[3])
        except BaseException:
            traceback.print_exc()
            code = 2
        os._exit(code)
    try:
        setup_dll_paths()
        log_path = events.install(settings.config_path().parent / "log.txt")
        from .version import VERSION
        print(f"VRC Translator v{VERSION} start (frozen={getattr(sys, 'frozen', False)})")
        # 자동 GC는 끈다: 설정 창의 연결 확인·번역 스레드에서 GC가 돌다가 tkinter 객체를 해제하면
        # Tcl이 프로세스를 강제 종료하기 때문. 정리는 메인 스레드에서만 직접 한다.
        gc.disable()
        _dpi_aware()
        cfg = settings.load()
        set_lang(cfg.get("my_lang", "ko"))
        ui = cfg.get("ui", {})
        quick = ui.get("setup_done") and ui.get("autostart", True) and "--settings" not in sys.argv
        if "--no-gui" not in sys.argv and not quick:
            cfg = run_launcher(cfg, on_root=_tk_errors)
            if cfg is None:
                print("launcher closed")
                return
        elif quick:
            print("autostart with saved settings")
            threading.Thread(target=_check_in_background, args=(cfg,), daemon=True).start()
        # 설정 창(Tk)에서 남은 객체를 반드시 메인 스레드에서 정리한다.
        # tkinter 객체가 다른 스레드의 GC에서 해제되면 Tcl이 프로세스를 강제 종료함
        # ("Tcl_AsyncDelete: async handler deleted by the wrong thread" / 0x80000003)
        gc.collect()          # 이후로도 메인 창이 메인 스레드에서 주기적으로 정리
        set_lang(cfg.get("my_lang", "ko"))
        win = MainWindow(log_path, cfg, on_settings=restart_to_settings)
        _tk_errors(win.root)
        win.set_controls(bool(cfg["mic"].get("enabled")), bool(cfg["listen"].get("enabled")))
        threading.Thread(target=_backend_safe, args=(cfg,), daemon=True).start()
        win.mainloop()
        print("main window closed")
    except BaseException:
        _fatal(traceback.format_exc())
    finally:
        # 창을 닫으면 무조건 프로세스까지 종료한다.
        # (오디오 캡처·GPU·번역 요청 스레드가 남아 있으면 exe가 백그라운드에 계속 남아서
        #  다시 빌드하거나 폴더를 지울 때 '사용 중' 오류가 났음)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    main()

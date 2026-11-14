"""faster-whisper 음성인식."""
import re
from .i18n import t

# Whisper가 무음/음악에서 자주 지어내는 문구 (유튜브 자막 학습 흔적)
HALLUCINATIONS = [
    "시청해주셔서", "시청해 주셔서", "구독과 좋아요", "구독 좋아요", "MBC 뉴스", "자막 제공",
    "ご視聴ありがとう", "チャンネル登録", "お疲れ様でした。ご視聴",
    "thanks for watching", "thank you for watching", "please subscribe", "subtitles by",
]
_ONLY_PUNCT = re.compile(r"^[\s\W_]*$")


def is_hallucination(text):
    t = text.strip()
    if not t or _ONLY_PUNCT.match(t):
        return True
    low = t.lower()
    for h in HALLUCINATIONS:
        if h.lower() in low and len(t) < len(h) + 15:
            return True
    # 같은 글자/단어 반복 ("ああああああ", "네 네 네 네 네 네")
    if len(set(t.replace(" ", ""))) <= 2 and len(t) > 10:
        return True
    return False


# 한 번 인식하는 데 이보다 오래 걸리면 "이 PC에는 무겁다"고 보고 더 가벼운 모델로 바꾼다
SLOW_SECONDS = {"cuda": 1.5, "cpu": 1.6}


def _candidates(cfg):
    """(장치, compute_type, 모델) 후보를 좋은 것부터. size: auto | accurate | light"""
    dev = cfg.get("device", "auto")
    size = cfg.get("size", "auto")
    big, small = cfg.get("model", "large-v3-turbo"), cfg.get("cpu_model", "small")
    gpu, cpu = [], []
    ct = cfg.get("compute_type", "auto")
    if size == "accurate":
        gpu, cpu = [("cuda", ct, big)], [("cpu", "int8", small)]
    elif size == "light":
        gpu, cpu = [("cuda", ct, "small")], [("cpu", "int8", "base")]
    else:
        gpu, cpu = [("cuda", ct, big), ("cuda", ct, "small")], [("cpu", "int8", small), ("cpu", "int8", "base")]
    if gpu and dev in ("auto", "cuda"):
        from .runtime_env import cuda_files_ok
        if not cuda_files_ok():                     # GPU 가속 파일을 아직 안 받음 → CPU로
            print(t("log.no_cuda_files"))
            gpu = []
    return (gpu if dev in ("auto", "cuda") else []) + (cpu if dev in ("auto", "cpu") else [])


def _probe_cuda(path, ctype, timeout=180):
    """모델을 GPU로 올려 한 번 인식해 보는 걸 자식 프로세스에서 실행한다.
    CUDA/cuDNN 문제는 파이썬 예외 없이 프로세스를 통째로 죽이기 때문에, 본체가 같이 죽지 않게 한다."""
    import os
    import subprocess
    import sys
    if getattr(sys, "frozen", False):
        args, cwd = [sys.executable], None
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        args, cwd = [sys.executable, "-m", "vrc_translator.main"], root
    args += ["--probe-cuda", path, ctype]
    flags = 0x08000000 if sys.platform == "win32" else 0      # CREATE_NO_WINDOW
    try:
        r = subprocess.run(args, cwd=cwd, timeout=timeout, capture_output=True, text=True,
                           creationflags=flags)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    detail = ((r.stdout or "") + (r.stderr or "")).strip()[-300:]
    return r.returncode == 0, f"exit {r.returncode} {detail}".strip()


def probe_main(path, ctype):
    """--probe-cuda 로 실행됐을 때 (자식 프로세스)."""
    import numpy as np
    from faster_whisper import WhisperModel
    m = WhisperModel(path, device="cuda", compute_type=ctype)
    list(m.transcribe(np.zeros(16000 * 3, np.float32), language="ko", beam_size=1)[0])
    return 0


def _is_prompt_leak(text, hotwords):
    """조용한 구간에서 Whisper가 단어장 목록을 그대로 읊는 경우를 거른다."""
    if not hotwords or not text:
        return False
    words = {w.strip().lower() for w in hotwords if w.strip()}
    toks = [x for x in re.split(r"[\s,、。.!?！？]+", text.lower()) if x]
    return len(toks) >= 2 and all(x in words for x in toks)


class STT:
    def __init__(self, cfg):
        import os
        import time

        import numpy as np
        from faster_whisper import WhisperModel

        from faster_whisper.utils import download_model

        candidates = _candidates(cfg)
        auto = cfg.get("size", "auto") == "auto"
        self.model = None
        cuda_broken = False
        for i, (device, ctype, name) in enumerate(candidates):
            try:
                if device == "cuda":
                    if cuda_broken:
                        continue
                    import ctranslate2
                    if ctranslate2.get_cuda_device_count() == 0:
                        raise RuntimeError(t("log.no_cuda"))
                print(t("log.stt_load", name=name, dev=device.upper()))
                path = download_model(name)            # 다운로드는 GPU와 무관하게 먼저
                if device == "cuda":
                    print(t("log.cuda_probe"))
                    ok, detail = _probe_cuda(path, ctype)
                    if not ok:
                        cuda_broken = True
                        print(t("log.cuda_crash", detail=detail))
                        continue
                kw = {"cpu_threads": min(8, os.cpu_count() or 4)} if device == "cpu" else {}
                model = WhisperModel(path, device=device, compute_type=ctype, num_workers=2, **kw)
                # DLL 누락 같은 문제는 첫 추론 때 드러나므로 여기서 한 번 돌려본다
                silence = np.zeros(16000 * 3, np.float32)
                list(model.transcribe(silence, language="ko", beam_size=1)[0])
                t0 = time.time()                    # 두 번째 실행으로 실제 속도 측정
                list(model.transcribe(silence, language="ko", beam_size=1)[0])
                took = time.time() - t0
                nxt = candidates[i + 1] if i + 1 < len(candidates) else None
                if auto and took > SLOW_SECONDS[device] and nxt and nxt[0] == device:
                    print(t("log.stt_slow", name=name, dev=device.upper(), took=took, nxt=nxt[2]))
                    del model
                    continue
                self.model, self.device, self.name = model, device, name
                print(t("log.stt_ready", name=name, dev=device.upper(), took=took))
                break
            except Exception as e:
                print(t("log.stt_bad", dev=device.upper(), name=name, e=e))
        if self.model is None:
            raise SystemExit(t("log.stt_fail"))

        # CPU에서는 후보 비교(빔 서치)가 크게 느려지므로 1로
        self.beam = 1 if self.device == "cpu" else int(cfg.get("beam_size", 3))
        self.vad_filter = bool(cfg.get("vad_filter", True))
        self.allowed = cfg.get("allowed_langs") or None
        self.label = f"{'GPU' if self.device == 'cuda' else 'CPU'} · {self.name}"

    def lang_probs(self, audio, langs):
        """Whisper 언어 감지. langs 안에서만 비교한 확률 {언어: p} (합 1).
        Google 결과 여러 개 중 어느 언어를 쓸지 정할 때 쓴다 (인코더 1회라 가벼움)."""
        _, _, probs = self.model.detect_language(audio=audio)
        d = dict(probs or [])
        sub = {l: float(d.get(l, 0.0)) for l in langs}
        total = sum(sub.values())
        if total <= 0:
            return {}
        return {l: v / total for l, v in sub.items()}

    def _run(self, audio, lang, hotwords=None, beam=None):
        segs, info = self.model.transcribe(
            audio, language=lang, beam_size=beam or self.beam,
            condition_on_previous_text=False, without_timestamps=True,
            vad_filter=self.vad_filter, hotwords=hotwords or None,
        )
        return list(segs), info

    def transcribe_ex(self, audio, lang=None, allowed=None, hotwords=None, beam=None, who=""):
        text, got = self.transcribe(audio, lang, allowed, hotwords=hotwords, beam=beam)
        return text, got, "PC"

    def transcribe(self, audio, lang=None, allowed=None, hotwords=None, beam=None):
        """(텍스트, 언어코드) 반환. 쓸 만한 게 없으면 텍스트는 "".
        언어를 모를 때는 감지와 인식을 한 번에 돌리고(인코더 1회),
        허용 언어(기본 한/일)가 아닌 걸로 감지된 드문 경우에만 다시 돌린다."""
        allowed = allowed or self.allowed
        if self.device == "cpu":
            beam = 1
        hw = ", ".join(hotwords) if hotwords else None
        segs, info = self._run(audio, lang, hw, beam)
        detected = lang or info.language
        if lang is None and allowed and detected not in allowed:
            probs = dict(info.all_language_probs or [])
            detected = max(allowed, key=lambda l: probs.get(l, 0.0))
            segs, info = self._run(audio, detected, hw, beam)

        # 확신이 낮거나(잡음·겹친 목소리) 같은 말이 반복되는 구간은 버린다
        parts = [s.text for s in segs
                 if not (s.no_speech_prob > 0.6 and s.avg_logprob < -0.8)
                 and s.avg_logprob > -1.2 and s.compression_ratio < 2.4]
        text = "".join(parts).strip()
        if is_hallucination(text) or _is_prompt_leak(text, hotwords):
            return "", detected
        return text, detected

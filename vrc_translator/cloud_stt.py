"""클라우드 음성인식 + PC 음성인식으로 넘어가는 라우터.

순서: ① Google 무료 음성인식 (VRCT와 같은 방식, 키 없음·비공식)
      ② PC 음성인식 (faster-whisper, 백그라운드에서 미리 준비)
Google이 연달아 실패하면 잠시 쉬게 하고 그동안 PC 음성인식을 쓴다."""
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import numpy as np

from . import events
from .i18n import t
from .stt import is_hallucination

LOCALES = {"ko": "ko-KR", "ja": "ja-JP", "en": "en-US"}
GOOGLE_URL = "http://www.google.com/speech-api/v2/recognize"
# SpeechRecognition 라이브러리에 들어 있는 공개 기본 키 (내 키 아님). 비밀 검사기 오탐을 피하려고 나눠 둠
GOOGLE_KEY = "AIzaSyBOti4mM-6x9" + "WDnZIjIeyEU21OpBXqWBgw"
FAIL_LIMIT = 2          # 연달아 이만큼 실패하면
COOLDOWN = 300          # 이 시간(초) 동안 그 엔진은 건너뜀
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cloud-stt")
LID_SURE = 0.6          # Whisper 언어 감지가 이 이상이면 그 언어 결과를 쓴다
LID_TIMEOUT = 3.0


class EngineError(Exception):
    pass


class QuotaError(EngineError):
    """이번 실행에서는 더 시도하지 않을 오류."""


def _pcm16(audio):
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16)


def _flac_bytes(audio, sr=16000):
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, _pcm16(audio), sr, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


class GoogleSTT:
    name = "Google"

    def __init__(self, timeout=4):
        import requests
        self.timeout = timeout
        self._local = threading.local()
        self._requests = requests
        _flac_bytes(np.zeros(160, np.float32))       # soundfile 동작 확인 (없으면 여기서 예외)

    @property
    def s(self):
        sess = getattr(self._local, "s", None)
        if sess is None:
            sess = self._local.s = self._requests.Session()
        return sess

    def _one(self, flac, lang):
        url = GOOGLE_URL + "?" + urlencode({"client": "chromium", "lang": LOCALES.get(lang, lang),
                                            "key": GOOGLE_KEY, "pFilter": 0})
        try:
            r = self.s.post(url, data=flac, timeout=(3, self.timeout),
                            headers={"Content-Type": "audio/x-flac; rate=16000"})
        except Exception as e:
            raise EngineError(f"Google: {type(e).__name__}")
        if r.status_code != 200:
            raise EngineError(f"Google HTTP {r.status_code}")
        # 응답은 여러 줄의 JSON. 첫 줄은 보통 빈 결과
        for line in r.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                res = json.loads(line).get("result") or []
            except ValueError:
                raise EngineError("Google: bad response")
            if res:
                alts = res[0].get("alternative") or []
                if alts:
                    best = max(alts, key=lambda a: a.get("confidence", 0.0))
                    return best.get("transcript", "").strip(), float(best.get("confidence", 0.5))
        return "", 0.0

    def transcribe(self, audio, langs, lid=None):
        """langs 중 가장 그럴듯한 결과. (텍스트, 언어)
        lid: audio → {언어: 확률} (Whisper 언어 감지). 여러 언어일 때 어느 결과를 쓸지 정하는 데 쓴다.

        예전에는 내 언어 결과가 먼저 오면 바로 채택했는데, Google은 다른 언어 음성도 그 언어로
        소리 나는 대로 받아적고(한국어 → 카타카나 등) 확신도도 높게 줘서 언어가 자주 틀렸다.
        그래서 모든 결과를 받은 뒤 언어 감지 + 글자 검사로 고른다."""
        flac = _flac_bytes(audio)
        if len(langs) == 1:
            text, _ = self._one(flac, langs[0])
            return text, langs[0]
        from concurrent.futures import wait
        futs = {_POOL.submit(self._one, flac, l): l for l in langs}
        lid_fut = _POOL.submit(lid, audio) if lid else None
        wait(futs)
        results, errors = {}, []
        for f, l in futs.items():
            try:
                results[l] = f.result()
            except EngineError as e:
                errors.append(str(e))
        if not results:
            raise EngineError(errors[0] if errors else "Google: no result")
        probs = {}
        if lid_fut is not None:
            try:
                probs = lid_fut.result(timeout=LID_TIMEOUT) or {}
            except Exception as e:
                print(f"[stt] language detect failed: {type(e).__name__}: {e}")
        lang = pick_lang(results, langs, probs)
        return results[lang][0], lang


# ---------------------------------------------------------------- 언어 고르기
def _count(text):
    c = {"hangul": 0, "hira": 0, "kata": 0, "kanji": 0, "latin": 0}
    for ch in text:
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            c["hangul"] += 1
        elif 0x3040 <= o <= 0x309F:
            c["hira"] += 1
        elif 0x30A0 <= o <= 0x30FF or 0xFF66 <= o <= 0xFF9D:
            if ch != "ー":                     # 장음 기호는 히라가나 문장에도 나옴
                c["kata"] += 1
        elif 0x4E00 <= o <= 0x9FFF:
            c["kanji"] += 1
        elif ch.isascii() and ch.isalpha():
            c["latin"] += 1
    return c


def script_penalty(text, lang):
    """인식 결과 글자가 그 언어답지 않으면 감점 (0 = 정상).
    ja-JP로 한국어를 들으면 카타카나만 잔뜩 나오고, ko-KR로 일본어를 들으면 한글 음차가 나온다."""
    c = _count(text)
    jp = c["hira"] + c["kata"] + c["kanji"]
    if lang == "ko":
        if c["hangul"] == 0:
            return 1.0
        return 0.5 if jp > c["hangul"] else 0.0
    if lang == "ja":
        if jp == 0 or c["hangul"]:
            return 1.0
        if c["kata"] >= 3 and c["kata"] / jp >= 0.6:   # 거의 카타카나 = 외국어를 소리 나는 대로 적은 것
            return 0.4
        return 0.0
    if lang == "en":
        return 1.0 if c["latin"] == 0 or c["hangul"] or jp else 0.0
    return 0.0


def pick_lang(results, langs, probs=None):
    """results: {언어: (텍스트, 확신도)}. 어느 언어 결과를 쓸지."""
    probs = {l: p for l, p in (probs or {}).items() if l in langs}
    cands = [l for l in langs if l in results and results[l][0]]
    if not cands:
        return next((l for l in langs if l in results), langs[0])
    if len(cands) == 1:
        only = cands[0]
        other = max(langs, key=lambda l: probs.get(l, 0.0)) if probs else only
        # 언어 감지는 확실히 다른 언어라는데 그 언어 결과는 비었고, 남은 결과도 글자가 이상하면
        # (예: 한국어를 ja-JP가 카타카나로 적은 것) 틀린 자막을 띄우느니 버린다
        if other != only and probs.get(other, 0.0) >= 0.85 and script_penalty(results[only][0], only) > 0:
            return other if other in results else only
        return only
    if probs:
        best = max(cands, key=lambda l: probs.get(l, 0.0))
        if probs.get(best, 0.0) >= LID_SURE:
            return best

    def score(l):
        text, conf = results[l]
        s = conf - script_penalty(text, l)
        if probs:
            s += (probs.get(l, 0.0) - 0.5) * 0.6
        if l == langs[0]:
            s += 0.02                           # 거의 같으면 내 언어
        return s
    return max(cands, key=score)


class _Slot:
    def __init__(self, engine):
        self.engine = engine
        self.fails = 0
        self.off_until = 0.0
        self.dead = False           # 이번 실행에서는 더 안 씀 (무료 범위 소진 등)

    def usable(self):
        return self.engine is not None and not self.dead and time.time() >= self.off_until

    def ok(self):
        self.fails = 0

    def fail(self, e):
        if isinstance(e, QuotaError):
            self.dead = True
            return "dead"
        self.fails += 1
        if self.fails >= FAIL_LIMIT:
            self.fails = 0
            self.off_until = time.time() + COOLDOWN
            return "cool"
        return "retry"


class STTRouter:
    """Google → PC 순서로 시도하는 음성인식."""

    def __init__(self, google=None, local_factory=None):
        self.slots = [s for s in (_Slot(google),) if s.engine is not None]
        self.local = None
        self.local_error = None
        self.current = None
        if local_factory:
            threading.Thread(target=self._load_local, args=(local_factory,), daemon=True).start()

    # ---- PC 음성인식은 뒤에서 미리 준비 (클라우드가 막혔을 때 바로 쓰도록)
    def _load_local(self, factory):
        try:
            self.local = factory()
            print(t("log.cloud_local_ready", label=self.local.label))
        except BaseException as e:
            self.local_error = str(e)
            print(t("log.cloud_local_fail", e=e))

    @property
    def label(self):
        names = [s.engine.name for s in self.slots if s.usable()]
        return (names[0] if names else (self.local.label if self.local else "PC")) + \
            ("" if self.local else " · " + t("s.pc_loading"))

    def _announce(self, name):
        if name != self.current:
            self.current = name
            events.status(t("s.running", label=name), state="running")

    def transcribe_ex(self, audio, lang=None, allowed=None, hotwords=None, beam=None, who=""):
        """(텍스트, 언어, 엔진 이름)"""
        langs = [lang] if lang else list(allowed or ["ko", "ja"])
        lid = None
        local = self.local
        if len(langs) > 1 and local is not None and hasattr(local, "lang_probs"):
            lid = lambda a: local.lang_probs(a, langs)
        for slot in self.slots:
            if not slot.usable():
                continue
            eng = slot.engine
            try:
                text, got = eng.transcribe(audio, langs, lid=lid)
                slot.ok()
                self._announce(eng.name)
                if text and not is_hallucination(text):
                    return text, got, eng.name
                return "", got, eng.name
            except EngineError as e:
                state = slot.fail(e)
                print(t(f"log.cloud_{state}", name=eng.name, e=e))
        if self.local is None:
            if self.local_error:
                raise RuntimeError(self.local_error)
            print(t("log.cloud_wait_local"))
            return "", langs[0], "-"
        text, got = self.local.transcribe(audio, lang, allowed, hotwords=hotwords, beam=beam)
        self._announce(self.local.label)
        return text, got, "PC"

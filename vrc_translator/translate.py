"""번역 엔진 (DeepL / 번역 서버 / Google 무료)."""
import os
from .i18n import t

DEEPL_TARGET = {"en": "EN-US", "pt": "PT-BR", "zh": "ZH-HANS"}


FORMALITY_LANGS = {"ja", "de", "fr", "it", "es", "nl", "pl", "pt", "ru"}
# DeepL custom_instructions 지원 대상 언어
INSTRUCTION_LANGS = {"ja", "ko", "en", "de", "es", "fr", "it", "zh"}
# 고품질 모드에서 DeepL에 주는 번역 지시 (relay/server.js 와 같은 내용)
INSTRUCTIONS = [
    "The text is casual spoken conversation from a VRChat voice chat, transcribed by speech "
    "recognition. It may contain recognition errors; translate the intended meaning.",
    "Keep the speaker's tone and natural spoken style, short like a chat message.",
    "Translate onomatopoeia, mimetic words and slang into natural equivalents instead of "
    "transliterating them.",
    "Keep people's names and usernames as they are.",
]


class DeepLTranslator:
    def __init__(self, key, formality="default", quality=True):
        import deepl
        self._deepl = deepl
        self.t = deepl.Translator(key)
        self.formality = formality or "default"
        self.quality = quality
        self.instructions_ok = True     # 계정에서 지원 안 하면 자동으로 끔

    def translate(self, text, src, tgt, context=None):
        target = DEEPL_TARGET.get(tgt, tgt.upper())
        base = {"target_lang": target}
        if src:
            base["source_lang"] = src.upper()
        if self.formality != "default" and tgt in FORMALITY_LANGS:
            base["formality"] = self.formality
        if context:
            base["context"] = context          # 앞 대화 (번역 안 되고, 글자 수도 안 셈)
        tries = []
        if self.quality:
            q = {**base, "model_type": "prefer_quality_optimized"}
            if self.instructions_ok and tgt in INSTRUCTION_LANGS:
                tries.append({**q, "custom_instructions": INSTRUCTIONS})
            tries.append(q)
        else:
            tries.append({**base, "model_type": "latency_optimized"})
        tries.append({"target_lang": target, **({"formality": base["formality"]} if "formality" in base else {})})
        last = None
        for i, kw in enumerate(tries):
            try:
                return self.t.translate_text(text, **kw).text
            except self._deepl.AuthorizationException:
                raise
            except self._deepl.QuotaExceededException:
                raise
            except self._deepl.DeepLException as e:
                last = e
                if "custom_instructions" in kw:
                    self.instructions_ok = False
                    print(f"[deepl] 번역 지시(custom_instructions)를 쓸 수 없어 끕니다: {e}")
        raise last


class RelayError(RuntimeError):
    pass


class RelayTranslator:
    """Oracle 서버의 번역 릴레이(relay/server.js)를 거쳐 DeepL을 사용."""

    def __init__(self, url, token, formality="default", quality=True):
        import threading
        self.url = url.rstrip("/")
        self.token = token
        self._local = threading.local()       # 스레드마다 keep-alive 세션 하나씩
        self.formality = formality or "default"
        self.quality = quality

    @property
    def s(self):
        sess = getattr(self._local, "session", None)
        if sess is None:
            import requests
            sess = requests.Session()
            sess.headers["Authorization"] = f"Bearer {self.token}"
            self._local.session = sess
        return sess

    def _check(self, r):
        if r.status_code == 401:
            raise RelayError(t("e.token"))
        if r.status_code == 429:
            err = (r.json() or {}).get("error", "")
            if err in ("quota_reserved", "deepl_quota"):
                raise RelayError(t("e.monthly"))
            if err == "daily_limit":
                raise RelayError(t("e.daily"))
            raise RelayError(t("e.rate"))
        r.raise_for_status()
        return r.json()

    def usage(self):
        return self._check(self.s.get(self.url + "/usage", timeout=(3, 6)))

    def translate(self, text, src, tgt, context=None):
        body = {"text": text[:500], "target_lang": DEEPL_TARGET.get(tgt, tgt.upper()),
                "quality": bool(self.quality)}
        if src:
            body["source_lang"] = src.upper()
        if self.formality != "default" and tgt in FORMALITY_LANGS:
            body["formality"] = self.formality
        if context:
            body["context"] = context[-1500:]
        # (연결 3초, 응답 10초) — 응답이 없으면 예외로 끊어서 다음 발화가 막히지 않게
        return self._check(self.s.post(self.url + "/translate", json=body, timeout=(3, 10)))["text"]


class GoogleFreeTranslator:
    """Google 번역 무료 방식 (비공식, 키 없음). 아시아 서버라 빠르지만 말투·맥락 조절은 없다.
    두 가지 무료 주소를 번갈아 쓴다: 한쪽이 요청 제한(429)에 걸리면 다른 쪽으로."""
    name = "Google"
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    ENDPOINTS = ("gtx", "dict")

    def __init__(self, timeout=5):
        import threading
        import requests
        self._requests = requests
        self._local = threading.local()
        self.timeout = timeout
        self.lock = threading.Lock()
        self.rest_until = {e: 0.0 for e in self.ENDPOINTS}   # 429 받은 주소는 잠깐 쉼

    @property
    def s(self):
        sess = getattr(self._local, "s", None)
        if sess is None:
            sess = self._local.s = self._requests.Session()
            sess.headers["User-Agent"] = self.UA      # 기본값(python-requests)은 금방 차단됨
        return sess

    def _gtx(self, text, src, tgt):
        r = self.s.get("https://translate.googleapis.com/translate_a/single",
                       params={"client": "gtx", "sl": src or "auto", "tl": tgt, "dt": "t", "q": text},
                       timeout=(2, self.timeout))
        if r.status_code != 200:
            return None, r.status_code
        data = r.json()
        return "".join(seg[0] for seg in (data[0] or []) if seg and seg[0]).strip(), 200

    def _dict(self, text, src, tgt):
        r = self.s.get("https://clients5.google.com/translate_a/t",
                       params={"client": "dict-chrome-ex", "sl": src or "auto", "tl": tgt, "q": text},
                       timeout=(2, self.timeout))
        if r.status_code != 200:
            return None, r.status_code
        data = r.json()
        # 응답 모양이 버전마다 달라서 유연하게: ["번역"] / [["번역", "ko"]] / {"sentences": [...]}
        if isinstance(data, dict):
            out = "".join(x.get("trans", "") for x in data.get("sentences", []))
        else:
            first = data[0] if data else ""
            out = first[0] if isinstance(first, list) else first
        return (out or "").strip(), 200

    def translate(self, text, src, tgt, context=None):
        import time
        errors = []
        order = sorted(self.ENDPOINTS, key=lambda e: self.rest_until[e])   # 쉬는 중이 아닌 쪽 먼저
        for ep in order:
            if time.time() < self.rest_until[ep] and ep != order[-1]:
                continue
            try:
                out, code = (self._gtx if ep == "gtx" else self._dict)(text, src, tgt)
            except Exception as e:
                errors.append(f"{ep}: {type(e).__name__}")
                continue
            if code == 200 and out:
                return out
            if code == 429:
                with self.lock:
                    self.rest_until[ep] = time.time() + 120
            errors.append(f"{ep}: HTTP {code}" if code != 200 else f"{ep}: empty")
        raise RuntimeError("Google Translate " + ", ".join(errors))


class FallbackTranslator:
    """빠른 번역기를 먼저 쓰고, 실패하면 DeepL(또는 번역 서버)로. 연달아 실패하면 5분간 쉼."""

    def __init__(self, primary, secondary=None):
        import threading
        self.primary, self.secondary = primary, secondary
        self.fails = 0
        self.off_until = 0.0
        self.lock = threading.Lock()

    def translate_ex(self, text, src, tgt, context=None):
        import time
        if time.time() >= self.off_until:
            try:
                out = self.primary.translate(text, src, tgt, context=context)
                with self.lock:
                    self.fails = 0
                return out, getattr(self.primary, "name", "Google")
            except Exception as e:
                with self.lock:
                    self.fails += 1
                    if self.fails >= 2:
                        self.fails, self.off_until = 0, time.time() + 300
                print(t("log.tr_fallback", name=getattr(self.primary, "name", "?"), e=e))
                if self.secondary is None:
                    raise
        if self.secondary is None:
            raise RuntimeError(t("log.tr_no_fallback"))
        return self.secondary.translate(text, src, tgt, context=context), "DeepL"

    def translate(self, text, src, tgt, context=None):
        return self.translate_ex(text, src, tgt, context=context)[0]


def translate_ex(translator, text, src, tgt, context=None):
    """(번역문, 사용한 엔진 이름) — DeepL 사용량을 셀 때 구분하려고."""
    if hasattr(translator, "translate_ex"):
        return translator.translate_ex(text, src, tgt, context=context)
    return translator.translate(text, src, tgt, context=context), "DeepL"


def has_deepl(cfg):
    engine = cfg.get("engine", "deepl")
    if engine == "relay":
        return bool(cfg.get("relay_url", "").strip() and cfg.get("relay_token", "").strip())
    if engine == "deepl":
        return bool(cfg.get("deepl_api_key") or os.environ.get("DEEPL_API_KEY"))
    return False


def make_translator(cfg):
    base = _make_base(cfg) if has_deepl(cfg) else None
    if cfg.get("provider", "deepl") == "google":
        return FallbackTranslator(GoogleFreeTranslator(), base)
    if base is None:
        return _make_base(cfg)          # 설정이 없다는 오류를 그대로 보여준다
    return base


def _make_base(cfg):
    engine = cfg.get("engine", "deepl")
    if engine == "deepl":
        key = cfg.get("deepl_api_key") or os.environ.get("DEEPL_API_KEY")
        if not key:
            raise SystemExit("DeepL API 키가 없습니다 (config.yaml 또는 DEEPL_API_KEY).")
        return DeepLTranslator(key, cfg.get("formality", "default"), cfg.get("quality", True))
    if engine == "relay":
        url, token = cfg.get("relay_url", "").strip(), cfg.get("relay_token", "").strip()
        if not url or not token:
            raise SystemExit("번역 서버 주소/토큰이 없습니다.")
        return RelayTranslator(url, token, cfg.get("formality", "default"), cfg.get("quality", True))
    raise SystemExit(f"알 수 없는 번역 엔진: {engine}")

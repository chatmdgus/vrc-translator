"""설정: 기본값 + %APPDATA%\\VRCTranslator\\config.yaml (사용자 설정)."""
import copy
import os
import sys
from pathlib import Path

import yaml

DEFAULTS = {
    "my_lang": "ko",                   # 내 언어 (ko | ja): 내가 말하는 언어 = 자막 언어
    "translator": {
        "provider": "deepl",           # deepl(자연스러움) | google(빠름, 실패하면 DeepL)
        "engine": "deepl",             # DeepL 연결 방식: deepl(키 직접) | relay(번역 서버)
        "deepl_api_key": "",
        "relay_url": "",               # 예: http://서버IP:8787
        "relay_token": "",
        "quality": True,               # 고품질 모델 + 대화 맥락 + 구어체 지시 (조금 느림)
        "formality": "default",        # 일본어 말투: default | prefer_less(반말) | prefer_more(존댓말)
    },
    "stt": {                           # 음성인식 방식
        "mode": "cloud",               # cloud(Google → 안 되면 PC) | local(PC만)
        "timeout": 4,
    },
    "whisper": {
        "device": "auto",              # auto | cuda | cpu
        "size": "auto",                # auto(PC 성능에 맞춰 자동) | accurate | light
        "model": "large-v3-turbo",     # GPU용
        "cpu_model": "small",          # CPU로 떨어졌을 때
        "compute_type": "auto",
        "beam_size": 3,                # 1=가장 빠름, 3~5=정확도↑ (주변 말)
        "mic_beam_size": 5,            # 내 말은 정확도 우선
        "vad_filter": True,
        "allowed_langs": ["ko", "ja"],   # 한/일만 인식 (오감지 방지)
    },
    "vad": {
        "aggressiveness": 2,
        "energy_floor": 0.005,
        "min_speech_ms": 300,
        "silence_ms": 500,             # 말이 끝났다고 판단하는 무음 길이 (설정 창 「반응 속도」)
        "max_utterance_s": 12,
    },
    "mic": {
        "enabled": True,
        "device": None,
        "source_lang": None,
        "target_lang": "ja",
        "speak": "both",               # 내가 말하는 언어: both(내 언어 + 챗박스 언어 자동 판별) | mine(내 언어만, 조금 더 빠름)
        "chatbox_show_original": True,  # 챗박스에 번역문 + 아래 줄에 원문
        "typing_indicator": True,       # 말하는 동안 챗박스에 '입력 중' 표시
        "respect_vrchat_mute": True,
        "osc_host": "127.0.0.1",
        "osc_send_port": 9000,
        "osc_listen_port": 9001,
        "chatbox_interval_s": 1.6,
    },
    "listen": {
        "enabled": True,
        "device": None,
        "source_lang": None,
        "target_lang": "ko",
        "show_original": True,
        "langs": ["ja"],               # 번역해서 보여줄 주변 언어 (ko / ja / en 중 내 언어 제외)
        "vad": {"aggressiveness": 3, "min_speech_ms": 400},   # 월드 BGM·잡음 조각 줄이기
    },
    "vocab": {                         # 단어장 (설정 창 「단어장」)
        "mic": [],                     # 내 말 인식 힌트: 이름, 자주 쓰는 말, 신조어
        "listen": [],                  # 주변 말 인식 힌트
        "fix": [],                     # 고쳐 쓰기 [[틀린 말, 맞는 말], ...]
    },
    "ui": {
        "lang_chosen": False,          # 첫 실행 때 한국어/일본어 고르기 화면을 봤는지
        "topmost": False,              # 메인 창 맨 앞 고정
        "subtitle_pos": None,          # 자막 창 위치 (자동 저장)
        "geometry": None,              # 메인 창 크기/위치 (자동 저장)
    },
    "overlay": {
        "mode": "desktop",
        "max_lines": 3,
        "line_timeout_s": 10,
        "font_size": 20,               # 설정 창 「자막 크기」
        "desktop_width": 720,
        "vr_width_m": 0.9,
        "vr_offset": [0.0, -0.35, -1.2],
        "fonts": {
            "ko": "C:/Windows/Fonts/malgun.ttf",
            "ja": "C:/Windows/Fonts/YuGothM.ttc",
            "en": "C:/Windows/Fonts/segoeui.ttf",
            "default": "C:/Windows/Fonts/malgun.ttf",
        },
    },
}

HEADER = ("# VRC Translator 설정 파일\n"
          "# 주요 항목은 실행 시 뜨는 설정 창에서 바꿀 수 있습니다.\n"
          "# 기본값과 다른 항목만 저장됩니다. 고급 항목(VAD 감도 등)은 README를 보고 필요한 것만 추가하세요.\n\n")


_MASK = b"vrc-translator-preset"


def encode_preset(raw: bytes) -> bytes:
    """exe 폴더에 토큰이 평문으로 보이지 않게 하는 용도 (암호화가 아님)."""
    import base64
    import zlib
    z = zlib.compress(raw)
    return base64.b64encode(bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(z)))


def decode_preset(data: bytes) -> bytes:
    import base64
    import zlib
    z = base64.b64decode(data)
    return zlib.decompress(bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(z)))


def _load_preset():
    """exe에 동봉된 preset.dat (개발 중에는 프로젝트 루트의 preset.yaml)."""
    if getattr(sys, "frozen", False):
        p = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "preset.dat"
        if not p.exists():
            return {}
        return yaml.safe_load(decode_preset(p.read_bytes()).decode("utf-8")) or {}
    p = Path(__file__).resolve().parent.parent / "preset.yaml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


PRESET = _load_preset()
# 프리셋이 번역 설정을 고정하면 설정 창에서 번역 항목을 숨기고 사용자 설정보다 우선한다
TRANSLATOR_LOCKED = bool(PRESET.get("lock_translator")) and "translator" in PRESET


def config_path():
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "VRCTranslator" / "config.yaml"


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


CONFIG_VERSION = 2
# 예전 버전은 기본값까지 전부 파일에 저장했기 때문에, 그 시절 기본값 그대로인 항목은 지워서
# 새 기본값이 적용되게 한다.
_OLD_DEFAULTS = {
    ("whisper", "beam_size"): 1,
    ("overlay", "font_size"): 28,
    ("overlay", "desktop_width"): 900,
    ("overlay", "max_lines"): 4,
    ("vad", "silence_ms"): 600,
}


def _migrate(user):
    lis = user.get("listen")
    if isinstance(lis, dict) and "english" in lis:     # 예전 '영어도 인식' 옵션 → 듣기 언어 목록
        if lis.pop("english") and "langs" not in lis:
            lis["langs"] = ["ja", "en"]
    if int(user.get("config_version", 1)) >= CONFIG_VERSION:
        return user
    for (sec, key), old in _OLD_DEFAULTS.items():
        if isinstance(user.get(sec), dict) and user[sec].get(key) == old:
            del user[sec][key]
    return user


def _diff(cfg, base):
    """기본값과 다른 항목만 남긴다 (기본값이 바뀌면 자동으로 따라가도록)."""
    out = {}
    for k, v in cfg.items():
        b = base.get(k) if isinstance(base, dict) else None
        if isinstance(v, dict) and isinstance(b, dict):
            d = _diff(v, b)
            if d:
                out[k] = d
        elif k not in (base or {}) or v != b:
            out[k] = v
    return out


def load(path=None):
    p = Path(path) if path else config_path()
    user = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            user = _migrate(yaml.safe_load(f) or {})
    user.pop("config_version", None)
    base = deep_merge(DEFAULTS, {k: v for k, v in PRESET.items() if k != "lock_translator"})
    cfg = deep_merge(base, user)
    if TRANSLATOR_LOCKED:
        t = deep_merge(DEFAULTS["translator"], PRESET["translator"])
        # 말투는 받은 사람이 설정 창에서 고를 수 있게 사용자 값 유지
        for k in ("formality", "quality", "provider"):
            if k in (user.get("translator") or {}):
                t[k] = user["translator"][k]
        cfg["translator"] = t
    return cfg


def save(cfg, path=None):
    p = Path(path) if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    base = deep_merge(DEFAULTS, {k: v for k, v in PRESET.items() if k != "lock_translator"})
    data = _diff(copy.deepcopy(cfg), base)
    if TRANSLATOR_LOCKED:
        # 동봉된 서버 정보는 사용자 파일에 쓰지 않음 (말투·품질만 저장)
        t = {k: cfg["translator"][k] for k in ("formality", "quality", "provider") if k in cfg["translator"]}
        data["translator"] = t
    data = {"config_version": CONFIG_VERSION, **data}
    with open(p, "w", encoding="utf-8") as f:
        f.write(HEADER)
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return p

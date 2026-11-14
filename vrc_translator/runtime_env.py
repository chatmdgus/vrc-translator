"""exe(PyInstaller)로 묶였을 때와 일반 실행 모두에서 CUDA DLL을 찾을 수 있게 경로를 잡아준다.
ctranslate2(faster-whisper)를 import하기 전에 호출해야 한다."""
import os
import sys
from pathlib import Path


def _candidate_roots():
    if getattr(sys, "frozen", False):
        yield Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    for p in sys.path:
        if p and p.endswith("site-packages"):
            yield Path(p)


def setup_dll_paths():
    # Hugging Face 경고/진행바 정리 (모델 다운로드 자체는 그대로 동작)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")      # 일반 HTTP 다운로드 사용 (exe에서 더 안정적)
    if sys.platform != "win32":
        return
    found = []
    for root in _candidate_roots():
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin"):
            d = root / sub
            if d.is_dir() and str(d) not in found:
                found.append(str(d))
    for d in found:
        os.add_dll_directory(d)
    if found:
        os.environ["PATH"] = os.pathsep.join(found + [os.environ.get("PATH", "")])


def cuda_files_ok():
    """exe에 GPU 가속 파일(CUDA DLL)이 들어 있는지. 처음 받는 zip에는 없고 앱 안에서 따로 받는다.
    exe가 아니면(개발 실행) 그냥 True."""
    if not getattr(sys, "frozen", False):
        return True
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return all(any((base / sub).glob("*.dll")) for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"))

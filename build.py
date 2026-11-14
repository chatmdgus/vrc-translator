"""exe 빌드 스크립트 (Windows에서 실행). build.bat 이 호출한다.
  python build.py                          → GPU(CUDA) 동봉 버전
  python build.py cpu                      → CPU 전용 (용량 작음)
  python build.py --preset preset.yaml     → 서버 주소/토큰을 넣은 지인 배포용
  python build.py --console                → 콘솔 창도 띄우는 디버그 빌드
  python build.py --zip                    → 폴더 전체를 zip 으로도 묶음 (예전 방식 배포용)
배포(다운로드 사이트 + 앱 안 업데이트)는 빌드 후 release.py 로."""
import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("mode", nargs="?", choices=["gpu", "cpu"], default="gpu")
ap.add_argument("--preset", help="exe에 동봉할 프리셋 파일 (서버 주소/토큰)")
ap.add_argument("--console", action="store_true", help="디버그용: 콘솔 창도 같이 띄움")
ap.add_argument("--zip", action="store_true", help="폴더 전체를 zip 으로도 묶기")
opt = ap.parse_args()
cpu_only = opt.mode == "cpu"
name = "VRCTranslator" + ("-Friends" if opt.preset else "") + ("-CPU" if cpu_only else "")

args = [
    str(ROOT / "app.py"),
    "--name", name,
    "--onedir", "--console" if opt.console else "--windowed", "--noconfirm", "--clean",
    "--collect-data", "faster_whisper",      # Silero VAD onnx
    "--collect-binaries", "ctranslate2",
    "--collect-binaries", "onnxruntime",
    "--collect-binaries", "openvr",
    "--collect-data", "_soundfile_data",      # 클라우드 음성인식용 FLAC 인코더(libsndfile)
    "--hidden-import", "soundfile",
    "--additional-hooks-dir", str(ROOT / "hooks"),   # webrtcvad 훅 교체
    "--hidden-import", "pyaudiowpatch",
]

if opt.preset:
    preset = Path(opt.preset).resolve()
    if not preset.exists():
        sys.exit(f"프리셋 파일이 없습니다: {preset}")
    sys.path.insert(0, str(ROOT))
    import yaml
    from vrc_translator.settings import encode_preset
    raw = preset.read_bytes()
    t = (yaml.safe_load(raw.decode("utf-8")) or {}).get("translator", {})
    if t.get("engine") == "relay" and not str(t.get("relay_token", "")).strip():
        sys.exit("preset.yaml 의 relay_token 이 비어 있습니다.")
    if str(t.get("deepl_api_key", "")).strip():
        sys.exit("preset.yaml 에 deepl_api_key 가 들어 있습니다. 배포용에는 DeepL 키를 넣지 마세요.")
    # 토큰이 exe 폴더에서 평문으로 보이지 않게 변환해서 동봉 (preset.dat)
    tmp = ROOT / ".preset-tmp" / "preset.dat"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(encode_preset(raw))
    args += ["--add-data", f"{tmp}{os.pathsep}."]

if not cpu_only:
    for pkg in ("nvidia.cublas", "nvidia.cudnn"):
        spec = importlib.util.find_spec(pkg)
        if spec is None:
            sys.exit(f"{pkg} 가 설치되어 있지 않습니다: pip install -r requirements-build.txt")
        bindir = Path(list(spec.submodule_search_locations)[0]) / "bin"
        dest = pkg.replace(".", "/") + "/bin"
        dlls = list(bindir.glob("*.dll"))
        if not dlls:
            sys.exit(f"{bindir} 에 DLL이 없습니다.")
        for dll in dlls:
            args += ["--add-binary", f"{dll}{os.pathsep}{dest}"]

PyInstaller.__main__.run(args)

out = ROOT / "dist" / name
shutil.copy(ROOT / "README.md", out / "README.md")
print(f"\n완료: {out}\\{name}.exe")
if opt.zip:
    zip_path = shutil.make_archive(str(ROOT / "dist" / name), "zip", out.parent, name)
    print(f"zip: {zip_path}")
else:
    print("배포: python release.py --site <kuruma-downloads 폴더>")

"""빌드 결과를 다운로드 사이트(kuruma-downloads)에 올릴 형태로 만든다. build.bat 다음에 실행.

  python release.py --site C:\\dev\\kuruma-downloads
      [--dist dist\\VRCTranslator-Friends] [--out dist\\upload] [--rebuild] [--full] [--fast] [--force]

만드는 것
  사이트 저장소 files/ (git 으로 커밋·푸시)
    translator-latest.json   매니페스트: 버전, 모든 파일 sha256, 묶음 주소. 앱이 이걸 보고 업데이트
    translator-app.zip       exe 등 자주 바뀌는 작은 파일 (보통 업데이트는 이것만 받음)
  --out 폴더 (GitHub 릴리스 translator-latest 에 직접 올림, 바뀐 것만 생김)
    VRCTranslator-Friends.zip  처음 받는 사람용 (GPU 파일 제외)
    translator-core.zip        파이썬·라이브러리 묶음
    translator-gpu.zip         NVIDIA GPU용 CUDA 파일 묶음 (원하는 사람만 앱 안에서 받음)

core·gpu 는 안에 든 파일이 하나도 안 바뀌었으면 다시 만들지 않고 예전 것을 그대로 쓴다.
작은 파일 몇 개만 바뀌었으면 그 파일만 app 묶음에 같이 넣는다 (core 를 다시 올리지 않아도 됨).

--rebuild  core·gpu 를 무조건 새로 만듦      --full  처음 받는 사람용 zip 도 새로 만듦
--fast     압축을 빠르게(대신 큼)              --force 버전을 안 올려도 진행
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE_URL = "https://chatmdgus.github.io/kuruma-downloads/"
RELEASE_TAG = "translator-latest"
RELEASE_URL = f"https://github.com/chatmdgus/kuruma-downloads/releases/download/{RELEASE_TAG}/"
MANIFEST_FILE = "translator-latest.json"
APP_ZIP = "translator-app.zip"
GPU_PREFIX = "_internal/nvidia/"
LOCAL_MANIFEST = "_internal/vrc_manifest.json"
PATCH_MAX = 20 << 20          # 이보다 작은 바뀐 파일은 app 묶음에 끼워 넣기
PATCH_TOTAL_MAX = 80 << 20    # 끼워 넣은 게 이만큼 넘으면 core 를 새로 만듦
SKIP_SUFFIX = (".vrcold", ".part")


def version():
    src = (ROOT / "vrc_translator" / "version.py").read_text(encoding="utf-8")
    return re.search(r'VERSION\s*=\s*"([^"]+)"', src).group(1)


def vtuple(v):
    return tuple(int("".join(c for c in p if c.isdigit()) or 0) for p in str(v).lstrip("v").split("."))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(dist):
    files = {}
    for p in sorted(dist.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(dist).as_posix()
        if rel == LOCAL_MANIFEST or rel.startswith("_update/") or rel.endswith(SKIP_SUFFIX):
            continue
        files[rel] = (sha256(p), p.stat().st_size)
    return files


def mb(n):
    return f"{n / (1 << 20):,.1f} MB"


def write_zip(path, dist, rels, method, top=None, extra=None):
    kw = {"compresslevel": 9} if method == zipfile.ZIP_DEFLATED else {}
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", method, **kw) as z:
        for rel in sorted(rels):
            z.write(dist / rel, f"{top}/{rel}" if top else rel)
        for rel, data in (extra or {}).items():
            z.writestr(f"{top}/{rel}" if top else rel, data)
    tmp.replace(path)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="kuruma-downloads 저장소 폴더")
    ap.add_argument("--dist", default=str(ROOT / "dist" / "VRCTranslator-Friends"))
    ap.add_argument("--out", default=str(ROOT / "dist" / "upload"))
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--force", action="store_true")
    opt = ap.parse_args()

    dist, site, out = Path(opt.dist).resolve(), Path(opt.site).resolve(), Path(opt.out).resolve()
    name = dist.name
    exe = f"{name}.exe"
    if not (dist / exe).exists():
        sys.exit(f"{dist}\\{exe} 가 없어요. 먼저 build.bat 으로 빌드하세요.")
    if not (site / "files").is_dir():
        sys.exit(f"{site}\\files 가 없어요. --site 에 kuruma-downloads 저장소 폴더를 주세요.")
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.zip"):
        old.unlink()

    ver = version()
    mpath = site / "files" / MANIFEST_FILE
    prev = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    if prev and prev.get("app") != name:
        sys.exit(f"사이트의 매니페스트는 {prev.get('app')} 용이에요 (지금 빌드: {name}).")
    if prev and vtuple(ver) <= vtuple(prev["version"]) and not opt.force:
        sys.exit(f"vrc_translator/version.py 의 버전({ver})을 지난 배포({prev['version']})보다 올려 주세요.")

    print(f"파일 검사 중… {dist}")
    files = scan(dist)
    app_set = {exe, "README.md", "_internal/base_library.zip", "_internal/preset.dat"}
    pf = (prev or {}).get("files", {})
    pp = (prev or {}).get("packs", {})

    def kind_of(rel):
        return "gpu" if rel.startswith(GPU_PREFIX) else "core"

    # core·gpu 를 새로 만들어야 하는지
    rebuild = {"core": opt.rebuild or "core" not in pp, "gpu": opt.rebuild or "gpu" not in pp}
    patches = {}
    for rel, (h, size) in files.items():
        if rel in app_set:
            continue
        k = kind_of(rel)
        if pf.get(rel) == [h, size, k]:
            continue                                   # 예전 묶음에 그대로 있음
        if k == "gpu" or size > PATCH_MAX:
            rebuild[k] = True
        else:
            patches[rel] = size
    if not rebuild["core"] and sum(patches.values()) > PATCH_TOTAL_MAX:
        rebuild["core"] = True
    if rebuild["core"]:
        patches = {}                                   # core 를 새로 만들면 끼워 넣을 필요 없음
    has_gpu = any(r.startswith(GPU_PREFIX) for r in files)
    if not has_gpu:
        rebuild["gpu"] = False

    assign = {}
    for rel in files:
        if rel in app_set or rel in patches:
            assign[rel] = "app"
        else:
            assign[rel] = kind_of(rel)

    lzma = zipfile.ZIP_DEFLATED if opt.fast else zipfile.ZIP_LZMA
    packs = {}
    uploads = []

    # app (사이트 저장소에 커밋)
    app_rels = [r for r, k in assign.items() if k == "app"]
    app_zip = write_zip(site / "files" / APP_ZIP, dist, app_rels, zipfile.ZIP_DEFLATED)
    ah = sha256(app_zip)
    packs["app"] = {"url": f"{SITE_URL}files/{APP_ZIP}?v={ah[:12]}", "sha256": ah,
                    "size": app_zip.stat().st_size}

    for k in ("core", "gpu"):
        rels = [r for r, kk in assign.items() if kk == k]
        if not rels:
            continue
        if rebuild[k]:
            print(f"{k} 묶음 압축 중… ({len(rels)}개, {mb(sum(files[r][1] for r in rels))}) — 몇 분 걸릴 수 있어요")
            zp = write_zip(out / f"translator-{k}.zip", dist, rels, lzma)
            packs[k] = {"url": RELEASE_URL + zp.name, "sha256": sha256(zp), "size": zp.stat().st_size}
            uploads.append(zp)
        else:
            packs[k] = dict(pp[k])
        if k == "gpu":
            packs[k]["optional"] = True

    manifest = {
        "app": name,
        "version": ver,
        "exe": exe,
        "date": datetime.date.today().isoformat(),
        "update_url": f"{SITE_URL}files/{MANIFEST_FILE}",
        "packs": packs,
        "files": {rel: [files[rel][0], files[rel][1], assign[rel]] for rel in sorted(files)},
    }

    # 처음 받는 사람용 zip (GPU 제외). core 가 바뀌었을 때만 새로 (예전 zip 으로 받아도 켜면 업데이트됨)
    if rebuild["core"] or opt.full:
        first = {**manifest, "installed": ["app", "core"]}
        rels = [r for r, k in assign.items() if k != "gpu"]
        print(f"처음 받는 사람용 zip 만드는 중… ({mb(sum(files[r][1] for r in rels))})")
        zp = write_zip(out / f"{name}.zip", dist, rels, zipfile.ZIP_DEFLATED, top=name,
                       extra={LOCAL_MANIFEST: json.dumps(first, ensure_ascii=False)})
        uploads.insert(0, zp)

    # 이 빌드 폴더도 그대로 업데이트되게 (본인 테스트용)
    local = {**manifest, "installed": ["app", "core"] + (["gpu"] if has_gpu else [])}
    (dist / LOCAL_MANIFEST).write_text(json.dumps(local, ensure_ascii=False), encoding="utf-8")
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # ---- 결과
    changed = [r for r in files if pf.get(r, [None])[0] != files[r][0]]
    print()
    print(f"v{ver}  바뀐 파일 {len(changed)}개" + (f" (지난 배포 v{prev['version']} 대비)" if prev else " (첫 배포)"))
    for k in ("app", "core", "gpu"):
        if k in packs:
            state = "새로 만듦" if (k == "app" or rebuild.get(k)) else "그대로"
            print(f"  {k:5s} {mb(packs[k]['size']):>12s}  {state}")
    if patches:
        print(f"  app 에 끼워 넣은 core 파일 {len(patches)}개: " + ", ".join(sorted(patches)[:8])
              + (" …" if len(patches) > 8 else ""))
    print()
    if uploads:
        print(f"[1] GitHub 릴리스 {RELEASE_TAG} 에 아래 파일을 올리기 (같은 이름이 있으면 지우고 새로):")
        for zp in uploads:
            print(f"      {zp}  ({mb(zp.stat().st_size)})")
        print(f"    https://github.com/chatmdgus/kuruma-downloads/releases/edit/{RELEASE_TAG}")
        print("    릴리스 설명에 v" + ver + " 을 적어 두면 다운로드 페이지에 버전이 표시돼요.")
        print("[2] 다 올라간 뒤에 사이트 저장소 커밋·푸시")
    else:
        print("릴리스에 올릴 큰 파일 없음 → 사이트 저장소만 커밋·푸시하면 끝")
    print(f"    cd {site}")
    print(f"    git add files/{APP_ZIP} files/{MANIFEST_FILE}")
    print(f'    git commit -m "VRC Translator v{ver}"')
    print("    git push")


if __name__ == "__main__":
    main()

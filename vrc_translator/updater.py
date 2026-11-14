"""앱 안 업데이트.

배포 구조 (release.py가 만듦)
- 매니페스트(vrc_manifest.json): 버전 + 모든 파일의 sha256·크기·묶음 이름 + 묶음(zip) 주소
- 묶음 3개
  app  : exe(우리 코드가 들어 있음)·프리셋 등 자주 바뀌는 작은 파일 → 다운로드 사이트(GitHub Pages)
  core : 파이썬·라이브러리 (가끔만 바뀜, 큼)                     → GitHub 릴리스
  gpu  : CUDA DLL (NVIDIA GPU용, 선택)                          → GitHub 릴리스
- exe 폴더의 _internal/vrc_manifest.json = 지금 깔려 있는 상태

업데이트할 때는 내 매니페스트와 서버 매니페스트를 비교해서 바뀐 파일이 들어 있는 묶음만 받는다.
보통은 app 묶음(수 MB)만 받으면 끝.

적용: 실행 중인 exe·DLL도 '이름 바꾸기'는 되므로, 바꿀 파일을 *.vrcold 로 옮기고 새 파일을 넣은 뒤
다시 시작한다 (*.vrcold 는 다음 실행 때 지움). 이름 바꾸기가 막히면 PowerShell 도우미가
프로그램이 꺼진 뒤 파일을 옮기고 다시 켠다. 매니페스트는 맨 마지막에 바꾸므로 도중에 실패해도
다음 확인 때 빠진 파일을 다시 받는다.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from .version import VERSION

MANIFEST_NAME = "vrc_manifest.json"
OLD_SUFFIX = ".vrcold"
STAGE_DIR = "_update"
CHUNK = 1 << 20
UA = f"VRCTranslator/{VERSION}"


class UpdateError(Exception):
    """사용자에게 보여줄 수 있는 오류. code: perm | disk | mismatch | net | bad"""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- 경로·버전
def frozen():
    return bool(getattr(sys, "frozen", False))


def app_root():
    return Path(sys.executable).resolve().parent


def internal_dir():
    return Path(getattr(sys, "_MEIPASS", app_root() / "_internal"))


def manifest_path():
    return internal_dir() / MANIFEST_NAME


def vtuple(v):
    out = []
    for part in str(v).lstrip("v").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def fmt_size(n):
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.0f} MB" if n >= 10 << 20 else f"{n / (1 << 20):.1f} MB"
    return f"{max(1, n // 1024)} KB"


def nvidia_present():
    """NVIDIA 드라이버가 깔려 있는지 (GPU 가속 파일을 권할지)."""
    if sys.platform != "win32":
        return False
    sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return (sysroot / "System32" / "nvcuda.dll").exists()


def load_local():
    """지금 깔린 버전의 매니페스트. exe가 아니거나 매니페스트 없이 빌드됐으면 None (업데이트 끔)."""
    if not frozen():
        return None
    try:
        with open(manifest_path(), encoding="utf-8") as f:
            m = json.load(f)
        _validate(m)
        return m
    except Exception:
        return None


def _validate(m):
    if not isinstance(m, dict) or not isinstance(m.get("files"), dict) or not isinstance(m.get("packs"), dict):
        raise UpdateError("bad", "manifest format")
    for rel, ent in m["files"].items():
        if not (isinstance(ent, list) and len(ent) == 3) or ".." in rel.split("/") or rel.startswith("/"):
            raise UpdateError("bad", f"file entry {rel}")
        if ent[2] not in m["packs"]:
            raise UpdateError("bad", f"pack {ent[2]}")
    for name, pk in m["packs"].items():
        if not all(k in pk for k in ("url", "sha256", "size")):
            raise UpdateError("bad", f"pack {name}")


def installed_packs(local, root=None):
    """깔려 있는 묶음. GPU 파일을 직접 복사해 넣은 경우(크기가 다 맞으면)도 인정한다."""
    root = root or app_root()
    inst = set(local.get("installed") or ["app", "core"])
    if "gpu" not in inst:
        gfiles = [(rel, ent[1]) for rel, ent in local["files"].items() if ent[2] == "gpu"]
        if gfiles and all(_size(root / rel) == size for rel, size in gfiles):
            inst.add("gpu")
    return inst


def _size(p):
    try:
        return p.stat().st_size
    except OSError:
        return -1


def fetch_manifest(url, timeout=12):
    import requests
    sep = "&" if "?" in url else "?"
    try:
        r = requests.get(f"{url}{sep}t={int(time.time())}", timeout=timeout,
                         headers={"User-Agent": UA, "Cache-Control": "no-cache"})
        r.raise_for_status()
        m = r.json()
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError("net", f"{type(e).__name__}: {e}")
    _validate(m)
    return m


# ---------------------------------------------------------------- 계획
class Plan:
    def __init__(self, remote, files, removed, packs, want):
        self.remote = remote
        self.files = files          # 새로 받아 넣을 파일 (상대 경로)
        self.removed = removed      # 지울 파일
        self.packs = packs          # 받아야 하는 묶음 이름
        self.want = want            # 끝나면 깔려 있을 묶음

    @property
    def version(self):
        return self.remote.get("version", "?")

    @property
    def size(self):
        return sum(int(self.remote["packs"][p]["size"]) for p in self.packs)

    def __repr__(self):
        return f"Plan(v{self.version}, files={len(self.files)}, removed={len(self.removed)}, packs={self.packs})"


def make_plan(local, remote, want, root=None, before=None):
    """want 묶음을 remote 상태로 맞추려면 무엇을 받아야 하는지."""
    root = root or app_root()
    before = set(before if before is not None else installed_packs(local, root))
    lf, rf = local.get("files", {}), remote["files"]
    need = []
    for rel, (h, size, pack) in rf.items():
        if pack not in want or rel.endswith("/" + MANIFEST_NAME):
            continue
        old = lf.get(rel)
        if pack not in before or not old or old[0] != h or _size(root / rel) != size:
            need.append(rel)
    removed = [rel for rel in lf if rel not in rf and (root / rel).exists()]
    packs = sorted({rf[rel][2] for rel in need})
    return Plan(remote, sorted(need), sorted(removed), packs, set(want))


class CheckResult:
    def __init__(self, local, remote, update=None, gpu=None):
        self.local, self.remote = local, remote
        self.update = update        # 새 버전 (또는 빠진 파일) Plan
        self.gpu = gpu              # GPU 가속 파일 받기 Plan (아직 없을 때)


def check(local=None):
    """서버 매니페스트를 받아서 할 일을 알려준다. 업데이트를 못 쓰는 환경이면 None."""
    local = local or load_local()
    if not local or not local.get("update_url"):
        return None
    remote = fetch_manifest(local["update_url"])
    if remote.get("app") != local.get("app"):
        return None
    root = app_root()
    inst = installed_packs(local, root)
    want = {p for p in inst if p in remote["packs"]} | {"app", "core"}
    plan = make_plan(local, remote, want, root, inst)
    newer = vtuple(remote.get("version", "0")) > vtuple(local.get("version", "0"))
    update = plan if (newer or plan.files) else None
    gpu = None
    if "gpu" in remote["packs"] and "gpu" not in inst:
        gpu = make_plan(local, remote, want | {"gpu"}, root, inst)
    return CheckResult(local, remote, update, gpu)


# ---------------------------------------------------------------- 받기
def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url, part, size, add):
    """part 파일에 이어 받기. add(n): 진행 바이트."""
    import requests
    have = _size(part)
    if have < 0 or have > size:
        if have > size:
            part.unlink()
        have = 0
    if have:
        add(have)
    if have == size:
        return
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = f"bytes={have}-"
    with requests.get(url, stream=True, timeout=(15, 60), headers=headers) as r:
        if have and r.status_code == 206:
            mode = "ab"
        else:
            if r.status_code == 404:
                raise UpdateError("mismatch", f"404 {url}")
            r.raise_for_status()
            if have:
                add(-have)
            have, mode = 0, "wb"
        with open(part, mode) as f:
            for chunk in r.iter_content(CHUNK):
                if chunk:
                    f.write(chunk)
                    add(len(chunk))


def download(plan, progress=None, root=None, cancel=None):
    _busy.set()
    try:
        return _download_plan(plan, progress, root, cancel)
    finally:
        _busy.clear()


def _download_plan(plan, progress=None, root=None, cancel=None):
    """plan 의 묶음을 받아서 필요한 파일만 _update/new 에 풀어 둔다.
    progress(stage, done, total): stage = "dl" | "unpack" """
    root = root or app_root()
    stage = root / STAGE_DIR
    new_dir = stage / "new"
    try:
        stage.mkdir(exist_ok=True)
    except PermissionError as e:
        raise UpdateError("perm", str(e))
    if new_dir.exists():
        shutil.rmtree(new_dir, ignore_errors=True)

    need_bytes = plan.size + sum(plan.remote["files"][r][1] for r in plan.files)
    try:
        free = shutil.disk_usage(stage).free
    except OSError:
        free = need_bytes
    if free < need_bytes + (50 << 20):
        raise UpdateError("disk", fmt_size(need_bytes))

    total = plan.size
    state = {"done": 0, "last": 0.0}

    def add(n):
        state["done"] += n
        now = time.time()
        if progress and (now - state["last"] > 0.25 or state["done"] >= total):
            state["last"] = now
            progress("dl", state["done"], total)
        if cancel and cancel.is_set():
            raise UpdateError("cancel")

    by_pack = {}
    for rel in plan.files:
        by_pack.setdefault(plan.remote["files"][rel][2], []).append(rel)

    for name in plan.packs:
        pk = plan.remote["packs"][name]
        size, sha = int(pk["size"]), pk["sha256"]
        zpath = stage / f"{name}-{sha[:12]}.zip"
        if not (_size(zpath) == size and _sha256_file(zpath) == sha):
            part = zpath.with_suffix(".part")
            for attempt in range(4):
                before = state["done"]
                try:
                    _download(pk["url"], part, size, add)
                    break
                except UpdateError:
                    raise
                except Exception as e:
                    state["done"] = before              # 다음 시도에서 이어 받은 만큼 다시 더해짐
                    if attempt == 3:
                        raise UpdateError("net", f"{type(e).__name__}: {e}")
                    time.sleep(2 + attempt * 3)
            if _size(part) != size or _sha256_file(part) != sha:
                part.unlink(missing_ok=True)
                raise UpdateError("mismatch", name)
            os.replace(part, zpath)
        else:
            add(size)

        if progress:
            progress("unpack", 0, len(by_pack.get(name, [])))
        try:
            with zipfile.ZipFile(zpath) as z:
                names = set(z.namelist())
                for i, rel in enumerate(by_pack.get(name, [])):
                    if rel not in names:
                        raise UpdateError("mismatch", f"{rel} not in {name}")
                    want_h, want_size, _ = plan.remote["files"][rel]
                    dst = new_dir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    h = hashlib.sha256()
                    with z.open(rel) as src, open(dst, "wb") as out:
                        for chunk in iter(lambda: src.read(CHUNK), b""):
                            h.update(chunk)
                            out.write(chunk)
                    if h.hexdigest() != want_h or dst.stat().st_size != want_size:
                        raise UpdateError("mismatch", rel)
                    if progress and i % 20 == 0:
                        progress("unpack", i, len(by_pack[name]))
        except zipfile.BadZipFile as e:
            zpath.unlink(missing_ok=True)
            raise UpdateError("mismatch", f"{name}: {e}")
        zpath.unlink(missing_ok=True)
    return new_dir


# ---------------------------------------------------------------- 적용
def _old_name(dst):
    old = dst.with_name(dst.name + OLD_SUFFIX)
    if old.exists():
        try:
            old.unlink()
        except OSError:
            old = dst.with_name(f"{dst.name}.{int(time.time() * 1000)}{OLD_SUFFIX}")
    return old


def _new_local_manifest(plan):
    m = dict(plan.remote)
    m["installed"] = sorted(plan.want)
    return m


def apply(plan, root=None, restart_args=None):
    """받아 둔 파일로 교체. 성공하면 "swapped", PowerShell 도우미에게 맡겼으면 "helper".
    (helper 면 호출한 쪽이 바로 종료해야 한다. restart_args 가 None 이 아니면 도우미가 끝나고 다시 켬)"""
    root = root or app_root()
    stage = root / STAGE_DIR
    new_dir = stage / "new"
    mpath = manifest_path() if frozen() else root / "_internal" / MANIFEST_NAME
    tmp_manifest = stage / "manifest.new.json"
    with open(tmp_manifest, "w", encoding="utf-8") as f:
        json.dump(_new_local_manifest(plan), f, ensure_ascii=False)

    moved, placed = [], []
    try:
        for rel in plan.files + plan.removed:
            dst = root / rel
            if dst.exists():
                old = _old_name(dst)
                os.replace(dst, old)            # 실행 중인 exe·DLL도 이름 바꾸기는 된다
                moved.append((old, dst))
        for rel in plan.files:
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(new_dir / rel, dst)
            placed.append(dst)
        os.replace(tmp_manifest, mpath)          # 맨 마지막: 여기까지 와야 '업데이트됨'
    except OSError as e:
        print(f"[update] swap failed, rolling back: {type(e).__name__}: {e}")
        for dst in reversed(placed):            # 새 파일은 다시 대기 폴더로 (도우미가 쓴다)
            try:
                os.replace(dst, new_dir / dst.relative_to(root))
            except OSError as e2:
                print(f"[update] rollback {dst}: {e2}")
        for old, dst in reversed(moved):        # 원래 파일 되돌리기
            try:
                os.replace(old, dst)
            except OSError as e2:
                print(f"[update] rollback {dst}: {e2}")
        _start_helper(plan, root, new_dir, tmp_manifest, mpath, restart_args)
        return "helper"
    shutil.rmtree(new_dir, ignore_errors=True)
    return "swapped"


_HELPER_PS1 = r"""
$ErrorActionPreference = 'Continue'
$job = Get-Content -Raw -Encoding UTF8 -LiteralPath $args[0] | ConvertFrom-Json
try { Wait-Process -Id $job.pid -Timeout 60 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 700
function Retry($block) {
  for ($i = 0; $i -lt 30; $i++) { try { & $block; return $true } catch { Start-Sleep -Milliseconds 500 } }
  return $false
}
$ok = $true
foreach ($r in $job.remove) {
  $p = Join-Path $job.root $r
  if (Test-Path -LiteralPath $p) { if (-not (Retry { Remove-Item -LiteralPath $p -Force -ErrorAction Stop })) { $ok = $false } }
}
foreach ($r in $job.files) {
  $src = Join-Path $job.new $r
  $dst = Join-Path $job.root $r
  $dir = Split-Path -Parent $dst
  if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  if (-not (Retry { Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop })) { $ok = $false }
}
if ($ok) { Copy-Item -LiteralPath $job.manifest_new -Destination $job.manifest -Force }
if ($job.restart) {
  if ($job.args.Count -gt 0) { Start-Process -FilePath $job.exe -ArgumentList $job.args }
  else { Start-Process -FilePath $job.exe }
}
"""


def _start_helper(plan, root, new_dir, tmp_manifest, mpath, restart_args=None):
    stage = root / STAGE_DIR
    job = {
        "pid": os.getpid(), "root": str(root), "new": str(new_dir),
        "files": [r.replace("/", "\\") for r in plan.files],
        "remove": [r.replace("/", "\\") for r in plan.removed],
        "manifest_new": str(tmp_manifest), "manifest": str(mpath),
        "exe": sys.executable, "restart": restart_args is not None, "args": list(restart_args or []),
    }
    jpath = stage / "job.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)
    spath = stage / "apply.ps1"
    with open(spath, "w", encoding="utf-8-sig") as f:     # BOM: PowerShell 5 가 한글 경로를 읽게
        f.write(_HELPER_PS1)
    flags = 0x08000000 | 0x00000008 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW | DETACHED
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", str(spath), str(jpath)], close_fds=True, creationflags=flags)


def _restart_args():
    return [a for a in sys.argv[1:] if a != "--settings"]


_pending = {"plan": None}
_busy = threading.Event()        # 받는 중이면 정리(cleanup)가 대기 폴더를 건드리지 않게


def set_pending(plan):
    _pending["plan"] = plan


def apply_pending(restart_args=None):
    """다 받아 둔 업데이트가 있으면 지금 적용 (창을 닫을 때·설정으로 다시 시작할 때).
    도우미에게 맡겼으면 True (호출한 쪽은 다시 켜지 말고 바로 종료)."""
    plan = _pending["plan"]
    if plan is None or not frozen():
        return False
    _pending["plan"] = None
    try:
        how = apply(plan, restart_args=restart_args)
        mark_updated(VERSION, plan.version)
        print(f"[update] applied on exit ({how}) → v{plan.version}")
        return how == "helper"
    except Exception as e:
        print(f"[update] apply on exit failed: {type(e).__name__}: {e}")
        return False


def mark_updated(old_version, new_version):
    """다시 켜졌을 때 '업데이트했어요'를 한 번 보여주기 위한 표시."""
    try:
        from .settings import config_path
        p = config_path().parent / "updated.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"from": old_version, "to": new_version}), encoding="utf-8")
    except Exception as e:
        print(f"[update] mark: {e}")


def pop_updated():
    try:
        from .settings import config_path
        p = config_path().parent / "updated.json"
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        p.unlink()
        return data
    except Exception:
        return None


def restart():
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0   # DETACHED | NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable] + _restart_args(), close_fds=True, creationflags=flags)
    os._exit(0)


def cleanup_async():
    """지난 업데이트에서 남은 *.vrcold 와 대기 폴더 지우기.
    대기 폴더는 처음 한 번만(자동 받기 시작 전), *.vrcold 는 예전 프로세스가 늦게 꺼질 수 있어 몇 번 재시도."""
    if not frozen():
        return

    def clean_stage(root):
        stage = root / STAGE_DIR
        if not stage.exists() or _busy.is_set():
            return
        if (stage / "job.json").exists():
            shutil.rmtree(stage, ignore_errors=True)            # 도우미가 끝낸 뒤
            return
        for p in stage.iterdir():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif not p.name.endswith((".part", ".zip")):         # 받다 만 묶음은 이어 받기용으로 둠
                try:
                    p.unlink()
                except OSError:
                    pass

    def run():
        root = app_root()
        try:
            clean_stage(root)
        except Exception as e:
            print(f"[update] cleanup: {e}")
        for delay in (1, 4, 15, 60):
            time.sleep(delay)
            left = 0
            for p in list(root.rglob("*" + OLD_SUFFIX)):
                try:
                    p.unlink()
                except OSError:
                    left += 1
            if not left:
                return
    threading.Thread(target=run, daemon=True, name="update-cleanup").start()

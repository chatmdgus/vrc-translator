"""백그라운드 스레드 → 메인 창(tkinter)으로 보내는 이벤트 큐, 그리고 print/에러 출력 가로채기.

콘솔 없는 exe(windowed)에서는 sys.stdout/stderr가 None이라, 여기서 대체 스트림을 넣는다.
- stdout(print): 로그 파일 + 메인 창 시스템 메시지
- stderr(경고, 진행바 등): 로그 파일에만
"""
import io
import os
import queue
import sys
import threading
import time

Q = queue.Queue()

# 메인 창의 on/off 스위치. 파이프라인이 매 발화마다 확인한다.
CONTROLS = {"mic": True, "listen": True}

# VRChat OSC 수신 상태. listening=None(확인 안 함) / False(포트 사용 불가) / True, last=마지막 수신 시각
OSC = {"listening": None, "last": 0.0}


def emit(kind, **data):
    Q.put((kind, data))


def log(text):
    emit("log", text=text)


def status(text, state="busy"):
    """state: busy(준비 중) | running(실행 중) | error"""
    emit("status", text=text, state=state)


def error(text, brief=None):
    emit("error", text=text, brief=brief)


class _Stream(io.TextIOBase):
    def __init__(self, original, logfile, to_ui):
        self.original = original
        self.logfile = logfile
        self.to_ui = to_ui
        self.buf = ""
        self.lock = threading.Lock()
        self.line_start = True

    @property
    def encoding(self):
        return "utf-8"

    def writable(self):
        return True

    def isatty(self):
        return False

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        with self.lock:
            if self.original is not None:
                try:
                    self.original.write(s)
                except Exception:
                    pass
            if self.logfile is not None:
                try:
                    # 줄마다 앞에 시각을 붙인다 (끊긴 시점과 맞춰 보기 쉽게)
                    out = []
                    for part in s.splitlines(keepends=True):
                        if self.line_start:
                            out.append(time.strftime("[%H:%M:%S] "))
                        out.append(part)
                        self.line_start = part.endswith("\n")
                    self.logfile.write("".join(out))
                    self.logfile.flush()
                except Exception:
                    pass
            if self.to_ui:
                self.buf += s
                while "\n" in self.buf:
                    line, self.buf = self.buf.split("\n", 1)
                    line = line.split("\r")[-1].rstrip()
                    if line.strip():
                        log(line)
        return len(s)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass


def install(log_path):
    """print/경고 출력을 가로챈다. 로그 파일 경로를 돌려준다."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():                      # 직전 실행 로그는 log.prev.txt 로 보관
            try:
                os.replace(log_path, log_path.with_name("log.prev.txt"))
            except OSError:
                pass
        f = open(log_path, "w", encoding="utf-8")
    except OSError:
        f = None
    sys.stdout = _Stream(sys.stdout, f, to_ui=True)
    sys.stderr = _Stream(sys.stderr, f, to_ui=False)
    if f is not None:
        try:
            import faulthandler
            faulthandler.enable(file=f, all_threads=True)   # 파이썬 밖(드라이버 등)에서 죽어도 위치를 남김
        except Exception:
            pass
    return log_path

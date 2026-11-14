"""VRChat OSC: 챗박스 전송 + 마이크 뮤트 상태 수신."""
import queue
import threading
import time

from pythonosc.udp_client import SimpleUDPClient
from .i18n import t

CHATBOX_LIMIT = 144   # VRChat 챗박스 최대 글자 수
_BREAKS = "。．.!?！？、，, "


def split_chatbox(text, limit=CHATBOX_LIMIT):
    text = text.strip()
    out = []
    while len(text) > limit:
        cut = max(text.rfind(c, 0, limit) for c in _BREAKS)
        if cut < limit // 2:
            cut = limit - 1
        out.append(text[:cut + 1].strip())
        text = text[cut + 1:].strip()
    if text:
        out.append(text)
    return out


def compose(translated, original, show_original=True, limit=CHATBOX_LIMIT):
    """챗박스에 보낼 문장. 번역문이 우선이고, 원문은 남는 자리에 맞춰 줄인다."""
    if translated is None:
        return original
    if not show_original:
        return translated
    room = limit - len(translated) - 1
    if room < 8:                       # 원문을 붙일 자리가 거의 없으면 번역만
        return translated
    if len(original) > room:
        original = original[:room - 1] + "…"
    return f"{translated}\n{original}"


class Chatbox:
    def __init__(self, host, port, interval=1.6):
        self.client = SimpleUDPClient(host, port)
        self.interval = interval
        self.q = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, text):
        for chunk in split_chatbox(text):
            self.q.put(chunk)

    def typing(self, on):
        """챗박스 위 '입력 중(…)' 표시. 말하기 시작하면 켜서 상대가 곧 메시지가 온다는 걸 알게 한다."""
        try:
            self.client.send_message("/chatbox/typing", bool(on))
        except OSError:
            pass

    def _run(self):
        last = 0.0
        while True:
            chunk = self.q.get()
            wait = self.interval - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            # [텍스트, 즉시 전송(키보드 안 띄움), 알림음 끔]
            self.client.send_message("/chatbox/input", [chunk, True, False])
            last = time.time()


class MuteWatcher:
    """VRChat이 보내는 OSC를 받는다.
    - /avatar/parameters/MuteSelf → 뮤트 여부
    - 아무 메시지나 오면 → VRChat OSC가 켜져 있다는 뜻 (메인 창에 연결 상태 표시)"""

    def __init__(self, host, port):
        from . import events
        self.events = events
        self.muted = False
        try:
            from pythonosc.dispatcher import Dispatcher
            from pythonosc.osc_server import ThreadingOSCUDPServer
            d = Dispatcher()
            d.map("/avatar/parameters/MuteSelf", self._on_mute)
            d.set_default_handler(self._on_any)
            server = ThreadingOSCUDPServer((host, port), d)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            events.OSC["listening"] = True
            print(t("log.osc_wait", host=host, port=port))
        except OSError as e:
            events.OSC["listening"] = False
            print(t("log.osc_port", port=port, e=e))

    def _on_any(self, address, *args):
        self.events.OSC["last"] = time.time()

    def _on_mute(self, address, value):
        self.events.OSC["last"] = time.time()
        self.muted = bool(value)
        print(t("log.mute_on") if self.muted else t("log.mute_off"))

"""오디오 캡처(마이크 / WASAPI 루프백)와 발화 단위 분할."""
import queue
from collections import deque
from math import gcd

import numpy as np
from scipy.signal import resample_poly
from .i18n import t

TARGET_SR = 16000          # Whisper / webrtcvad 입력 샘플레이트
FRAME_MS = 30
VAD_FRAME_LEN = TARGET_SR * FRAME_MS // 1000   # 480


class AudioCapture:
    """PyAudioWPatch로 마이크(kind="mic") 또는 스피커 루프백(kind="loopback")을 캡처.
    read()는 장치 원래 샘플레이트의 mono float32를 돌려준다."""

    def __init__(self, kind, device_name=None):
        assert kind in ("mic", "loopback")
        self.kind = kind
        self.device_name = device_name
        self.q = queue.Queue(maxsize=1000)
        self.rate = None
        self.channels = None
        self._pa = None
        self._stream = None

    def _find_device(self):
        import pyaudiowpatch as pyaudio
        p = self._pa
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)

        if self.device_name:
            needle = self.device_name.lower()
            for i in range(p.get_device_count()):
                d = p.get_device_info_by_index(i)
                if d["hostApi"] != wasapi["index"] or needle not in d["name"].lower():
                    continue
                is_lb = d.get("isLoopbackDevice", False)
                if self.kind == "loopback" and is_lb:
                    return d
                if self.kind == "mic" and not is_lb and d["maxInputChannels"] > 0:
                    return d
            raise RuntimeError(t("log.no_device", kind=self.kind, name=self.device_name))

        if self.kind == "mic":
            return p.get_device_info_by_index(wasapi["defaultInputDevice"])

        spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if spk.get("isLoopbackDevice"):
            return spk
        for lb in p.get_loopback_device_info_generator():
            if spk["name"] in lb["name"]:
                return lb
        raise RuntimeError(t("log.no_loopback"))

    def start(self):
        import pyaudiowpatch as pyaudio
        self._pa = pyaudio.PyAudio()
        dev = self._find_device()
        self.rate = int(dev["defaultSampleRate"])
        self.channels = max(1, int(dev["maxInputChannels"]))
        print(f"[{self.kind}] {dev['name']} ({self.rate}Hz, {self.channels}ch)")

        def callback(in_data, frame_count, time_info, status):
            try:
                self.q.put_nowait(in_data)
            except queue.Full:
                pass  # 처리가 밀리면 버림 (실시간 우선)
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(
            format=pyaudio.paInt16, channels=self.channels, rate=self.rate,
            input=True, input_device_index=dev["index"],
            frames_per_buffer=int(self.rate * FRAME_MS / 1000),
            stream_callback=callback,
        )
        self._stream.start_stream()

    def read(self, timeout):
        try:
            raw = self.q.get(timeout=timeout)
        except queue.Empty:
            return None
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if self.channels > 1:
            pcm = pcm.reshape(-1, self.channels).mean(axis=1)
        return pcm


class Segmenter:
    """webrtcvad로 말하는 구간을 잘라 16kHz float32 발화 단위로 반환.
    VAD 판정은 대충 다운샘플한 신호로, 최종 오디오는 원본을 제대로 리샘플해서 만든다."""

    PREROLL = 10          # 300ms
    START_RATIO = 6       # 프리롤 10프레임 중 6개가 음성이면 시작

    def __init__(self, rate, cfg, on_start=None, on_discard=None):
        import webrtcvad
        self.vad = webrtcvad.Vad(int(cfg.get("aggressiveness", 2)))
        self.rate = rate
        self.flen = rate * FRAME_MS // 1000
        self.energy_floor = float(cfg.get("energy_floor", 0.005))
        self.min_speech = int(cfg.get("min_speech_ms", 300)) // FRAME_MS
        self.silence_limit = max(1, int(cfg.get("silence_ms", 600)) // FRAME_MS)
        self.max_frames = int(float(cfg.get("max_utterance_s", 12)) * 1000) // FRAME_MS
        self.on_start = on_start
        self.on_discard = on_discard

        g = gcd(TARGET_SR, rate)
        self._up, self._down = TARGET_SR // g, rate // g
        self._xp = np.arange(self.flen)
        self._x = np.linspace(0, self.flen - 1, VAD_FRAME_LEN)

        self.pending = np.zeros(0, np.float32)
        self.preroll = deque(maxlen=self.PREROLL)
        self._reset()

    def _reset(self):
        self.active = False
        self.frames = []
        self.speech_n = 0
        self.silence_n = 0

    def _is_speech(self, f):
        if np.sqrt(np.mean(f * f)) < self.energy_floor:
            return False
        x = np.interp(self._x, self._xp, f)
        pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()
        return self.vad.is_speech(pcm, TARGET_SR)

    def feed(self, mono):
        """mono: 장치 샘플레이트 float32. 완성된 발화 리스트를 반환."""
        out = []
        self.pending = np.concatenate([self.pending, mono])
        while len(self.pending) >= self.flen:
            f = self.pending[:self.flen]
            self.pending = self.pending[self.flen:]
            u = self._frame(f)
            if u is not None:
                out.append(u)
        return out

    def _frame(self, f):
        sp = self._is_speech(f)
        if not self.active:
            self.preroll.append((f, sp))
            if sum(s for _, s in self.preroll) >= self.START_RATIO:
                self.active = True
                self.frames = [x for x, _ in self.preroll]
                self.speech_n = sum(s for _, s in self.preroll)
                self.silence_n = 0
                self.preroll.clear()
                if self.on_start:
                    self.on_start()
            return None

        self.frames.append(f)
        if sp:
            self.speech_n += 1
            self.silence_n = 0
        else:
            self.silence_n += 1
        if self.silence_n >= self.silence_limit or len(self.frames) >= self.max_frames:
            return self._finish()
        return None

    def flush(self):
        """입력이 끊겼을 때(루프백은 무음이면 콜백이 안 옴) 진행 중 발화를 마무리."""
        return self._finish() if self.active else None

    def _finish(self):
        frames, n = self.frames, self.speech_n
        self._reset()
        if n < self.min_speech:
            if self.on_discard:
                self.on_discard()
            return None
        audio = np.concatenate(frames)
        return resample_poly(audio, self._up, self._down).astype(np.float32)

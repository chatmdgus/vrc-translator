"""WASAPI 장치 목록. 직접 실행하면 콘솔에 출력: python -m vrc_translator.devices"""


def list_devices():
    """(마이크 이름 목록, 루프백 이름 목록)"""
    import pyaudiowpatch as pyaudio
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        mics = []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if (d["hostApi"] == wasapi["index"] and d["maxInputChannels"] > 0
                    and not d.get("isLoopbackDevice")):
                mics.append(d["name"])
        loops = [d["name"] for d in p.get_loopback_device_info_generator()]
        return mics, loops
    finally:
        p.terminate()


if __name__ == "__main__":
    m, l = list_devices()
    print("== 마이크 ==", *m, "== 루프백 (출력장치) ==", *l, sep="\n  ")

# $PyInstaller-Hook-Priority: 100
# pyinstaller-hooks-contrib 의 webrtcvad 훅은 배포판 이름을 'webrtcvad'로 찾지만,
# 우리가 쓰는 건 'webrtcvad-wheels' 라서 이 훅으로 덮어쓴다.
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata("webrtcvad-wheels")

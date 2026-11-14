# VRC Translator — 작업 규칙

- 주인: チャ (한국어로 대화, 짧게·음슴체). 명령어는 들여쓰기 없는 코드 블록 하나로.
- Windows용 Python 3.12 앱 → PyInstaller onedir, 창 모드 exe (`VRCTranslator-Friends`).
- 배포: main 에 푸시 + `vrc_translator/version.py` 버전 올림 → Actions `release.yml` 이 빌드·업로드
  (다운로드 사이트 https://chatmdgus.github.io/kuruma-downloads/#translator, 저장소 chatmdgus/kuruma-downloads).
  버전을 안 올리면 배포 안 됨. 앱은 매니페스트를 보고 바뀐 묶음만 받아 스스로 업데이트 (`updater.py`).
- 서버(번역 릴레이 `relay/server.js`)는 Oracle `ubuntu@193.123.163.59` 의 `~/vrc-relay` (pm2 `vrc-relay`). 이 저장소에서 자동 배포 안 됨.

## 꼭 지킬 것 (실제로 터졌던 것)
- tkinter 객체를 다른 스레드 GC가 해제하면 Tcl이 프로세스를 강제 종료 → `gc.disable()`, `gc.collect()`는 메인 스레드에서만.
- 창을 닫으면 `os._exit(0)` (안 하면 백그라운드에 남아 폴더 '사용 중').
- 콘솔 없는 exe라 `sys.stdout` 이 None → `events.install` 로 대체. 오류는 MessageBox로 반드시 표시.
- `build.bat` 은 CRLF + ASCII만.
- 로컬 변수 이름 `t` 금지 (i18n 함수 `t()` 와 충돌).
- UI 문구는 `i18n.py` 에 한국어·일본어 둘 다.
- 샌드박스에서는 Google/DeepL 실서버 테스트 불가 → 목(mock)과 Xvfb 스크린샷으로 검증.
- 토큰·키를 코드나 커밋에 넣지 말 것 (`preset.yaml` 은 Secrets `PRESET_YAML`).

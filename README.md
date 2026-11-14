# VRC Translator

한국인 유저용 VRChat 한↔일 실시간 번역기.

- **① 내 말 → 챗박스**: 한국어로 말하면 일본어로 번역해서 VRChat 챗박스에 표시 (일본어로 말하면 그대로 전송)
- **② 상대 말 → 자막**: 일본어 음성을 한국어 자막으로 표시 (한국어는 그대로 표시)
- 음성인식은 기본적으로 한국어/일본어만 대상으로 해서 다른 언어로 잘못 인식되는 걸 막습니다
- 설정 창 맨 위 「내 언어」(한국어 / 일본어): 내가 말하는 언어이자 자막을 볼 언어
- ①「챗박스 언어」(내 언어를 뺀 한국어 / 일본어 / 영어): 챗박스에 번역문 + 아래 줄에 원문
- ②「듣기 언어」(한국어 / 일본어 / 영어 중 체크): 체크한 언어를 내 언어 자막으로 번역

## 사용법 (exe)

1. `VRCTranslator.exe` 실행
2. 설정 창에서 번역 방식 선택
   - **번역 서버 (Oracle 릴레이)**: 서버 주소 + 접속 토큰 입력 (서버 구축은 `relay/README.md`)
   - **DeepL 키 직접 입력**: 본인 DeepL API 키 (무료, 월 50만 자)
3. VRChat에서 **Action Menu → Options → OSC → Enabled** 켜기
4. 「시작」 → 첫 실행 시 음성인식 모델 자동 다운로드 (GPU 약 1.6GB / CPU 약 0.5GB)

- NVIDIA GPU가 있으면 자동으로 GPU를 사용하고, 없거나 실패하면 CPU로 전환됩니다 (NVIDIA 드라이버만 있으면 됨).
- 챗박스: 일본어 번역문 + 아래 줄에 한국어 원문 (설정 창에서 끌 수 있음)
- 메인 창: 번역 기록이 채팅처럼 표시됨 (오른쪽 파란 글씨 = 챗박스로 보낸 내 말, 왼쪽 = 주변 말).
  이번 실행에서 쓴 글자 수와 시간당 사용량도 표시. ①/② 스위치로 잠시 끌 수 있음
  「맨 앞 고정」으로 항상 위에 띄우기, 카드 우클릭으로 번역문 복사. 창 크기·위치·고정 상태는 자동 저장
- 자막 창: 드래그로 이동, 우클릭으로 숨기기 (메인 창의 「자막 창 보이기」로 다시 표시)
- 메인 창을 닫으면 프로그램이 종료됩니다
- 문제가 생기면 메인 창의 「로그 파일 열기」 내용을 보내주세요
- 설정 저장 위치: `%APPDATA%\VRCTranslator\config.yaml` (「고급 설정 파일 열기」)

## 실행 흐름

- 처음 실행: 「한국어로 말해요 / 日本語で話します」 선택 → 간단한 설정 → 「시작」
- 「다음부터 이 설정으로 바로 시작」이 켜져 있으면, 다음부터는 설정 창 없이 바로 번역이 시작됩니다
- 설정을 바꾸고 싶으면 메인 창 오른쪽 위 「설정」 버튼
- 번역 엔진: 「DeepL · 자연스러움」 또는 「Google · 빠름」(무료·비공식, 안 되면 DeepL로 자동 전환)
- 내 말은 「내 언어」로 고정해서 인식합니다 (더 빠르고 정확)

## 고급 설정에서 조정할 만한 것

- **지연이 길다** → 설정 창 「반응 속도」를 「빠르게」로 (대신 문장이 잘게 끊김), 「번역 품질」을 「빠르게」로
- **PC 사양과 상관없이 빠르게** → 「인식 방식: 클라우드 우선」(기본). Google 무료 음성인식을 쓰고, 안 되면 자동으로 PC 음성인식으로 전환.
  클라우드 방식은 음성이 Google 서버로 전송됩니다. Google 무료 방식은 비공식이라 막힐 수 있지만 그때는 자동으로 PC 음성인식을 써요
- **다른 PC에서 느리다** → 메인 창 위쪽 「실행 중 · GPU/CPU · 모델」 확인. 「음성인식 모델: 자동」이면 PC가 느릴 때 알아서 가벼운 모델로 바꿈. 그래도 느리면 「가볍게」
- **번역이 어색하다** → 「번역 품질: 고품질」(기본). DeepL 차세대 모델 + 앞 대화 맥락(글자 수에 안 들어감) + 구어체 번역 지시를 사용
- **이름·신조어·자주 틀리는 말** → 설정 창 「단어장」에 적기 (인식 힌트 + 「틀린 말 → 맞는 말」 고쳐 쓰기)
- **자막이 크다/작다** → 설정 창 「자막 크기」. 자막 창 위치는 드래그하면 자동 저장
- **BGM에 반응한다** → `listen.vad.aggressiveness: 3`, `vad.energy_floor` 올리기
- **작은 목소리를 놓친다** → `energy_floor` 낮추기
- **짧은 말이 한/일 잘못 판정된다** → `mic.source_lang: ko` 로 고정 (일본어로는 말하지 않는 경우)
- **다른 언어도 쓰고 싶다** → `whisper.allowed_langs`에 추가 (예: `[ko, ja, en]`)
- **자막 크기/위치** → `overlay.font_size`, `overlay.vr_offset`

## 한계

- 루프백은 월드 소리 전체를 듣기 때문에 누가 말했는지 구분하지 못함
- 스피커 사용 시 마이크가 상대 목소리를 주울 수 있음 → 헤드셋 권장
- 챗박스는 144자 제한, 연속 전송은 약 1.6초 간격
- DeepL 무료 한도를 넘으면 그 달은 번역이 멈춤

---

## 개발자용

```
pip install -r requirements.txt
python app.py            # 설정 창 포함
python app.py --no-gui   # 저장된 설정으로 바로 실행
```

### exe 빌드 (Windows)

```
build.bat        # GPU 버전 (CUDA 12 / cuDNN 9 동봉, 수 GB)
build.bat cpu    # CPU 전용 (훨씬 작음)
build.bat --console   # 디버그용: 콘솔 창도 같이 띄움
```

결과물: `dist\VRCTranslator\` 폴더 (`--zip` 을 붙이면 폴더 전체 zip 도 만듦)

### 지인 배포용 빌드 (키/토큰 입력 없이 바로 사용)

1. `relay/README.md` 대로 Oracle 서버에 릴레이를 올려둔다
2. `preset.example.yaml` → `preset.yaml` 로 복사하고 `relay_token` 입력
3. `build.bat --preset preset.yaml`
4. `release.py` 로 다운로드 사이트에 올리기 (아래 「배포와 앱 안 업데이트」)

- GPU 버전으로 빌드해도 NVIDIA GPU가 없는 PC에서는 자동으로 CPU로 동작합니다
- DeepL 키는 서버에만 있고 exe에는 들어가지 않습니다 (preset.yaml에 키가 있으면 빌드가 거부됨)
- 토큰은 exe 폴더에서 평문으로 보이지 않게 변환되어 들어가지만, 암호화는 아니라서 마음먹으면 꺼낼 수 있습니다.
  → 공개된 곳(Booth 등)에 올리지 말고, 서버의 토큰별 일일 한도로 피해를 제한하세요
- 토큰이 새면 서버 `.env`에서 토큰을 바꾸고 다시 빌드해서 재배포하면 됩니다
(onedir 방식이라 폴더째 배포해야 합니다. exe만 따로 떼면 실행되지 않음)

### 배포와 앱 안 업데이트

다운로드 사이트: https://chatmdgus.github.io/kuruma-downloads/#translator

- 친구는 처음 한 번만 `VRCTranslator-Friends.zip`(GPU 파일 제외)을 받는다
- 그다음부터는 메인 창 위에 「새 버전」 줄이 뜨고, 「업데이트」를 누르면 바뀐 파일이 든 묶음만 받아서 교체 후 다시 시작
- NVIDIA GPU가 있으면 「GPU 가속 파일 받기」 줄이 뜬다 (약 1GB, 원하는 사람만). 없으면 PC 음성인식은 CPU로 동작
- 예전 빌드의 `_internal\nvidia` 폴더를 그대로 복사해 넣어도 GPU 파일로 인정됨 (버전이 같을 때)

묶음 (`release.py`가 만듦)

| 묶음 | 내용 | 위치 |
|---|---|---|
| `translator-latest.json` | 버전, 모든 파일 sha256, 묶음 주소 | 사이트 저장소 `files/` (git) |
| `translator-app.zip` | exe(우리 코드)·프리셋 등 자주 바뀌는 작은 파일 | 사이트 저장소 `files/` (git) |
| `translator-core.zip` | 파이썬·라이브러리 | 릴리스 `translator-latest` |
| `translator-gpu.zip` | CUDA DLL | 릴리스 `translator-latest` |
| `VRCTranslator-Friends.zip` | 처음 받는 사람용 (app + core) | 릴리스 `translator-latest` |

새 버전 올리기 (자동)

- `vrc_translator/version.py` 의 `VERSION` 을 올려서 main 에 푸시하면 GitHub Actions(`release.yml`)가
  Windows에서 빌드 → `release.py` → 릴리스 업로드 → 사이트 저장소 푸시까지 한다 (15~25분)
- 버전을 안 올린 푸시는 확인만 하고 끝남. 같은 버전으로 다시 올리려면 Actions 탭에서 `release` → Run workflow → force
- 저장소 Secrets: `PRESET_YAML`(preset.yaml 내용), `DOWNLOADS_TOKEN`(kuruma-downloads Contents 쓰기 토큰)

손으로 올릴 때 (Actions가 막혔을 때)

1. `build.bat --preset preset.yaml`
2. `python release.py --site C:\dev\kuruma-downloads` (빌드 venv의 python)
3. 큰 파일이 새로 생겼으면 안내대로 릴리스에 올리기 → 그다음 사이트 저장소 커밋·푸시 (순서 중요)

- 작은 라이브러리 파일 몇 개만 바뀌면 core를 다시 올리지 않고 app 묶음에 끼워 넣는다
- 앱은 실행 중인 exe·DLL의 이름을 `*.vrcold`로 바꾸고 새 파일을 넣은 뒤 다시 시작한다 (다음 실행 때 지움).
  이름 바꾸기가 막히면 PowerShell 도우미가 프로그램이 꺼진 뒤 옮긴다
- 받는 중 끊기면 다음에 이어 받는다 (`_update` 폴더)


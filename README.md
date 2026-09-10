# Lecorder

Lecorder는 강의 녹음을 브라우저에서 안전하게 받고, 로컬
[whisper.cpp](https://github.com/ggml-org/whisper.cpp)로 전사해 원본 미디어와 Markdown
노트로 저장하는 macOS용 도구입니다.

선택적으로 로컬 Ollama/Qwen3가 발음이 비슷한 단어 오인식과 의미상 문단 경계를
제안합니다. Qwen3는 전사문 전체를 다시 쓰지 않으며, 단어 수정은 사용자가 해당 시간의
원음을 확인하고 승인해야 반영됩니다.

대시보드, Whisper, Ollama는 기본적으로 `127.0.0.1`에서만 통신합니다. 녹음과 결과물,
강의 설정, 작업 이력은 사용자의 Mac에 저장됩니다.

## 주요 기능

- 브라우저 녹음을 5초 조각으로 저장하고 중단된 녹음 복구
- 기존 오디오·동영상 파일 업로드와 단일 작업 대기열 처리
- whisper.cpp `large-v3`와 Silero VAD 기반 로컬 음성 인식
- VAD 음성 누락 감지 후 전체 오디오 자동 재전사
- 고신뢰 동일 발화 반복 붕괴 감지와 문맥 유지 안전 재전사
- 저신뢰 구간만 제한적으로 다시 읽고 더 나은 결과만 채택
- 강의별 언어, 인식 힌트, 확정 교정과 처리 옵션 관리
- 선택 사항인 Ollama/Qwen3 단어 후보·문단 경계 검수
- 원음 확인 후 승인한 후보만 현재 노트와 강의 규칙에 반영
- YAML 메타데이터와 원음 타임스탬프 링크가 포함된 Markdown 생성
- 출력 폴더 탐색, 음성 재생, Markdown 미리보기와 Finder 열기
- 작업 중단, 실패 작업 재시도, 단계별 처리 시간과 품질 지표 확인

## 작동 구성

| 구성요소 | 역할 | 기본 주소 또는 위치 |
| --- | --- | --- |
| 브라우저 대시보드 | 강의 설정, 녹음·업로드, 결과 및 제안 검토 | `http://127.0.0.1:5055` |
| Flask 백엔드 | API, SQLite, 녹음 수신, 단일 작업 대기열, 파일 저장 | Lecorder 프로세스 내부 |
| whisper.cpp | `large-v3` 전사와 Silero VAD | `http://127.0.0.1:8080` |
| Ollama / Qwen3 | 선택적인 단어 후보와 문단 경계 검수 | `http://127.0.0.1:11434` |
| 출력 폴더 | 원본 미디어와 같은 이름의 Markdown | `LECORDER_OUTPUT_DIR` |

처리 흐름은 다음과 같습니다.

```text
브라우저 녹음 또는 파일 업로드
  → 작업 원본 보관 및 SQLite 등록
  → FFmpeg 16kHz 모노 WAV 변환
  → Whisper + VAD 1차 전사
  → VAD 누락 보호 및 저신뢰 구간 재확인
  → 강의별 확정 교정 적용
  → 선택적인 Qwen3 단어 후보·문단 검수
  → 원본 미디어 + 타임스탬프 Markdown 저장
  → 사용자의 수정 후보 승인 또는 거절
```

## 요구 사항

- macOS — 현재 `start.command`와 Finder 연동은 macOS 기준
- Homebrew
- Python 3.10 이상
- Git, CMake, FFmpeg
- whisper.cpp의 `whisper-server`
- `ggml-large-v3.bin`과 `ggml-silero-v6.2.0.bin`
- 선택 사항: Ollama와 `qwen3:8b-q4_K_M`

Apple Silicon에서는 whisper.cpp의 기본 CMake 빌드가 Metal 가속을 사용합니다.

## 설치

### 1. 기본 도구 설치

```bash
brew update
brew install git cmake ffmpeg python
```

Qwen3 검수도 사용할 경우 Ollama를 함께 설치합니다.

```bash
brew install ollama
```

### 2. Lecorder Python 환경 준비

Lecorder 프로젝트 폴더에서 실행합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
mkdir -p output
chmod +x start.command
```

`start.command`는 프로젝트의 `.venv/bin/python`을 우선 사용하고, 없으면 시스템의
`python3`를 찾습니다.

### 3. whisper.cpp 빌드

기본 `.env`는 Lecorder와 whisper.cpp가 같은 상위 폴더에 있는 구조를 사용합니다.

```text
작업 폴더/
├── lecorder/
└── whisper.cpp/
```

프로젝트 폴더에서 다음 명령을 실행합니다.

```bash
git clone https://github.com/ggml-org/whisper.cpp.git ../whisper.cpp
(
  cd ../whisper.cpp
  cmake -B build
  cmake --build build -j --config Release
)
```

이미 whisper.cpp가 있다면 다시 복제하지 말고 해당 폴더에서 `git pull` 후 CMake 빌드만
다시 실행합니다.

### 4. Whisper와 VAD 모델 설치

```bash
(
  cd ../whisper.cpp
  sh ./models/download-ggml-model.sh large-v3
  ./models/download-vad-model.sh silero-v6.2.0

  test -x ./build/bin/whisper-server && echo "whisper-server OK"
  test -f ./models/ggml-large-v3.bin && echo "large-v3 OK"
  test -f ./models/ggml-silero-v6.2.0.bin && echo "Silero VAD OK"
)
```

Lecorder는 기본적으로 다음 파일을 찾습니다.

```text
../whisper.cpp/build/bin/whisper-server
../whisper.cpp/models/ggml-large-v3.bin
../whisper.cpp/models/ggml-silero-v6.2.0.bin
```

### 5. 선택: Ollama와 Qwen3 준비

Ollama를 지금 시작하고 macOS 로그인 후에도 자동 실행되는 Homebrew 서비스로 등록한 뒤
기본 검수 모델을 내려받습니다.

```bash
brew services start ollama
ollama pull qwen3:8b-q4_K_M

brew services list | grep ollama
ollama list
```

`brew services list`에서 Ollama가 `started`이고, `ollama list`에
`qwen3:8b-q4_K_M`이 보이면 준비된 상태입니다. Ollama를 사용하지 않을 경우 이 단계는
생략하고 대시보드에서 해당 강의의 **Qwen3 문맥 검수**를 끕니다.

### 6. 실행

```bash
./start.command
```

Finder에서 `start.command`를 더블클릭해도 됩니다. macOS가 처음 실행을 차단하면 Finder에서
파일을 Control-클릭하고 **열기**를 선택합니다.

실행 스크립트는 다음 작업을 수행합니다.

1. Python과 필수 패키지, FFmpeg, whisper-server와 모델을 확인합니다.
2. 8080 포트에 호환되는 Whisper가 이미 있으면 재사용합니다.
3. 그렇지 않으면 large-v3와 Silero VAD를 사용해 로컬 whisper-server를 시작합니다.
4. Flask 대시보드와 단일 작업 대기열을 시작합니다.
5. `http://127.0.0.1:5055`를 기본 브라우저에서 엽니다.

종료하려면 실행 터미널에서 `Ctrl+C`를 누릅니다. 스크립트가 시작한 Lecorder와 Whisper
프로세스가 함께 종료됩니다.

## 사용 방법

### 1. 강의 설정

**강의** 화면에서 강의를 만들거나 선택한 뒤 다음 항목을 설정합니다.

- **언어:** 한국어, English, 자동 감지
- **인식 힌트:** 과목명, 인명, 전문용어와 자주 등장하는 표현
- **확정 교정:** 한 줄에 `잘못된 표현=올바른 표현` 또는 `잘못된 표현=>올바른 표현`
- **가독성 자동 정리:** Qwen3가 판단한 의미 경계로 문단 구성
- **Qwen3 문맥 검수:** 발음이 가까운 단어 오인식 후보 생성

설정은 입력하는 동안 SQLite에 자동 저장됩니다. 녹음이나 업로드를 시작하면 당시 설정이
작업 기록에 복사되므로, 처리 중 강의 설정을 바꿔도 이미 등록된 작업은 영향을 받지 않습니다.

### 2. 녹음 또는 파일 업로드

**녹음** 화면에서 저장할 이름과 강의를 확인한 뒤 마이크 녹음을 시작하거나 기존 파일을
업로드합니다. 일반적인 WAV, MP3, M4A, FLAC, OGG, WebM과 MP4, MOV 등의 미디어 형식을
지원합니다.

브라우저 녹음은 5초마다 조각을 만듭니다. 각 조각은 서버 전송 전에 IndexedDB에 보관되며,
서버가 잠시 끊기면 다시 전송됩니다. 녹음 중 탭을 닫으려 하면 경고가 표시됩니다.

### 3. 대기열과 결과 확인

작업은 한 번에 하나씩 등록 순서대로 처리됩니다. 앞 작업이 전사 중이어도 다음 녹음이나
업로드를 대기열에 추가할 수 있습니다.

- **저장소:** 실제 출력 폴더를 실시간으로 읽어 같은 이름의 미디어와 Markdown을 묶어 표시
- **최근 작업:** 대기·처리·완료·실패 상태, 등록·시작·완료 시각과 단계별 처리 지표 표시
- **수정 검토:** Qwen3 후보의 해당 시간 원음을 듣고 승인 또는 거절

같은 이름의 파일이 이미 있으면 `(2)`, `(3)`을 붙여 기존 결과물을 덮어쓰지 않습니다.

## 전사 파이프라인

1. **원본 접수:** 업로드 파일을 작업 폴더에 복사하거나 브라우저 녹음 조각을 순서대로 결합합니다.
2. **대기열 등록:** 작업과 당시의 강의 설정을 SQLite에 기록하고 단일 소비자 스레드가 처리합니다.
3. **오디오 변환:** FFmpeg로 `16kHz`, 모노, PCM s16le WAV를 만듭니다.
4. **Whisper 1차 전사:** large-v3, 선택 언어, 최대 3,000자의 인식 힌트와 Silero VAD를 사용하며 장문 문맥을 유지합니다.
5. **품질 보호:** 60초 이상 파일에서 전사 범위가 지나치게 짧을 때만 VAD를 끄고 다시 읽습니다. 반복 붕괴 재확인은 장문 문맥과 VAD를 유지해 불필요한 장시간 처리를 피합니다.
6. **저신뢰 재확인:** 기본 평균 로그확률 `-0.65` 미만 구간을 최대 4묶음·90초 범위에서 다시 읽고, 개선된 결과만 채택합니다.
7. **확정 교정:** 강의별 문자열 교정 규칙을 발화 단위로 적용합니다.
8. **선택 검수:** Qwen3에 단어 오인식 후보와, 활성화된 경우 문단 경계를 한 번의 통합 JSON 요청으로 받습니다.
9. **Markdown 생성:** YAML 메타데이터, 원본 링크와 문단별 시작 시각 링크를 만듭니다.
10. **안전한 저장:** 숨김 임시파일을 완성한 뒤 최종 이름으로 교체하고 성공한 작업의 임시 폴더를 삭제합니다.

Qwen3나 일부 검수 요청이 실패해도 Whisper 전사와 저장은 계속됩니다. 이 경우 완료된 검수
결과만 사용하고 최근 작업에 경고를 표시합니다.

Whisper 전사 자체가 실패해도 작업용 임시 폴더에만 의존하지 않도록 원본 음성을 즉시 저장
경로에 복사합니다. 재처리를 위해 작업용 원본도 함께 유지합니다.

## Qwen3 검수의 범위

Qwen3는 전사문을 자연스럽게 고치는 일반 교정기가 아닙니다. 다음 조건을 통과한 후보만
사용자에게 보여 줍니다.

- 발음이 가까운 ASR 오인식 또는 전문용어 오인식
- 원문과 대체문이 각각 1~3어절, 24자 이하
- 모델 신뢰도 0.80 이상
- 원래 표현이 해당 발화에 실제로 존재
- 숫자를 바꾸지 않고 최소 음성 유사도 기준을 통과

번역, 맞춤법·띄어쓰기, 조사·어미, 문체, 요약, 사실 교정과 의미가 다른 표현으로의 교체는
차단합니다. 문단 경계는 주제 전환이나 예시 종료처럼 의미 변화가 분명할 때만 사용합니다.

후보를 승인하면 현재 Markdown을 갱신하고 같은 교정을 해당 강의의 확정 교정 규칙에
추가합니다. 거절한 후보는 상태만 기록하고 원문을 변경하지 않습니다.

## 결과 파일

출력 폴더에는 원본 확장자를 유지한 미디어와 같은 이름의 Markdown이 저장됩니다.

```text
output/
├── 확률론 03.webm
└── 확률론 03.md
```

Markdown에는 다음 정보가 포함됩니다.

- 강의명, 녹음 시각, 길이와 언어
- `whisper.cpp/large-v3`와 선택한 검수 모델
- 원본 미디어 파일명
- 전체 녹음 링크
- 문단별 시작 시각과 해당 시점의 원음 링크

## 설정

`.env.example`을 `.env`로 복사해 사용합니다. 주요 경로 세 개는 대시보드 오른쪽 위의
시스템 경로 설정에서도 변경할 수 있습니다.

| 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `LECORDER_DIR` | `.` | `app.py`와 `web/backend`가 있는 프로젝트 경로 |
| `WHISPER_CPP_DIR` | `../whisper.cpp` | whisper.cpp 빌드와 모델이 있는 경로 |
| `LECORDER_OUTPUT_DIR` | `output` | 원본 미디어와 Markdown 결과 저장 경로 |
| `OLLAMA_MODEL` | `qwen3:8b-q4_K_M` | 선택 검수에 사용할 Ollama 모델 |

고급 설정은 `.env`에서 직접 변경할 수 있습니다.

| 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `LECORDER_PORT` | `5055` | 대시보드 포트 |
| `WHISPER_PORT` | `8080` | whisper-server 포트 |
| `WHISPER_URL` | `http://127.0.0.1:8080/inference` | Whisper API 주소 |
| `OLLAMA_URL` | `http://127.0.0.1:11434/api/chat` | Ollama API 주소 |
| `WHISPER_LOW_CONFIDENCE` | `-0.65` | 재확인할 발화의 평균 로그확률 기준 |
| `WHISPER_RETRY_MAX_GROUPS` | `4` | 작업당 재확인할 최대 구간 묶음 |
| `WHISPER_RETRY_MAX_SECONDS` | `90` | 새 재확인 묶음을 시작하는 시간 한도 |

대시보드에서 경로를 저장하면 `.env`가 갱신됩니다. `start.command`로 실행 중이면 앱과 이
스크립트가 관리하는 Whisper가 자동으로 다시 시작됩니다. 출력 경로 변경은 이후 완료되는
작업부터 적용되며 기존 파일과 작업 이력은 이동하지 않습니다.

## 복구와 중단

- 앱 시작 시 SQLite에 남은 대기 작업은 다시 대기열에 넣습니다.
- 녹음 중 앱이 종료되면 해당 작업을 복구 가능 상태로 바꾸고 브라우저 조각으로 복구합니다.
- 처리 중 앱이 종료되면 해당 작업을 실패 상태로 바꾸고 보존한 원본으로 재시도할 수 있습니다.
- 일부 녹음 조각이 누락되면 처음부터 연속으로 남은 조각만 결합하는 수동 복구를 제공합니다.
- 대기 또는 처리 중인 작업을 중단하면 전사 결과를 삭제하고 `-중단됨`이 붙은 원본만 저장합니다.
- 한 작업의 실패는 대기열 작업 스레드 전체를 종료하지 않습니다.

## 로컬 데이터와 개인정보

다음 데이터는 저장소에 커밋되지 않도록 `.gitignore`에서 제외됩니다.

- `.env`, Python 가상환경과 캐시
- `web/data/`의 SQLite DB와 복구용 원본·녹음 조각
- `output/`, `recordings/`, 오디오·동영상 파일

저장 위치는 다음과 같습니다.

| 데이터 | 위치 |
| --- | --- |
| 강의 설정, 작업 이력, 수정 제안 | `web/data/lecorder.db` |
| 처리 전·실패 작업의 임시 원본 | `web/data/recordings/` |
| 완성된 미디어와 Markdown | `LECORDER_OUTPUT_DIR` |
| 전송 전 브라우저 녹음 조각 | 브라우저 IndexedDB |

## 프로젝트 구조

```text
lecorder/
├── app.py                    Flask 앱 진입점
├── start.command             Whisper와 대시보드 실행·종료
├── .env.example              경로·모델 설정 예시
├── requirements.txt          Python 의존성
├── web/
│   ├── static/
│   │   ├── dashboard.*       대시보드 HTML·CSS·JavaScript
│   │   ├── recording-store.js 브라우저 녹음 조각 복구 저장소
│   │   └── guide.*           상세 사용 안내 HTML·CSS·JavaScript
│   ├── data/                 SQLite와 작업 복구 데이터
│   └── backend/
│       ├── config.py         환경변수, 경로와 포트
│       ├── routes.py         화면과 API
│       ├── pipeline.py       녹음 수신, 대기열, 복구와 저장
│       ├── engine.py         FFmpeg, Whisper와 Qwen3 실행
│       ├── review.py         교정·문단 제안 검증
│       ├── records.py        강의·작업·제안 데이터 모델
│       ├── markdown.py       메타데이터와 시간 링크 생성
│       ├── output_files.py   결과 이름 변경과 WebM 재생 호환 처리
│       ├── storage.py        출력 폴더 조회와 안전한 접근
│       └── store.py          SQLite 영속화
└── tests/                    단위·회귀 테스트
```

더 자세한 설치, 파이프라인과 문제 해결 설명은 앱 실행 후 대시보드 오른쪽 위의 사용 안내
버튼 또는 <http://127.0.0.1:5055/guide>에서 확인할 수 있습니다. 소스 문서는
[web/static/guide.html](web/static/guide.html)입니다.

## 테스트

```bash
.venv/bin/python -m unittest discover -s tests -v
```

테스트는 외부 Whisper/Ollama 서버를 직접 요구하지 않으며 다음 핵심 동작을 검증합니다.

- 녹음 조각의 브라우저 선저장과 복구
- 업로드, 대기열, 취소와 재시도
- VAD 폴백과 저신뢰 구간 재확인
- Qwen3 응답 검증과 승인된 수정 적용
- 결과 이름 보호, 저장소 조회와 Markdown 생성
- 강의 설정과 경로 설정의 영속화

## 라이선스

아직 라이선스가 지정되지 않았습니다. 라이선스 파일이 추가되기 전에는 저작권자의 명시적
허락 없이 코드를 복제·수정·배포할 권리가 자동으로 부여되지 않습니다.

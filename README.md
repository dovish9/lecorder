# Lecorder

브라우저에서 강의를 녹음하거나 미디어 파일을 가져와 원본 음성과 타임스탬프가 있는 Markdown을 저장하는 로컬 우선 도구입니다. 강의노트의 용어와 문맥을 전사·검수에 활용하고, 노트의 개요와 페이지별 해설을 생성합니다.

현재 지원 환경은 **Apple Silicon macOS**입니다. Windows의 경로·실행 파일 탐색·Credential Manager·파일 탐색기 연결을 구현했지만, Windows 네이티브 도구 배포와 OCR 품질 검증, 실제 전체 전사는 후속 작업입니다. CI의 Windows 테스트는 이 범위를 대신 보증하지 않습니다.

## 주요 기능

- 녹음·일시정지·파일 업로드, 브라우저에 보관한 녹음 조각 복구
- 강의와 강의노트 선택, 힌트 없이 전사하는 **선택 안 함**
- 변환·전사 → 품질 확인 → LLM 검수 → 저장의 진행 상태와 상세 로그
- 전사와 별도로 재실행 가능한 검수, 수정 제안의 승인·거절, 실행 중 중단
- PDF·이미지 노트의 원문 추출, 용어집, 개요, 페이지별 해설과 원문 비교
- 다시 전사 실패·중단 시 이전 결과 보존, 노트 재분석 시 페이지 이미지 재사용
- 음성 전사와 LLM 각각의 제공자·모델·서버 설정, 작업별 설정 스냅샷

LLM 검수는 강의 선택 여부와 무관하게 실행합니다. 수정 제안을 승인하면 해당 전사문과 Markdown에 반영되며, 모델 학습이나 다음 녹음의 자동 교정 규칙으로 저장하지 않습니다.

## macOS 설치·실행

Xcode Command Line Tools와 Homebrew가 필요합니다. 저장소를 복제한 디렉터리에서 실행하세요.

```bash
bash scripts/setup.sh --dry-run  # 설치 예정 내역만 확인
bash scripts/setup.sh
cp .env.example .env  # 새 설치에서만 실행; 기존 설정을 덮어쓰지 마세요.
```

설치는 다음 순서로 실행하며 `[1/5]`부터 `[5/5]`까지 진행 상황을 표시합니다.

1. **필수 도구 확인:** macOS, Xcode Command Line Tools와 Homebrew를 확인합니다. 없으면 설치 방법을 안내하고 종료합니다.
2. **Python·Homebrew 도구 준비:** pyenv가 있으면 설치된 Python 3.13 중 최신 패치 버전을 사용하고, 없으면 pyenv로 3.13을 추가 설치합니다. pyenv가 없으면 기존 `python3` 또는 `python`을 사용하고, Python 자체가 없을 때만 Homebrew의 최신 안정 버전 `python`을 설치합니다. CMake는 Homebrew에 없을 때 설치합니다. 기존 Git(Apple Git 포함)을 사용하며 pyenv 전역·로컬 버전 설정은 변경하지 않습니다.
3. **Python 패키지 설치:** 선택한 Python에 `requirements.txt`를 설치·검사합니다. 기존 Python은 3.11 이상이어야 하며 패키지 호환성 검사 실패 시 중단합니다. Homebrew처럼 직접 설치가 제한된 Python은 `dependencies/python/site-packages`에 패키지를 설치합니다. 설치 성공 시 Python 경로를 기록하고 `start.command`가 같은 환경을 사용합니다.
4. **네이티브 빌드:** 고정된 소스로 Whisper → llama.cpp → Kiwi helper → Vision helper를 빌드합니다. 이 단계에서는 모델을 내려받지 않습니다.
5. **모델·데이터 준비:** Whisper large-v3·large-v3-turbo·Silero VAD·토크나이저, LLM GGUF·이미지 projector, Kiwi 모델·사전을 내려받고 체크섬을 검증합니다. 필수 실행 파일·패키지·모델 검사까지 통과해야 설치 완료로 표시합니다. AI 서버는 시작하지 않습니다.

모델 다운로드에는 상당한 디스크 공간과 시간이 필요합니다. 실패하면 원인을 해결하고 같은 명령을 다시 실행하세요. 기존 빌드 산출물과 검증된 모델을 재사용하며, 다운로드에 실패한 임시 파일은 제거합니다. 기존 모델의 체크섬이 다르면 임의로 덮어쓰지 않으므로 해당 파일을 별도로 보관하고 재시도하세요.

`.env`의 `LECORDER_OUTPUT_DIR`을 결과 저장 폴더로 설정하고 실행합니다.

```bash
./start.command
# 기본 화면: http://127.0.0.1:5055
python scripts/doctor.py
```

프로젝트와 결과 폴더는 화면 오른쪽 위 **시스템 설정 → 저장 위치**에서도 바꿀 수 있습니다. 경로 저장은 앱을 재시작합니다. AI 설정 저장은 다음 작업부터 적용됩니다.

llama.cpp 소스와 모델은 기본 `~/Local Storage/llama.cpp`에 두고 `dependencies/llama.cpp`에서 심볼릭 링크로 연결합니다. Whisper도 동기화 폴더 밖에 보관하고 `dependencies/whisper.cpp`로 연결할 수 있습니다. 고정 소스 커밋·패치·모델 다운로드 명세는 `scripts/llama`, `scripts/whisper`, `scripts/kiwi`에 있습니다. 기존 수정이 있는 네이티브 체크아웃을 설치 스크립트가 임의로 덮어쓰지 않습니다.

## 의존성

| 구분 | 도구 | 역할 / 설치 방식 |
|---|---|---|
| 실행 환경 | Python 3.13 권장, 기존 환경 3.11 이상 | pyenv → 기존 Python → Homebrew 순서로 선택 |
| 빌드 | Git, CMake, C++·Swift 컴파일러 | CMake는 Homebrew, Git은 기존 설치 재사용, 컴파일러는 Xcode CLT. 모델은 고정 URL·체크섬으로 별도 다운로드 |
| 직접 빌드 | whisper.cpp, llama.cpp, Kiwi helper | 음성 전사, LLM, 한국어 명사·복합어 추출. `dependencies/`에서 사용 |
| macOS 제공 | Vision·AppKit, Keychain, Finder | OCR, API 키 보관, 파일 위치 열기 |
| Python 패키지 | Flask, requests, jsonschema | 웹 서버, HTTP, 구조화 응답 검증 |
| Python 패키지 | keyring, tiktoken | OS 보안 저장소, tokenizer |
| Python 패키지 | av | PyAV의 미디어 변환·구간 추출·침묵 탐지. 별도 FFmpeg CLI 불필요 |
| Python 패키지 | pypdfium2, pdfplumber, Pillow, pillow-heif | PDF 렌더링·내장 텍스트/좌표 추출, 이미지·HEIC 처리 |
| Python 패키지·번들 자산 | mini-racer, KaTeX | 서버의 JavaScript 엔진으로 수식 검증. 브라우저를 닫아도 실행 |
| 개발 전용 | Node.js | 내장 테스트 러너로 프런트엔드 모듈 검증. 앱 실행에는 불필요 |
| 선택 | Tesseract + kor/eng 데이터 | 명시적으로 선택할 때만 사용하는 OCR. 기본 Vision과 품질이 동일하다는 보장은 없음 |

런타임 Python 패키지는 `requirements.txt`가 기준입니다. Ollama·Poppler·LibreOffice·curl은 앱 실행에 필요하지 않습니다. 설치 과정의 모델 다운로드는 Python에서 수행합니다. llama.cpp 기본 빌드는 별도 OpenSSL 라이브러리에 연결하지 않습니다.

## AI와 개인정보

| 영역 | 로컬 기본값 | 외부 연결 |
|---|---|---|
| 음성 전사 | whisper.cpp · large-v3 · `127.0.0.1:8080` | OpenAI `whisper-1` |
| 검수·해설·개요·의미 검증 | llama.cpp · Qwen3.5-9B Q4_K_M + F16 projector · `127.0.0.1:8081` | OpenAI·Claude·Gemini |

시스템 설정의 **음성 전사 / LLM**에서 제공자와 모델, 서버 URL, 필요한 키를 설정합니다. 로컬 자동 관리 모델은 파일 경로도 지정합니다. 사용자 지정 서버는 직접 실행해야 하며 앱이 시작·종료하지 않습니다. 연결 확인은 사용자 자료 대신 합성 입력을 사용합니다. 기본값 초기화는 해당 AI 영역만 복원하고 등록된 키를 삭제하지 않습니다.

- 로컬 자동 관리 서버는 요청 시 로드하고 연속 요청에서 재사용하며 기본 60초 유휴 후 해제합니다. 모델 전환 시 비활성 서버를 해제하며, 추론 조정기는 요청 경계에서 전사를 우선합니다.
- 외부 전사에는 음성이, 외부 LLM에는 전사문과 선택한 노트의 텍스트·이미지가 전송됩니다. API 요금이 발생할 수 있습니다.
- API 키는 macOS Keychain 또는 Windows Credential Manager에 저장합니다. 안전한 저장소가 없는 환경에서는 키 저장에 실패하며 평문 파일로 대체하지 않습니다. 조회 응답과 작업 기록에는 키 원문을 포함하지 않습니다.
- 새 작업은 제공자·모델·URL·옵션을 고정합니다. 다시 전사·다시 검수·다시 분석은 요청 시점의 최신 설정을 사용합니다.
- 인증·서버·모델 오류를 명시적으로 표시하고 다른 제공자로 자동 전환하지 않습니다. 일시적 요청 제한은 최대 1회 재시도합니다. 중단은 후속 요청과 결과 반영을 막지만 이미 접수된 외부 요청의 과금을 취소하지는 못합니다.

## 자료와 품질 정책

노트는 PDF 또는 PNG·JPEG·HEIC 이미지 묶음을 지원합니다(원본 합계 100MB, 최대 500페이지). PowerPoint는 PDF로 내보내서 등록하세요. pdfplumber의 내장 텍스트와 OCR을 대조하고 PDFium 렌더링 이미지를 LLM에 함께 전달합니다. 한국어 용어는 Kiwi로 조사를 분리하고 원문의 복합어를 보존합니다.

수식·응답 형식 등의 코드 검증 후 의미 검증을 수행합니다. 오류가 있으면 최대 1회 재생성하고, 해결하지 못한 항목은 **확인 필요**로 남깁니다. 원문 추출과 LLM 모두 오류 가능성이 있으므로 해설과 수정 제안의 근거를 확인하세요. 사진·손글씨·분수·기호를 항상 완벽하게 인식한다고 보장하지 않습니다.

| 위치 | 내용 |
|---|---|
| 결과 폴더 | 원본 미디어, 같은 이름의 Markdown |
| `web/data/lecorder.db` | 강의·작업·노트 버전·실행 로그·제안 |
| `web/data/recordings/` | 진행 중 업로드·녹음 조각과 복구 자료 |
| `web/data/notes/` | 원본 노트, 공유 페이지 이미지, 분석 자료 |
| `web/data/ai-settings.json` | 제공자와 모델 설정, 키 원문 없음 |
| 브라우저 IndexedDB | 서버 전송 전 녹음 조각과 복구 세션 |

DB·원본·설정·결과를 구조 정리 목적으로 재처리하지 않습니다. DB를 백업할 때는 SQLite 백업 API를 사용하거나 앱을 정상 종료하고 관련 파일을 함께 보관하세요. `dependencies/`, `web/data/`, `memories/`, `.env`는 Git에서 제외됩니다.

## 구조와 교체 지점

- `routes.py`의 `create_app`이 `Services`를 연결합니다. 앱 생성은 큐·모델을 시작하지 않습니다. 실행 호스트가 `services.start()` / `services.close()`를 호출합니다.
- `platforms.py`: `HostPlatform`·`SecretStore`, macOS/Windows 실행 경로·환경·파일 탐색·보안 저장소.
- `processes.py`: 취소 토큰과 Python 작업 프로세스. 미디어·PDF·수식·HTTP는 JSON 표준 입출력으로 요청합니다. `Services.create(..., worker=..., ocr=..., platform=...)`로 테스트/플랫폼 구현을 주입합니다.
- `managed_runtime.py` / `ai_runtime.py`: 공통 모델 수명주기와 제공자별 실행 인자, 앱별 레지스트리. 앱 소유 프로세스만 정리합니다.
- `speech_processing.py` / `speech_quality.py` / `review_engine.py`: 제공자 호출·구간 처리, 품질 보호, 검수. `engine.py`가 전체 전사를 조합합니다.
- `recording_intake.py` / `pipeline.py` / `recording_output.py`: 접수·복구, 대기·실행, 결과 저장·롤백.
- `note_repository.py` / `note_intake.py` / `note_extract.py` / `notes.py`: 노트 조회·선택, 접수, 추출, 분석 큐.
- `*_http.py`, `note_routes.py`, `ai_routes.py`: 기능별 HTTP 경계. 기존 URL과 응답 계약 유지.
- `web/static/dashboard/`: 상태·라우팅·강의·녹음·보관함·플레이어·업로드·대기열·알림·설정 모듈. `selectors.js`는 DOM 없이 상태를 계산하며 `dashboard.js`가 조립합니다.

Windows 기반에서는 `.exe`와 CMake `Release` 위치, 공백·한글·백슬래시 경로, 예약 파일명을 처리합니다. Vision은 호출하지 않으며 명시적인 미지원 오류를 반환합니다. Windows OCR 구현, 네이티브 빌드·모델 설치·런처·배포 패키지는 별도 준비해야 합니다. Linux는 모델 없는 테스트 대상이며 기본 보안 저장소와 파일 탐색 기능은 제공하지 않습니다.

## 검증

```bash
python scripts/test.py all          # 모델 없이 전체 회귀
python scripts/test.py unit         # 순수 규칙·경로·로그·상태
python scripts/test.py integration  # 임시 SQLite·API·큐·롤백·수명주기 계약
python scripts/test.py tools        # 실제 PDF·미디어·수식·HTTP 및 취소
python scripts/test.py frontend     # Node 내장 러너, ES module 직접 import
python scripts/doctor.py            # 설치 상태 읽기, 모델을 로드하지 않음
python -m unittest tests.native.test_setup  # macOS 설치 순서·실패 처리, 격리된 도구 사용
python scripts/verify_transcription.py sample.wav --language ko --llm --report memories/verification.json
python scripts/verify_runtime.py sample.wav --report memories/runtime-verification.json
python scripts/verify-live.py sample.wav --language ko --final --report memories/live-verification.json  # 16 kHz mono PCM WAV, 실제 속도로 공급
```

마지막 두 명령은 실제 모델을 사용하며 기본 포트 18080/18081과 임시 작업 폴더를 사용합니다. 필요한 도구·모델·표본이 없으면 실패합니다. `--speech-port`, `--llm-port`로 포트를 바꿀 수 있습니다. 실제 모델·OCR와 브라우저 검증은 CI의 모델 없는 검증과 구분합니다. 외부 제공자는 API 키 없이 계약만 테스트할 수 있으며 실사용 검증으로 간주하지 않습니다.

`.github/workflows/tests.yml`은 macOS·Windows·Linux의 모델 없는 테스트를 실행합니다. 같은 실제 표본의 전후 시간을 비교하고, 정답 전사문이 있을 때만 `scripts/evaluate_transcript.py`로 WER/CER를 평가하세요. 모델의 이전 출력을 정답으로 삼지 않습니다. 개발·실험 결과는 Git 제외 `memories/status.md`에 요약하고 원본 샘플은 `memories/samples/`에 보존합니다.

사용 흐름은 앱의 **사용 안내**(`/guide`)에 설명되어 있습니다.

설치 미리보기(`--dry-run`)는 패키지 설치·빌드·다운로드를 실행하지 않습니다. 기존 모델은 파일 존재 여부만 표시하고, 실제 설치에서 체크섬을 검증합니다. 최종 검사는 네이티브 도구를 `--help`로 실행해 라이브러리 로딩 실패와 비정상 종료도 확인합니다. 서버 시작·모델 추론 검사는 포함하지 않습니다.

### 녹음 중 실시간 초안

마이크 녹음 중 로컬 `large-v3-turbo`가 발화 단위 초안을 만듭니다. AudioWorklet의 16 kHz PCM과 Silero VAD 헬퍼를 사용하며, 원음 저장 경로와 분리되어 초안 실패·전송 지연에도 원음은 보존됩니다. 녹음 종료 후 선택된 음성 제공자로 전체 원음을 처리하고 LLM 검수를 진행합니다. 초안은 최종 검수 입력으로 사용하지 않습니다.

녹음 중 다른 앱 관리 로컬 AI 작업은 요청 경계에서 대기합니다. 일시정지는 예약을 유지하고, 종료·폐기·30초 생존 신호 만료 시 turbo와 예약을 정리합니다. 사용자 지정 서버와 외부 API는 예약 대상이 아닙니다. 실시간 모델·VAD 헬퍼는 기본 설치 과정에 포함되며 추가 런타임 패키지는 필요하지 않습니다.

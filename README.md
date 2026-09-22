# Lecorder

Apple Silicon Mac에서 음성을 보존하고 Whisper로 전사하는 로컬 강의 기록 도구입니다. 강의노트의 키워드는 음성 인식에, 출처가 있는 원문은 Qwen 검수에 사용합니다.

## 설치

Homebrew와 Xcode Command Line Tools를 준비한 뒤 실행합니다.

```bash
bash scripts/setup.sh --pptx
bash scripts/build-whisper.sh
cp .env.example .env
```

`--pptx`는 LibreOffice를 설치합니다. PDF·이미지만 사용한다면 생략할 수 있습니다. `.env`의 `LECORDER_OUTPUT_DIR`에는 출력 폴더를 지정하세요. Whisper 소스·모델은 `dependencies/whisper.cpp`, Vision 실행 파일은 `dependencies/vision/bin/vision-ocr`에 설치되며 별도의 Whisper 경로 설정은 필요 없습니다. iCloud 밖에 보관하려면 실제 `whisper.cpp`를 `~/Local Storage/whisper.cpp`로 옮기고 `dependencies/whisper.cpp`에 해당 폴더의 심볼릭 링크를 두어도 됩니다. 설치·실행 경로는 그대로 유지됩니다.

```bash
ollama pull qwen3.5:9b
ollama serve
./start.command
```

Ollama가 이미 실행 중이면 다시 시작하지 않습니다. 모델 다운로드는 처음 한 번 필요하며 문서와 음성의 처리는 로컬에서 수행합니다. 필요한 도구를 확인하려면 `.venv/bin/python scripts/doctor.py`를 실행하세요. Whisper 소스 기준 버전과 서버 패치는 `scripts/whisper/`에 있습니다. 설치 스크립트는 다른 Whisper 체크아웃의 수정 사항을 덮어쓰지 않습니다.

## 강의노트

강의 화면에서 PDF·PPTX 한 개 또는 PNG·JPEG·HEIC 이미지 묶음을 등록합니다. 이미지는 등록 전에 순서를 변경할 수 있습니다. 원본 합계 100MB, 최대 500페이지를 지원합니다. PPTX는 슬라이드 본문만 PDF로 변환합니다.

원본을 먼저 보존하고 페이지별 내장 텍스트와 OCR을 저장합니다. 재분석 시 페이지 이미지는 노트의 `pages-v1` 폴더에서 공유하고, OCR·해설 결과만 새 버전으로 저장합니다. 이미지 렌더링 규격을 바꿀 때는 공유 폴더의 버전도 변경해야 합니다. 한국어 키워드는 소스 빌드한 Kiwi로 명사를 추출해 조사를 분리하고, 붙어 있는 복합 명사는 보존합니다. 키워드가 준비되면 학습 정리를 기다리지 않고 전사에 사용할 수 있습니다.

강의노트는 **개요 / 학습 정리 / 키워드 / 원문**으로 나뉩니다. 학습 정리는 왼쪽 페이지 이미지와 오른쪽 Markdown 설명을 함께 표시하고, 키워드 출처는 해당 학습 정리 페이지로 이동합니다. 원문 탭은 추출 결과를 확인하는 디버깅 화면입니다. 수식 렌더링과 문법 검증은 프로젝트에 포함된 KaTeX로 로컬 처리합니다. 검증에 필요한 Node.js는 Brewfile로 설치합니다.

검수·문단 구분·강의노트 분석은 기본적으로 `OLLAMA_MODEL=qwen3.5:9b` 하나를 사용합니다. 신규 전사와 명시적인 재전사·검수 재시도부터 적용하며 과거 실행 기록은 보존합니다. `NOTE_VISION_MODEL`은 별도 모델이 필요할 때만 지정합니다.

학습 정리는 기본 `qwen3.5:9b` 비전 모델에 이미지와 추출 텍스트를 함께 전달합니다. 개요는 페이지별 설명을 작은 묶음으로 종합하며, 출처 번호는 모델 대신 코드가 연결합니다. 생성 후 JSON·수식·미완성 본문을 코드로 검사하고, 통과한 결과는 모델이 원자료와 의미를 대조합니다. 문제가 있으면 최대 한 번 재생성하며, 해결되지 않거나 검증하지 못한 결과는 이유와 함께 **확인 필요**로 보존합니다. 개요의 의미 검증은 입력한 페이지별 설명을 기준으로 합니다. 판독 불확실한 부분은 별도로 표시하며, 생성 설명은 원본을 대조할 수 있습니다. 추론 결과의 사실 정확성을 코드로 보증하는 것은 아닙니다. 분석 실패 시 원본·키워드·완료된 페이지 설명은 보존합니다. 이전 결과는 자동 덮어쓰지 않으며 **다시 분석**으로 새 버전을 생성합니다.

Kiwi는 `scripts/build-kiwi.sh`에서 고정된 소스 커밋과 모델을 내려받아 Homebrew CMake로 빌드합니다. Python Kiwi 패키지는 설치하지 않습니다. 소스·모델·실행 파일은 `dependencies/kiwi`, `dependencies/kiwi/build`, `dependencies/kiwi/bin/kiwi-helper`에 저장됩니다. [Kiwi 공식 소스와 Apache 2.0 라이선스](https://github.com/bab2min/Kiwi)를 사용하며, 빌드 스크립트에 기준 커밋을 기록했습니다.

기본 OCR은 macOS Vision입니다. `LECORDER_OCR_ENGINE=tesseract`로 변경할 수 있습니다. 둘 다 한국어·영어를 사용합니다. Vision 헬퍼는 설치 스크립트에서 빌드합니다.

## 전사와 검수

녹음은 노트 선택 창 없이 바로 시작합니다. 녹음 중 플로팅 컨트롤 위에서 같은 강의의 노트 하나 또는 **사용 안 함**을 선택하며, 일시정지 중에도 변경할 수 있습니다. 선택한 분석 버전은 서버에 즉시 저장되어 녹음 복구 시에도 유지됩니다. 기본값은 사용 안 함입니다. 파일 업로드와 보관함의 **다시 전사하기**에서는 선택 창에서 노트를 지정합니다. 재전사 창에는 기존 노트가 기본 선택됩니다. 실행에 고정된 설정과 노트 버전은 이후 변경 사항과 섞이지 않습니다.

- 기존 파일·작업과 이름이 겹치면 접수 시점부터 `(2)`, `(3)`을 붙입니다.
- 원본 음성을 먼저 출력 폴더에 보존한 뒤 Whisper를 실행합니다.
- 긴 음성은 약 120초 단위로 침묵 경계 또는 오버랩을 사용해 전사합니다.
- 자동 힌트는 실제 Whisper tokenizer로 96토큰 이내에서 선별합니다. tokenizer가 없는 외부 서버는 힌트 없이 전사합니다.
- 반복·VAD·저신뢰 재시도에는 힌트를 넣지 않습니다.
- Whisper 결과와 Markdown을 먼저 저장하고 **전사 완료**로 표시합니다.
- Qwen 검수는 독립 실행하며, 실패해도 전사 결과는 보존됩니다. **검수 다시 하기**로 검수만 재시도합니다.
- 제안은 사용자가 승인해야 현재 Markdown에 반영됩니다. 미래 작업에 적용되는 확정 교정 규칙은 없습니다.
- **다시 전사하기**는 노트를 다시 선택하고 같은 작업과 파일 경로에 결과를 교체합니다. 실패·중단 시 기존 결과는 유지합니다.

Whisper는 전사할 때 로드하고 기본 60초 유휴 후 해제합니다. Whisper와 Qwen 추론은 공유 실행 조정기로 직렬화하며 요청 경계에서 전사를 우선합니다. CPU 문서 추출은 독립적입니다.

## 저장·복구

`web/data/`의 DB와 원본·분석 자료는 Git에서 제외됩니다. DB 마이그레이션 전 같은 디렉터리에 백업을 생성합니다. 기존 수동 힌트·확정 교정 열은 호환성을 위해 남지만 실행하거나 자동 키워드로 이관하지 않습니다.

노트의 재분석과 키워드 선택은 새 버전으로 저장합니다. 노트를 목록에서 제거해도 과거 작업이 사용한 자료는 남습니다. 앱 재시작 시 미완료 추출은 저장한 페이지부터 복구하고, 중단된 검수는 실패 상태로 표시해 사용자가 재시도할 수 있습니다.

## 검증

```bash
python3 -m unittest discover -s tests
python3 scripts/doctor.py
python3 scripts/evaluate_transcript.py verified-reference.txt result.json --output accuracy.json
WHISPER_PORT=18080 python3 scripts/verify_transcription.py sample.wav --language en --report memories/verification.json
```

실제 통합 검증은 운영 DB·서버와 다른 폴더·포트를 사용합니다. 새 Mac의 깨끗한 설치와 장시간 실제 마이크 녹음은 아직 검증하지 않았습니다. 로컬 개발 기록은 Git에서 제외한 `memories/status.md`에 보관합니다. 로컬 서버 주소는 기본 `http://127.0.0.1:5055`입니다.

정확도 비교 도구는 제공된 정답에 대한 WER/CER와 치환·삭제·삽입 수를 계산합니다. 다른 모델의 전사 결과를 정답으로 사용하지 마세요. 한국어 WER는 띄어쓰기의 영향을 받으므로 공백을 제외한 CER도 함께 확인하세요.

## 폴더 구조

- `web/`: 앱 코드와 화면
- `scripts/`: 설치·빌드·진단·수동 평가 도구
- `tests/`: 자동 회귀 테스트
- `dependencies/`: 로컬 도구·모델·빌드 캐시 (Git 제외)
- `memories/`: 개발 요약·실험 근거·원본 샘플 (Git 제외)

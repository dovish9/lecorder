# 코드 구조와 설계 규칙

## 책임과 의존 방향

```text
app.py
└── routes.create_app                  앱 조립·HTTP 경계·수명주기 연결
    ├── Services                      프로세스·AI·작업 서비스의 소유자
    ├── recordings/                   접수·원음·큐·실시간·검토 작업
    ├── notes/                        원문·해설·개요·용어집
    └── library/                      강의·녹음·승인·검색·백업 저장 계약

web/backend/
├── execution_context.py              요청/작업의 실행 기반 문맥
├── ai_control.py                     요청 세대·일시정지·개별 취소
├── ai_runtime.py, managed_runtime.py  앱 소유 모델 프로세스
├── ai_providers.py, ai_transport.py   제공자 계약·취소 가능한 전송
├── ai_settings.py, runtime_config.py  설정 스냅샷·지원 설정 정의
├── request_history.py                요청별 사용량·완료 지점 재사용
├── atomic.py, publication.py          파일 게시·DB 반영 사이 복구
├── processes.py, *_worker.py          취소 가능한 격리 작업
├── migrations/                       순서 있는 SQLite 변경
├── result_codec.py, versions.py      저장 형식·처리 정책·버전
├── recordings/
│   ├── pipeline.py                   녹음 작업 큐와 처리 조합
│   ├── recording_intake.py           등록·조각 수신·복구·재실행
│   ├── recording_output.py           원음 보존·결과 승인·출력
│   ├── review_jobs.py                독립 검토 큐
│   └── live_transcription.py         별도 실시간 세션
├── notes/
│   ├── notes.py                      추출·학습 작업 조합
│   ├── note_repository.py            노트·리비전·페이지 저장
│   ├── note_extract.py               문서·OCR 어댑터
│   ├── note_study.py                 해설·개요 생성
│   ├── glossary.py, policy.py        근거 기반 용어집·고정 계약
│   └── note_validation.py            응답 구조·수식 검사
├── speech/
│   ├── engine.py                     음성 처리 단계 조합
│   ├── speech_processing.py          구간별 인식 요청
│   ├── speech_quality.py             누락·반복·저신뢰 보호
│   ├── review_engine.py              LLM 검토 요청
│   └── transcription.py              처리 입력·결과 자료형
└── library/
    ├── store.py                      DB 수명주기·트랜잭션·녹음 저장
    ├── courses.py                    강의 설정·선택 저장소
    ├── approvals.py                  제안 결정·불변 승인 근거
    ├── records.py                    도메인 자료와 공개 응답
    ├── insights.py                   검색 색인·사용량 조회
    └── backup.py                     명시적 백업 계약·검사·병합
```

HTTP는 입력 검증·응답 변환을, 기능 서비스는 순서·취소·복구를, 저장소는 SQL·결과 코덱을 담당합니다. 새로운 SQL은 해당 저장소에 추가합니다. DB 행이나 dataclass의 새 필드가 공개 API·백업에 자동 노출되지 않게 계약을 명시합니다.

앱 조립 지점이 의존성을 연결합니다. 기능이 전역 서비스 전체를 조회하지 않습니다. `ExecutionResources`는 요청·워커를 따라 전달하는 AI 실행 제어·전송·모델·요청 기록 같은 기반만 담으며 다른 기능을 찾는 레지스트리가 아닙니다. 독립 검증 도구는 자체 실행 자원을 생성하고 종료합니다.

## 작업과 저장

사용자 취소 토큰과 개별 AI 요청 토큰을 구분합니다. AI OFF는 개별 요청만 취소하며 작업 자체를 실패시키지 않습니다. 완료 결과는 입력·모델·단계 정책이 같은 경우에만 재사용합니다. 사용자 취소·삭제·종료가 재개보다 우선합니다.

최종 음성·LLM은 기존 로컬 추론 순서를 사용하고 실시간 음성은 별도 서버로 실행합니다. 실시간 세션이 일반 큐를 예약하거나 녹음 중 일반 작업을 정지시키지 않습니다. 외부 서버와 앱 소유 서버를 구분해 앱 소유 프로세스만 해제합니다.

파일 교체는 `atomic.publish_file`, 텍스트 저장은 `atomic.write_text`를 사용합니다. 최종 파일의 숨김 속성을 제거하고, DB와 파일을 함께 바꾸는 경우 `Publication` 저널로 미완료 게시를 복구합니다. 이름 변경·승인·재실행에서도 원음과 기존 확정본을 보존합니다.

## 화면과 디자인 소유권

```text
web/static/
├── dashboard.js                      화면 서비스 조립·수명주기
├── dashboard.html                    앱 셸·화면 의미 구조
├── dashboard/
│   ├── state.js                      앱 공유 상태
│   ├── routing.js, shell.js           탐색·대화상자·공통 알림
│   ├── capture.js                    마이크 녹음과 도구 제어
│   ├── capture-recovery.js           조각 저장·수신 확인·재전송
│   ├── courses.js, library.js         강의·보관함 화면
│   ├── search.js                     검색·필터·키보드
│   ├── usage-panel.js, usage.js       사용량 화면·집계·그래프
│   ├── recovery.js, transfers.js      복구 안내·백업 작업
│   └── components.js                 공통 선택 메뉴·표시 형식
├── styles/                           토큰·공통 요소·화면별 CSS
├── notes.js, notes.css                노트 뷰어
└── guide.html, guide.js, guide.css     사용 안내
```

화면별 DOM 참조는 해당 화면이 소유합니다. 조립 지점의 `actions`에는 다른 화면이 실제 사용하는 API만 노출하고 내부 렌더 도우미를 넣지 않습니다. 탐색 중인 강의와 녹음 대상은 별개입니다. 설정 저장 작업은 화면 탭의 수명주기와 분리하며, 오래된 비동기 응답은 현재 편집 상태를 덮지 않습니다.

CSS 진입점 `dashboard.css`는 토큰 → 기반 → 공통 컴포넌트 → 화면 파일을 불러옵니다. 공통 요소의 모양을 화면 끝에 다시 덮어쓰지 말고 소유 파일에서 변경합니다. 상태·반응형 규칙도 같은 파일에 둡니다. `!important`는 숨김·동작 감소·브라우저 자체 UI 억제처럼 필요한 불변 조건에만 사용합니다.

HTML과 정적 자원은 서버의 공통 캐시 정책으로 매번 재검증합니다. 파일마다 날짜 쿼리를 붙여 수동으로 캐시를 무효화하지 않습니다.

새 추상화는 실제로 공유하는 의미와 변경 이유가 있을 때만 추가합니다. 한 줄을 중계하는 관리자나 파일 크기만을 위한 분리는 피합니다. 반대로 다른 수명주기나 오류 처리를 하나의 기능으로 합치지 않습니다.

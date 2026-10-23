# YouthLink RAG

YouthLink RAG 기반 청년 지원 챗봇 및 신청 가이드 시스템의 백엔드입니다.

- FastAPI 서버 하나가 프론트용 공개 API, 채팅 저장, RAG 파이프라인을 맡습니다. 로그인은 없고 브라우저별 익명 ID로 사용자를 구분합니다 (Spring은 두지 않기로 결정, [설계 문서](docs/design.md) 15절)
- 정책 데이터와 임베딩은 ETL 서버가 적재하고, 평가 서버는 내부 API(`/internal/pipeline`)로 RAG 성능을 평가합니다
- 공개 API(`/api/sessions` 등, 프론트 계약 기준)는 9~10주차에 구현합니다 ([8주차 계획](docs/weekly/week08.md) 7절)

## 설계 및 작업 기록

- [설계 문서](docs/design.md): 시스템 구성, 파이프라인 구조, 설계 결정
- [주차별 작업 기록](docs/weekly/): 주차별 계획, 결정 사항, 결과

## 기술 스택

- Python 3.12, [uv](https://docs.astral.sh/uv/)
- FastAPI, Uvicorn, Pydantic, pydantic-settings
- PostgreSQL + pgvector, SQLAlchemy 2.1 (async), asyncpg
- OpenAI: 임베딩 `text-embedding-3-small`, 답변 생성 `gpt-6-luna`

## 시작하기

### 사전 준비

- Python 3.12 이상
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL + pgvector (ETL 서버의 스키마와 정책 데이터가 적재된 DB)

### 설치

```bash
uv sync
```

### 환경 변수

```bash
# macOS / Linux
cp .env.example .env

# Windows
copy .env.example .env
```

`.env`에 필요한 값을 채웁니다. `.env`는 커밋하지 않습니다.

### 실행

```bash
uv run uvicorn app.main:app --reload
```

- API 문서 (Swagger UI): http://127.0.0.1:8000/docs
- API 문서 (ReDoc): http://127.0.0.1:8000/redoc

### 테스트

```bash
uv run pytest
```

- 채팅 저장소 테스트는 실제 PostgreSQL에 테스트 전용 DB를 만들어 시험하고, 끝나면 삭제합니다. `.env`에 `TEST_DATABASE_URL`을 넣어 주세요 (DB 이름은 `_test`로 끝나야 함, 예: `.../youthlink_test`).
- `TEST_DATABASE_URL`이 없거나 DB에 연결할 수 없으면 DB 테스트는 건너뜁니다.
- 테스트 DB 스키마는 ETL `db/schema.sql`의 복사본(`tests/sql/`)입니다. ETL 스키마가 바뀌면 함께 갱신합니다.

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/health` | 서버 상태 확인 |
| POST | `/internal/pipeline` | 프로필 + 질문 → 추천 정책 + 답변 + trace (저장 없음) |
| POST | `/api/rooms` | 채팅방 생성 (프로필은 이때만 받음) |
| GET | `/api/rooms` | 사용자의 채팅방 목록 |
| POST | `/api/rooms/{room_id}/messages` | 질문 전송 → 답변 + 추천 정책 (대화 저장) |
| GET | `/api/rooms/{room_id}/messages` | 대화 기록 조회 |

### `POST /internal/pipeline`

```json
{
  "question": "월세 지원받을 수 있는 정책 있어?",
  "profile": {
    "age": 24,
    "annual_income": 20000000,
    "job_code": "0013003"
  }
}
```

- `profile`은 생략할 수 있고, 각 항목도 모두 선택입니다.
- 응답은 `answer`(답변), `policies`(카드로 보여 줄 정책, 답변 본문과 같은 순서), `trace`(실행 정보)로 구성됩니다.
- `policies`의 각 카드: `policy_no`, `policy_name`, `application_start_date`, `application_end_date`, `application_url`, `score`, `application_status`, `supervising_org_name`
  - `application_status`: 서버가 계산한 신청 상태 `OPEN`(신청 가능) / `ALWAYS_OPEN`(상시 모집) / `UPCOMING`(신청 예정) / `CLOSED`(마감) / `UNKNOWN`(기간 정보 없음). 마감 코드 정책은 기간이 남아 있어도 `CLOSED`라, 날짜는 `OPEN`·`UPCOMING`일 때만 표시하는 것을 권장합니다
  - `application_url`: 원본 신청 링크를 정리한 값(`https://` 보정, 주소가 아닌 값은 `null`). 절반 이상이 사이트 첫 화면이라 버튼 문구는 "관련 사이트"처럼 중립적으로 쓰는 것을 권장합니다
- 요청 검증 실패는 422, OpenAI·DB 호출 실패는 503을 반환합니다.
- 자세한 형식은 `/docs`와 [5주차 작업 기록](docs/weekly/week05.md)을 참고하세요. `trace` 구성은 아래 debug 모드 기준입니다.

#### debug 모드 (`?debug=true`)

RAG 평가·디버깅용입니다. 근거 원문 같은 내부 정보는 debug 요청에만 포함합니다.

| 요청 | `trace`에 포함되는 키 |
|---|---|
| `POST /internal/pipeline` (기본) | `latency_ms`, `tokens`, `chat_model`, `retriever`, `top_k`, `prompt_version`, `embedding_model`, `handler`, `analysis`, `current_policy_nos`, `cards` |
| `POST /internal/pipeline?debug=true` | 위 키 + `contexts`, `retrieved_chunks` |

- `contexts`: LLM에 넘긴 후보 정책별 근거 블록 (`list[str]`, 최대 `CARD_CANDIDATES`개). 신청 상태·링크와 그 정책의 청크 전체가 들어 있습니다. 검색된 정책이 없으면 `[]`
- `cards`: 카드 결정 결과. `candidate_nos`(근거 순서의 후보 정책), `selected`(LLM이 고른 근거 번호, 1부터), `fallback`(답변 첫 줄 형식이 없어 후보 전체를 카드로 보인 경우 `true`)
- `retrieved_chunks`: 검색된 청크 목록 (`chunk_id`, `policy_no`, `chunk_type`, `chunk_index`, `score`, `content`), 검색 점수순

### 채팅 API (`/api/rooms`)

대화를 저장하는 API입니다. 지금은 Swagger와 확인 스크립트로 사용하며, 공개 API의 세션·채팅(`/api/sessions`)으로 대체할 예정입니다.

```
1. POST /api/rooms                     {"user_id": "u1", "profile": {"age": 24}}  → 201 {"room_id": "..."}
2. POST /api/rooms/{room_id}/messages  {"user_id": "u1", "question": "..."}       → {"message_id", "answer", "policies"}
3. GET  /api/rooms/{room_id}/messages?user_id=u1                                  → {"messages": [...]}
```

- 채팅방 프로필은 생성할 때만 받고 이후 수정하지 않습니다. 조건이 바뀌면 새 채팅방을 만듭니다.
- `user_id`가 채팅방 소유자와 다르거나 채팅방이 없으면 404입니다. 서버는 인증하지 않고 `user_id`를 그대로 믿습니다 (공개 API에서는 `X-Client-Id`).
- 질문과 답변은 답변까지 성공한 뒤 한 번에 저장합니다. 실패하면(503) 아무것도 저장하지 않습니다.
- `policies`는 `/internal/pipeline`과 같은 형식이고, 기록 조회에서도 같은 형식으로 복원됩니다. 신청 상태는 조회 시점 기준으로 다시 계산합니다.
- 6주차에는 대화를 저장만 하고, 이전 대화를 답변에 활용하는 것은 7주차부터입니다.
- 채팅 테이블은 ETL 저장소의 마이그레이션 008이 적용된 DB가 필요합니다.

## 디렉터리 구조

```
app/
├─ main.py         # 앱 생성, lifespan, 라우터 등록
├─ api/            # 라우터 (health, internal, rooms)
├─ core/           # 설정, 의존성 주입
├─ schemas/        # 요청·응답 스키마
├─ llm/            # OpenAI 호출 (임베딩, 답변 생성)
├─ pipeline/       # RAG 처리 흐름 (검색, 정책 묶기, 답변 생성). DB에 쓰지 않음
├─ services/       # 채팅 처리 흐름 (소유 확인 → 파이프라인 → 저장)
├─ repositories/   # DB 접근 (정책 테이블은 읽기, 채팅 테이블은 읽기·쓰기)
└─ prompts/        # 프롬프트 파일 (버전별)
docs/              # 설계 문서, 주차별 작업 기록
```

파일 단위 구조는 [설계 문서](docs/design.md)의 3-1을 참고하세요.

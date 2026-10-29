# YouthLink Backend

YouthLink RAG 기반 청년 지원 챗봇 및 신청 가이드 시스템의 백엔드입니다.

- FastAPI 서버 하나가 프론트용 공개 API, 채팅 저장, RAG 파이프라인을 맡습니다. 로그인은 없고 브라우저별 익명 ID로 사용자를 구분합니다 (Spring은 두지 않기로 결정, [설계 문서](docs/design.md) 15절)
- 정책 데이터와 임베딩은 ETL 서버가 적재하고, 평가 서버는 내부 API(`/internal/pipeline`)로 RAG 성능을 평가합니다
- 공개 API(`/api/sessions` 등, 프론트 계약 기준)는 세션·채팅 SSE 뼈대까지 구현했고, 나머지는 9~10주차에 구현합니다 ([8주차 계획](docs/weekly/week08.md) 7절, [프론트 연동 현황](docs/front-sync.md))
- 배포 설정(컨테이너 이미지 등)은 별도 배포 저장소에서 관리합니다. 운영에 필요한 조건과 주의사항은 [운영 안내](docs/deploy.md)를 참고하세요

## 설계 및 작업 기록

- [설계 문서](docs/design.md): 시스템 구성, 파이프라인 구조, 설계 결정
- [주차별 작업 기록](docs/weekly/): 주차별 계획, 결정 사항, 결과

## 기술 스택

- Python 3.12, [uv](https://docs.astral.sh/uv/)
- FastAPI, Uvicorn, Pydantic, pydantic-settings
- PostgreSQL + pgvector, SQLAlchemy 2.1 (async), asyncpg
경- LLM: Bedrock 게이트웨이(OpenAI 호환, OpenAI SDK로 호출). 임베딩 `bedrock-titan-embed-v2`(1024차원), 답변 생성 `bedrock-gpt-5.6-luna`

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
- 운영(`APP_ENV=prod`)에서는 API 문서가 꺼집니다
- 운영 설정·보안·요청 제한은 [운영 안내](docs/deploy.md)를 참고하세요

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
| POST | `/api/sessions` | 세션(채팅방) 생성 (프로필은 이때만 받음) |
| GET | `/api/sessions` | 내 세션 목록 (만든 시각 내림차순) |
| GET | `/api/sessions/{session_id}` | 세션 하나 |
| GET | `/api/sessions/{session_id}/messages` | 메시지 이력 (답변의 카드 포함) |
| POST | `/api/sessions/{session_id}/chat` | 질문 → 답변 SSE 스트리밍 (지금은 RAG 없는 뼈대) |

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
- 질문 분석 결과에 따라 처리 방식(핸들러)이 달라집니다 ([설계 문서](docs/design.md) 9절).
  - 검색: 정책 찾기·추천, 처음 묻는 정책 이름. 1차 필터 → 검색 → LLM이 카드를 고름
  - 상세: 카드 번호나 "그거"로 가리킨 정책. 검색 없이 그 정책만 근거로 쓰고, 카드도 그 정책
  - 짧은 답변: 인사, 사용법, 범위 밖. 검색·근거 없음, 카드 없음
  - 되묻기: 애매한 질문, 없는 카드 번호. LLM 없이 고정 문구. 보기로 든 정책이 답변 번호 순서대로 카드에 들어감
  - 상세·되묻기 카드는 검색하지 않아 `score`가 0입니다
- `policies`의 각 카드: `policy_no`, `policy_name`, `application_start_date`, `application_end_date`, `application_url`, `score`, `application_status`, `supervising_org_name`, `region_label`
  - `application_status`: 서버가 계산한 신청 상태 `OPEN`(신청 가능) / `ALWAYS_OPEN`(상시 모집) / `UPCOMING`(신청 예정) / `CLOSED`(마감) / `UNKNOWN`(기간 정보 없음). 마감 코드 정책은 기간이 남아 있어도 `CLOSED`라, 날짜는 `OPEN`·`UPCOMING`일 때만 표시하는 것을 권장합니다
  - `region_label`: 정책 지역 표시 문구 ("서울특별시 강남구", "경기도 5곳", "전국")
  - `application_url`: 원본 신청 링크를 정리한 값(`https://` 보정, 주소가 아닌 값은 `null`). 절반 이상이 사이트 첫 화면이라 버튼 문구는 "관련 사이트"처럼 중립적으로 쓰는 것을 권장합니다
- 요청 검증 실패는 422, OpenAI·DB 호출 실패는 503을 반환합니다.
- 서버에 `INTERNAL_API_KEY`가 설정돼 있으면 `X-Internal-Key` 헤더에 같은 값을 보내야 합니다 (아니면 401). 로컬에서 비워 두면 검사하지 않습니다. 운영(`APP_ENV=prod`)에서는 필수입니다.
- 자세한 형식은 `/docs`와 [5주차 작업 기록](docs/weekly/week05.md)을 참고하세요. `trace` 구성은 아래 debug 모드 기준입니다.

#### debug 모드 (`?debug=true`)

RAG 평가·디버깅용입니다. 근거 원문 같은 내부 정보는 debug 요청에만 포함합니다.

| 요청 | `trace`에 포함되는 키 |
|---|---|
| `POST /internal/pipeline` (기본) | `latency_ms`, `tokens`, `chat_model`, `retriever`, `top_k`, `prompt_version`, `embedding_model`, `handler`, `answer_kind`, `analysis`, `current_policy_nos`, `cards`, `conditions` |
| `POST /internal/pipeline?debug=true` | 위 키 + `contexts`, `retrieved_chunks` |

- `contexts`: LLM에 넘긴 후보 정책별 근거 블록 (`list[str]`, 최대 `CARD_CANDIDATES`개). 신청 상태·링크와 그 정책의 청크 전체가 들어 있습니다. 검색된 정책이 없으면 `[]`
- `handler`: `search` / `detail` / `short` / `clarify`. `answer_kind`: 답변 조각 `recommend` / `detail` / `short` / `clarify`
- `prompt_version`: 조립한 프롬프트 조각 이름을 `+`로 이은 값 (예: `answer_common_v1+answer_cards_v1+answer_recommend_v1`). 되묻기는 LLM을 부르지 않아 `null`, `tokens`도 0
- `cards`: 카드 결정 결과. `candidate_nos`(근거 순서의 후보 정책), `selected`(LLM이 고른 근거 번호, 1부터), `fallback`(답변 첫 줄 형식이 없어 후보 전체를 카드로 보인 경우 `true`)
- `conditions`, `retrieved_chunks`: 검색 핸들러일 때만 있습니다. `conditions`는 1차 필터 조건(`lines`, 항목마다 질문·프로필 출처), 필터 후 정책 수(`filtered_policy_count`, 조건이 없으면 `null`)
- `retrieved_chunks`: 검색된 청크 목록 (`chunk_id`, `policy_no`, `chunk_type`, `chunk_index`, `score`, `content`), 검색 점수순

### 공개 API (`/api/sessions`)

브라우저(프론트)가 부르는 API입니다. 형식은 [공개 API 계약](docs/api-contract.md)과 프론트 계약(`YouthLink_Front/spec/api-contract.md`)을 따릅니다.

```
1. POST /api/sessions                  {"profile": {"age": 24}}                    → 201 {"id", "title", "profile", "createdAt"}
2. POST /api/sessions/{id}/chat        {"clientMessageId": "<uuid>", "content": "..."} → SSE token… policies → done
3. GET  /api/sessions/{id}/messages                                                 → [{"id", "clientMessageId", "role", "content", "policies", "createdAt"}]
```

- 모든 요청에 `X-Client-Id` 헤더(UUID v4)가 필요합니다. 없거나 형식이 틀리면 `400 INVALID_CLIENT_ID`, 다른 사용자의 세션이나 없는 세션은 `404 SESSION_NOT_FOUND`입니다.
- 오류는 `{code, message, details}` 형식입니다. 입력 검증 실패는 `400 VALIDATION_FAILED`이고 `details`에 필드별 이유가 들어갑니다. (`/internal/*`은 FastAPI 기본 형식 그대로)
- 세션 프로필은 생성할 때만 받고 이후 수정하지 않습니다. 지금은 `/internal/pipeline`과 같은 DB 코드 형식이며, 프론트 enum 형식은 계약 합의 뒤에 바뀝니다.
- 채팅은 지금 **SSE 뼈대**입니다. RAG 없이 계약 §5.4 조건(15초 대기, 1초 간격 `token` 90개, 15초마다 `:ping`, `policies`, `done`)으로 고정 문장을 보내고 저장하지 않습니다. 대기·길이는 `CHAT_SKELETON_*` 설정으로 바꿀 수 있습니다.
- 세션·입력 오류는 스트림을 시작하기 전에 JSON으로 응답합니다. `curl -N`으로 보면 이벤트가 오는 대로 찍힙니다.
- 요청 제한: IP당 분당 300회, 채팅은 IP당 분당 60회 + `X-Client-Id`당 분당 10회, 서버 전체 하루 LLM 1,000회. 넘으면 `429 RATE_LIMITED` + `Retry-After`. 값은 `.env`로 바꾸고 `0`이면 끕니다.
- 본문이 256KB를 넘으면 `413 PAYLOAD_TOO_LARGE`입니다.
- 채팅 테이블은 ETL 저장소의 마이그레이션 008이 적용된 DB가 필요합니다.

## 디렉터리 구조

```
app/
├─ main.py         # 앱 생성, lifespan, 라우터 등록
├─ api/            # 라우터 (health, internal, sessions)
├─ core/           # 설정, 의존성 주입, 공개 API 오류 형식, SSE, 요청 제한, 보안 미들웨어
├─ schemas/        # 요청·응답 스키마
├─ llm/            # OpenAI 호출 (임베딩, 답변 생성)
├─ pipeline/       # RAG 처리 흐름 (검색, 정책 묶기, 답변 생성). DB에 쓰지 않음
├─ services/       # 채팅 처리 흐름 (소유 확인 → 파이프라인 → 저장), 채팅 SSE 뼈대
├─ repositories/   # DB 접근 (정책 테이블은 읽기, 채팅 테이블은 읽기·쓰기)
└─ prompts/        # 프롬프트 파일 (버전별)
docs/              # 설계 문서, 주차별 작업 기록
```

파일 단위 구조는 [설계 문서](docs/design.md)의 3-1을 참고하세요.

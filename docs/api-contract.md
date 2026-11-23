# YouthLink 공개 API 계약

- 버전: **v1.4** (2026-11-22)
- 범위: 브라우저(React) ↔ 백엔드(FastAPI) 공개 API `/api/*`. 평가 서버용 `/internal/*`은 다루지 않음
- 이 문서 하나가 기준이다. 프론트 초안 v0.3(`YouthLink_Front/spec/api-contract.md`)을 함께 읽지 않아도 되게 모든 규칙을 적었다. 필드·타입은 서버의 OpenAPI 명세(`/docs`)로도 확인할 수 있다
- 표시: **(나중)** = 형식은 확정, 구현은 아직

## 0. 프론트에서 확인할 것 (v1.3 → v1.4)

> v1.4에서 새로 생기거나 바뀐 것만. 백엔드 구현은 **(나중)** 표시가 없어질 때까지 진행 중
> "삭제된 것"(과도기 호환·뼈대)은 **v1.4 백엔드 배포부터 적용**. 그 전까지 서버는 v1.3처럼 동작함

**추천 에이전트 패널 (새 기능, 4.8)**
- [ ] 채팅방 우측 버튼으로 패널을 열면 `GET /api/sessions/{sessionId}/agent`. 처음이면 시작 상태(인사 + 분야 질문 + 프로필 기준 카드), 대화한 적이 있으면 이어서 보여 줌
- [ ] 선택지 칩(`options`)을 누르면 **칩 글자를 그대로** `POST …/agent/messages`의 `content`로 보냄. "직접 입력" 칩은 서버가 주지 않으니 프론트가 항상 붙임. 질문 없는 턴(`question`이 `null`, `options`가 `[]`)에도 직접 입력은 보이게
- [ ] 응답은 SSE: `progress`(0회 이상, "찾는 중" 표시) → `cards`(카드가 바뀌었을 때만) → `message` → `done`. 보통 **3~6초**라 `progress` 문구로 기다림을 보여 줌
- [ ] 우측 카드 목록은 `cards` 이벤트를 받을 때마다 **통째로 교체**. 카드는 **최대 5개**이고 카드마다 `reason`(추천 이유, 시작 카드는 `null`)이 있음
- [ ] (선택) 카드 목록 소제목을 단계별로: 시작 카드(`reason`이 모두 `null`)는 "프로필에 맞춘 추천", 에이전트가 고른 카드(`reason` 있음)부터는 "대화로 좁힌 추천" + 카드마다 추천 이유. 계약 변경 없이 `reason` 유무로 구분
- [ ] (선택) 카드 목록 아래 "이 분야 정책 더 보기" 링크: 기존 채팅방 안 정책 검색 `GET /api/policies?sessionId=…&category=…`를 엶 (4.4, 백엔드 변경 없음). 분야는 첫 질문에서 고른 칩 기준, 직접 입력으로 시작했으면 `category` 없이
- [ ] `error` 이벤트면 서버는 아무것도 저장하지 않음. 카드는 그대로 두고 같은 `content`로 다시 보내기 버튼
- [ ] `done`·`error` 없이 연결이 끊기면 저장됐는지 알 수 없으니 **`GET …/agent`로 다시 받은 뒤** 마지막 메시지가 내 답이 아니면 다시 보내기 버튼 (중복 저장 방지)
- [ ] 보낸 사용자 메시지는 응답에 오지 않음. 화면에 먼저 그린 말풍선을 그대로 쓰고, 다시 열면 `GET`에 들어 있음
- [ ] 같은 채팅방에서 에이전트 답변이 진행 중이면 `409 STREAM_IN_PROGRESS`. 답을 받기 전에는 칩·입력을 막음 (채팅과는 따로 셈)
- [ ] "새로 시작" 버튼은 `DELETE …/agent` → 다시 `GET`으로 시작 상태
- [ ] 패널을 닫아도 대화는 남음. 채팅방을 지우면 함께 지워짐

**채팅에서 물어보기 (새 기능, 4.9)**
- [ ] **채팅방 안에서 연 정책 상세**에만 "채팅에서 물어보기" 보조 버튼 (신청 가이드 버튼이 주 버튼). 채팅방 밖 상세에는 두지 않음
- [ ] 누르면 `PUT /api/sessions/{sessionId}/current-policy` → `204` 뒤 상세(와 에이전트 패널)를 닫고 채팅 입력창에 초점
- [ ] 입력창 위에 "이야기 중: ○○" 표시, placeholder는 "○○에 대해 물어보세요". **글자를 자동으로 넣거나 보내지 않음**
- [ ] 표시는 **누른 뒤** 사용자가 질문을 보내면 없앰 (그 뒤 현재 정책은 채팅 흐름대로 바뀜, 5.3). 누르기 전에 보낸 질문의 답변이 끝나도(`done`) 표시는 그대로 둠

**삭제된 것 (과도기 호환·뼈대)**
- [ ] v0.3 값이 이제 `400 VALIDATION_FAILED`: 프로필·정책 검색의 시도 enum `region`(`SEOUL` 등), 정책 검색 `category=FINANCE`. 카드의 v0.3 `region` 필드는 더 이상 오지 않음 (`regionLabel`만). 프론트 `origin/main`(11-15)에서 쓰지 않는 것을 확인함
- [ ] 테스트·mock에 남은 v0.3 값(`SEOUL`, `FINANCE`, `NATIONWIDE`)은 정리 권장 (실서버와 모양이 달라짐)
- [ ] 채팅 뼈대(`CHAT_SKELETON_ENABLED`)는 지움. 배포 경로 SSE는 실제 답변으로 확인됨 (6.3)

**배포 설명 정정 (6절)**
- [ ] 계약의 배포 설명을 실제(k3s + Traefik, 같은 출처, HTTP)로 고침. 프론트 체크리스트의 "RAG에 회신" 항목은 이걸로 갈음

## 1. 공통 규칙

### 1.1 호출 방식
- 모든 경로는 `/api`로 시작한다. 프론트는 상대 경로로 부르고, 개발 때는 Vite 프록시(`DEV_API_TARGET`), 배포 때는 Traefik이 **FastAPI 서버**로 넘긴다 (6절)
- 요청·응답 본문은 `application/json; charset=utf-8`. 채팅과 추천 에이전트 답하기 응답만 `text/event-stream` (5절, 4.8)
- 필드 이름은 camelCase

### 1.2 사용자 식별 `X-Client-Id`
| 헤더 | 필수 | 값 |
|---|---|---|
| `X-Client-Id` | 모든 요청 | UUID v4. 로그인 대신 브라우저마다 만드는 익명 사용자 ID |

- 없거나 UUID 형식이 아니면 `400 INVALID_CLIENT_ID`. 대문자·하이픈 없는 형태도 같은 ID로 받음
- 세션·스크랩은 이 값으로 주인을 구분한다. 다른 사용자의 세션에 접근하면 존재를 숨기려고 `404 SESSION_NOT_FOUND`
- 정책 검색·상세, 지역 목록은 형식만 확인한다 (사용자마다 응답이 같음). 단 `sessionId`를 준 정책 검색은 세션 API처럼 주인을 확인한다 (4.4)

### 1.3 날짜와 시간
- 날짜 `YYYY-MM-DD` (예: `2026-09-30`). 신청 기간에 씀
- 일시 ISO 8601, 한국 시간 오프셋 포함 (예: `2026-10-25T20:38:26.355621+09:00`. 소수점 초가 붙을 수 있음)
- 신청 상태(`status`)는 서버가 한국 날짜 기준으로 계산한다. D-day는 프론트가 한국 날짜 기준으로 계산

### 1.4 오류 형식
2xx가 아닌 JSON 응답은 모두 아래 형식이다. `message`는 화면에 그대로 보여 줄 수 있는 한국어 문장이다.
```json
{ "code": "SESSION_NOT_FOUND", "message": "세션을 찾을 수 없습니다." }
```
입력 검증 실패는 필드별 `details`가 붙는다. `field`는 요청 필드 이름(`profile.age`, `content`, 검색 쿼리 이름 `keyword` 등)이다.
```json
{ "code": "VALIDATION_FAILED", "message": "입력값을 확인해 주세요.",
  "details": [{ "field": "profile.age", "reason": "15 이상이어야 합니다." }] }
```

| HTTP | code | 상황 |
|---|---|---|
| 400 | `INVALID_CLIENT_ID` | `X-Client-Id` 없음·형식 오류 |
| 400 | `VALIDATION_FAILED` | 필수값 누락, 형식·범위 오류, 깨진 JSON, 모르는 지역 코드 |
| 403 | `FORBIDDEN` | 프록시(Traefik)를 거치지 않은 요청 (출처 확인을 켰을 때만, 6.2) |
| 404 | `SESSION_NOT_FOUND` | 없는 세션, 다른 사용자의 세션, UUID가 아닌 세션 id |
| 404 | `POLICY_NOT_FOUND` | 없는 정책, 비활성 정책 |
| 404 | `GUIDE_NOT_FOUND` | 만료되었거나 없는 가이드 PDF 링크 (4.7) |
| 404 | `NOT_FOUND` | 없는 경로 |
| 405 | `METHOD_NOT_ALLOWED` | 허용되지 않는 메서드 |
| 409 | `STREAM_IN_PROGRESS` | 같은 세션에서 답변을 만드는 중 (5.4). 추천 에이전트는 따로 셈 (4.8) |
| 409 | `IDEMPOTENCY_CONFLICT` | 같은 `clientMessageId`에 다른 질문 (5.4) |
| 413 | `PAYLOAD_TOO_LARGE` | 본문 256KB 초과 |
| 429 | `RATE_LIMITED` | 요청 제한 초과, 하루 LLM 상한 (1.6). `Retry-After` 헤더(초) |
| 500 | `INTERNAL_ERROR` | 그 밖의 서버 오류 |
| 502 | `RAG_UPSTREAM_ERROR` | LLM 호출 실패 |
| 504 | `RAG_TIMEOUT` | LLM 응답 시간 초과 |

- 채팅·추천 에이전트는 스트림을 시작한 뒤의 실패를 JSON 대신 `error` 이벤트로 보낸다 (5.2, 4.8)
- 이 형식이 아닌 응답도 올 수 있다 (프록시의 502·504 HTML 등). `content-type`이 JSON이 아니면 상태 코드만으로 처리

### 1.5 페이지네이션
결과가 많은 목록(정책 검색)에만 쓴다.
- 요청 `?page=0&size=20` (`page`는 0부터)
- 응답 `{ "items": [...], "page": 0, "size": 20, "totalElements": 134, "totalPages": 7 }`

### 1.6 요청 제한
| 대상 | 기준 |
|---|---|
| `/api/*` 전체 | IP(`X-Forwarded-For` 첫 값)당 분당 300회 |
| 채팅, 대화 요약, 신청 가이드, 추천 에이전트 답변 | 위에 더해 IP당 분당 60회 + `X-Client-Id`당 분당 10회 |
| 서버 전체 LLM 호출 (채팅, 요약, 가이드, 추천 에이전트 답변) | 하루 1,000회 |

- 넘으면 `429 RATE_LIMITED` + `Retry-After`(초)
- 하루 상한은 `message`가 다르다: "오늘 이용량이 많아 채팅을 잠시 멈췄어요. 내일 다시 이용해 주세요." (`Retry-After`는 한국 자정까지). 대화 요약·신청 가이드·추천 에이전트도 같은 문구
- 추천 에이전트는 답변 하나에 LLM을 여러 번 부를 수 있지만 **요청 1번을 1회**로 셈. 패널 열기(`GET …/agent`)와 새로 시작(`DELETE`)은 LLM을 쓰지 않아 위 두 줄에 들지 않음
- 수치는 시연 전에 서버 설정으로 바뀔 수 있음 (형식은 그대로)

### 1.7 캐시와 보안 헤더
- 사용자마다 다른 응답(세션 API 전체(추천 에이전트 포함), 스크랩 목록, `sessionId`를 준 정책 검색)은 `Cache-Control: no-store`
- 모든 응답에 `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`

## 2. 코드 값

### 2.1 정책 카테고리 `Category`
| 코드 | 표시명 |
|---|---|
| `JOB` | 일자리 |
| `HOUSING` | 주거 |
| `EDUCATION` | 교육 |
| `WELFARE` | 복지·문화 |
| `PARTICIPATION` | 참여·권리 |

- 정책 데이터(온통청년)의 대분류와 1:1. 데이터에서 금융과 복지가 한 묶음("금융·복지·문화")이라 `FINANCE`는 없음
- 세션에는 카테고리가 없음. 카드 표시와 정책 검색 필터에만 씀

### 2.2 지역 `RegionCode`
- **법정 행정구역 코드 5자리 문자열**. 시군구 `"11680"`(서울 강남구), 시도 전체 `"11000"`(끝 3자리 `000`)
- 서울·광역시는 구, 도는 시·군 단위. 구가 있는 시(수원시)는 시 하나(`"41110"`)
- 쓸 수 있는 값은 `GET /api/regions`가 주는 코드뿐이다 (4.5). 프론트는 목록을 하드코딩하지 않음
- **필터 범위**: 고른 시군구 + 그 시도 전체 + 전국 정책. 같은 시도의 다른 시군구 전용 정책은 빠짐. "○○ 전체"를 고르면 그 시도의 모든 정책 + 전국
- 광주광역시와 전라남도는 정책 데이터에서 **전남광주통합특별시** 하나다 (코드 `"12000"`, 옛 광주광역시 자리). 옛 광주의 구는 "동구", "서구"처럼 보임

### 2.3 프로필 `Profile` (8항목 모두 필수)
| 필드 | 타입 | 값 |
|---|---|---|
| `region` | `RegionCode` | 2.2 |
| `maritalStatus` | enum | `SINGLE` 미혼, `MARRIED` 기혼 |
| `age` | integer | 만 나이 15~39 |
| `annualIncome` | integer | 연소득, **만 원** 단위, 0~1,000,000 |
| `education` | enum | `BELOW_HIGH_SCHOOL` 고졸 미만, `HIGH_SCHOOL_ENROLLED` 고교 재학, `HIGH_SCHOOL_GRADUATED` 고교 졸업, `COLLEGE_ENROLLED` 대학 재학, `COLLEGE_EXPECTED` 대졸 예정, `COLLEGE_GRADUATED` 대학 졸업, `GRADUATE_SCHOOL` 석·박사, `OTHER` 기타 |
| `major` | enum | `HUMANITIES` 인문, `SOCIAL` 사회, `BUSINESS` 상경, `NATURAL_SCIENCE` 이학, `ENGINEERING` 공학, `ARTS` 예체능, `AGRICULTURE` 농산업, `OTHER` 기타, `NONE` 해당 없음 |
| `employmentStatus` | enum | `EMPLOYED` 재직자, `SELF_EMPLOYED` 자영업자, `UNEMPLOYED` 미취업자, `FREELANCER` 프리랜서, `DAILY_WORKER` 일용근로자, `STARTUP` (예비)창업자, `SHORT_TERM_WORKER` 단기근로자, `FARMER` 영농종사자, `OTHER` 기타 |
| `specialField` | enum | `SME` 중소기업, `WOMEN` 여성, `BASIC_LIVELIHOOD` 기초생활수급자, `SINGLE_PARENT` 한부모가정, `DISABLED` 장애인, `FARMER` 농업인, `SOLDIER` 군인, `LOCAL_TALENT` 지역인재, `NONE` 해당 없음 |

- 서버도 검증한다. 빠진 값, 범위 밖 숫자, enum 밖 값, 모르는 지역 코드는 `400 VALIDATION_FAILED` + 필드별 `details`
- 값의 의미 (정책 추천에 영향)
  - `NONE`(해당 없음): 그 대상이 아니라는 뜻. 특정 전공·대상 전용 정책(예: "중소기업 재직자만")은 빠짐
  - `OTHER`(기타): 분류에 맞지 않는다는 뜻. 그 항목으로는 거르지 않음
- 프로필은 세션을 만든 뒤 바꿀 수 없다. 조건이 바뀌면 새 세션 (5.5)

## 3. 공통 타입

### 3.1 세션·메시지
```ts
type ISODate = string      // 'YYYY-MM-DD'
type ISODateTime = string  // '2026-10-17T18:00:00+09:00'

interface ChatSession {
  id: string               // UUID
  title: string            // 첫 질문 전 "새 대화". 첫 질문의 공백을 정리해 30자까지 (넘으면 "…")
  profile: Profile
  createdAt: ISODateTime
}

interface Message {
  id: string
  clientMessageId: string | null // 질문에만 있음. 프론트가 먼저 그린 말풍선과 저장된 이력을 맞출 때 씀
  role: 'user' | 'assistant'
  content: string          // 답변은 마크다운 (5.3)
  policies: PolicySummary[] | null // 질문은 null, 카드 없는 답변은 []
  createdAt: ISODateTime
}
```

### 3.2 정책 카드 `PolicySummary`
채팅 카드, 정책 검색, 추천, 스크랩이 모두 같은 형식이다.
```ts
type ApplicationStatus = 'OPEN' | 'ALWAYS_OPEN' | 'UPCOMING' | 'CLOSED' | 'UNKNOWN'

interface PolicySummary {
  id: string               // 문자열. 지금 데이터는 20자리 숫자지만 형식을 보장하지 않음
  title: string
  category: Category | null // 대분류가 여러 개면 첫 번째. 대분류 없는 정책은 null
  regionLabel: string | null // 표시 문구. 지역 정보가 없는 정책은 null
  applyStart: ISODate | null
  applyEnd: ISODate | null
  status: ApplicationStatus
  organization: string | null // 담당(주관) 기관
  sourceUrl: string | null // 관련 사이트
}
```
- `status`
  | 값 | 의미 | D-day |
  |---|---|---|
  | `OPEN` | 신청 가능 (기간 안) | 마감일까지 (`applyEnd`가 없으면 표시 안 함) |
  | `UPCOMING` | 신청 예정 | 시작일까지 |
  | `ALWAYS_OPEN` | 상시 모집 | 없음 |
  | `UNKNOWN` | 기간 정보 없음 | 없음 |
  | `CLOSED` | 마감 (기간이 남았어도 조기 마감 포함) | 없음 |
  - 날짜만으로는 상시·정보 없음·조기 마감을 구분할 수 없어 화면은 `status`로 판단
  - 채팅 기록·추천 에이전트의 카드도 **조회 시점** 기준으로 다시 계산함
- `regionLabel` 예: `"서울특별시 강남구"`(시군구 하나), `"서울특별시"`(시도 전체), `"경기도 5곳"`(한 시도의 여러 시군구), `"서울특별시 외 14개 시도"`, `"전국"`
- `sourceUrl`: 약 69%가 `null`(신청 주소가 없거나 주소 모양이 아님). 절반 이상이 기관 홈페이지 첫 화면이라 버튼 문구는 "관련 사이트"처럼 중립적으로

### 3.3 정책 상세 `PolicyDetail`
```ts
interface PolicyDetail extends PolicySummary {
  summary: string
  eligibility: string      // 서버가 만든 여러 줄 (아래)
  benefits: string
  howToApply: string       // 비어 있으면 안내 문구
  requiredDocuments: string // 목록이 아니라 문자열 하나 (원문이 자유 문장). 비어 있으면 안내 문구
}
```
- 문자열은 정책 원문이라 **줄바꿈(`\n`)이 있음** → `white-space: pre-line`
- `eligibility` 형식. 나이 줄은 항상 있고(조건이 없으면 "제한 없음"), 나머지는 해당 항목이 없으면 그 줄을 뺌
  ```
  - 나이: 만 19~39세
  - 소득: 연소득 5,000만 원 이하
  - 혼인: 미혼
  - 취업 상태: 미취업자, 프리랜서
  - 학력: …
  - 전공: …
  - 특화 대상: …
  추가 자격
  (원문)
  참여 제한 대상
  (원문)
  ```
- 비어 있는 칸(신청 방법 약 48%, 제출 서류 약 70%)은 서버가 확인할 곳을 안내하는 문구를 넣는다. **프론트는 빈 값 처리를 따로 하지 않아도 됨**
  | 상황 | 문구 |
  |---|---|
  | 담당 기관과 링크가 있음 | 공고에 나와 있지 않아요. 담당 기관(서울특별시)에 문의하거나 관련 사이트를 참고해 주세요. |
  | 담당 기관만 | 공고에 나와 있지 않아요. 담당 기관(서울특별시)에 문의해 주세요. |
  | 링크만 | 공고에 나와 있지 않아요. 관련 사이트를 참고하거나 정책을 운영하는 기관에 문의해 주세요. |
  | 둘 다 없음 | 공고에 나와 있지 않아요. 정책을 운영하는 기관에 문의해 주세요. |
  - 운영 기관이 따로 있으면 함께 씀: "담당 기관(고용노동부, 운영: 한국산업인력공단)"

### 3.4 스크랩·지역
```ts
interface Scrap {
  policy: PolicySummary
  createdAt: ISODateTime   // 처음 스크랩한 시각
}

interface SidoRegions {
  code: RegionCode         // 시도 전체 코드 ("11000")
  name: string             // "서울특별시"
  sigungu: { code: RegionCode; name: string }[] // 첫 항목은 "서울특별시 전체"
}
```

### 3.5 추천 에이전트 (v1.4)
```ts
interface AgentCard {
  policy: PolicySummary
  reason: string | null    // 추천 이유 한두 문장. 시작 상태의 카드는 null
}

interface AgentMessage {
  id: string
  role: 'user' | 'assistant'
  content: string          // 사용자: 보낸 글자 그대로. 에이전트: 안내 문장 (마크다운 아님, 줄바꿈만)
  question: string | null  // 에이전트의 다음 질문 (말풍선을 따로 그림). 사용자 메시지·질문 없는 답변은 null
  options: string[]        // 질문의 선택지 칩 (0~5개). "직접 입력"은 들어 있지 않음. question이 null이면 항상 []
  createdAt: ISODateTime | null // 시작 메시지는 null (저장하지 않음)
}

interface AgentState {
  messages: AgentMessage[] // 오래된 순. 맨 앞은 항상 시작 메시지 1개
  cards: AgentCard[]       // 지금 우측에 보일 카드 (0~5개)
}
```

## 4. REST 엔드포인트

| 엔드포인트 | 설명 | 구현 |
|---|---|---|
| `POST /api/sessions` | 세션 만들기 | 됨 |
| `GET /api/sessions` | 내 세션 목록 | 됨 |
| `GET /api/sessions/{sessionId}` | 세션 하나 | 됨 |
| `DELETE /api/sessions/{sessionId}` | 세션 삭제 (v1.2) | 됨 |
| `GET /api/sessions/{sessionId}/messages` | 메시지 이력 | 됨 |
| `POST /api/sessions/{sessionId}/chat` | 채팅 (SSE, 5절) | 됨 |
| `GET /api/sessions/{sessionId}/recommendations` | 추천 정책 | 됨 |
| `GET /api/sessions/{sessionId}/agent` | 추천 에이전트 열기 (v1.4) | 됨 |
| `POST /api/sessions/{sessionId}/agent/messages` | 추천 에이전트에 답하기 (SSE, 4.8) | 됨 |
| `DELETE /api/sessions/{sessionId}/agent` | 추천 에이전트 새로 시작 (v1.4) | 됨 |
| `PUT /api/sessions/{sessionId}/current-policy` | 채팅에서 이야기할 정책 지정 (v1.4) | 됨 |
| `POST /api/sessions/{sessionId}/summary` | 대화 요약 | 됨 |
| `POST /api/sessions/{sessionId}/guides` | 신청 가이드 PDF 만들기 | 됨 (v1.3) |
| `GET /api/guides/{token}.pdf` | 만든 PDF 받기 (`pdfUrl`) | 됨 (v1.3) |
| `GET /api/policies` | 정책 검색 (`sessionId`를 주면 채팅방 안 검색) | 됨 |
| `GET /api/policies/{policyId}` | 정책 상세 | 됨 |
| `GET /api/regions` | 지역 목록 | 됨 |
| `GET /api/scraps`, `POST /api/scraps`, `DELETE /api/scraps/{policyId}` | 스크랩 | 됨 |

세션 아래 API는 모두 세션 주인만 쓸 수 있다. 아니면 `404 SESSION_NOT_FOUND`.

### 4.1 세션

**`POST /api/sessions`** → `201 ChatSession`
```json
{
  "profile": {
    "region": "11680", "maritalStatus": "SINGLE", "age": 27, "annualIncome": 3000,
    "education": "COLLEGE_GRADUATED", "major": "ENGINEERING",
    "employmentStatus": "UNEMPLOYED", "specialField": "NONE"
  }
}
```
- 관심 분야(`category`)는 없다. 질문마다 분야를 판단하므로 세션에서 고르지 않음
- 응답 `title`은 `"새 대화"`. 첫 질문을 보내면 서버가 그 질문으로 바꿈
- 응답 `profile`은 요청과 같은 형식

**`GET /api/sessions`** → `200 ChatSession[]`
- 만든 시각 내림차순. 페이지네이션 없음

**`GET /api/sessions/{sessionId}`** → `200 ChatSession`
- 채팅방 화면의 프로필 표시도 이 응답(또는 목록)의 `profile`을 씀. 프로필을 바꾸는 API는 없음

**`DELETE /api/sessions/{sessionId}`** → `204` (v1.2)
- 본문 없음. 세션과 그 메시지·카드·현재 정책·추천 에이전트 대화(4.8)가 **모두 지워지고 되돌릴 수 없음**. 스크랩은 사용자 단위라 남음
- 없거나 이미 지운 세션, 다른 사용자의 세션은 `404 SESSION_NOT_FOUND`
- 답변 생성 중에 지우면 그 스트림(채팅·추천 에이전트)은 `error` 이벤트(`INTERNAL_ERROR`)로 끝날 수 있음

**`GET /api/sessions/{sessionId}/messages`** → `200 Message[]`
- 오래된 순. 답변에는 그때의 카드(`policies`)가 함께 옴 (상태는 지금 기준, 3.2)
- 연결이 끊기거나 생성이 실패한 질문은 답변 없이 남음

### 4.2 추천 정책 `GET /api/sessions/{sessionId}/recommendations` → `200 PolicySummary[]`
- 세션 **프로필로 거른 뒤**(채팅과 같은 지역·나이·소득·대상 조건) **최근 대화의 분야**로 좁힌 정책
  - 분야: 최근 답변 3개의 분야 중 가장 많이 나온 것 (같으면 더 최근 것). 대화 전이거나 분야가 없으면 프로필 기준만
- 마감 정책은 빼고 **최대 10개**. 순서는 신청 가능한 정책 먼저: 진행 중(마감 임박 순) → 상시 → 예정(시작 임박 순) → 정보 없음
- 분야 정책이 적으면 적은 대로 (다른 분야로 채우지 않음). 없으면 `[]`
- LLM을 쓰지 않음 (약 0.4초)

### 4.3 대화 요약 `POST /api/sessions/{sessionId}/summary` → `200 { "summary": string }`
- 요청 본문 없음
- 최근 10턴을 2~3문장(150자 안팎, "~습니다"체)으로 요약. 대화에 나온 내용만 씀. 줄바꿈이 있을 수 있음
- **저장하지 않고** 요청마다 새로 만듦. 보통 1~2초
- 대화가 없으면 LLM을 부르지 않고 `"아직 나눈 대화가 없습니다."`
- 오류: 요청 제한·하루 상한 `429 RATE_LIMITED`(채팅과 같은 기준, 1.6), LLM 실패 `502 RAG_UPSTREAM_ERROR`, 30초 초과 `504 RAG_TIMEOUT`

### 4.4 정책

**`GET /api/policies`** → `200 Page<PolicySummary>`
| 쿼리 | 설명 |
|---|---|
| `keyword` | 앞뒤 공백을 지운 뒤 50자 이하. 띄어쓰기로 나눈 낱말이 **모두** 정책 이름·설명·키워드 중 한 곳에 있어야 걸림 (대소문자 무시) |
| `category` | 2.1의 5개. 대분류가 여러 개인 정책은 하나라도 맞으면 걸림 (카드의 `category`는 첫 번째라 다를 수 있음) |
| `region` | 지역 목록의 코드. 범위는 2.2. `sessionId`와 함께 쓸 수 없음 |
| `sessionId` | 채팅방 안 검색 (v1.1). 아래 참고 |
| `page`, `size` | `page` 0~10,000 (기본 0), `size` 1~50 (기본 20) |

- 모두 선택. 빈 값(`keyword=`)은 조건 없음. 어기면 `400 VALIDATION_FAILED` (`details[].field`는 쿼리 이름)
- **`sessionId`를 주면** (채팅방 화면 상단 검색)
  - 그 세션의 **프로필로 1차 필터**를 건다. 채팅 답변·추천과 같은 조건(지역·나이·취업·학력·전공·특화·혼인·소득)이고, 확실히 안 맞는 정책만 뺀다
  - `keyword`·`category`는 함께 쓸 수 있다. `region`을 함께 보내면 `400 VALIDATION_FAILED`(`field: "region"`). 빈 `region=`은 조건 없음이라 괜찮음
  - 정렬·마감 포함·페이지는 아래 정책 검색과 같다. 추천(4.2)은 당장 신청할 수 있는 정책이 먼저라 순서가 다르다 (상시가 예정보다 앞, 마감 제외)
  - 세션 주인이 아니거나, 없는 세션이거나, UUID 형식이 아니면 `404 SESSION_NOT_FOUND`
  - 응답에 `Cache-Control: no-store`
  - `sessionId` 없는 검색은 지금 그대로 (채팅방과 상관없는 정책 목록 화면)
- 정렬: **진행 중 → 예정 → 상시 → 정보 없음 → 마감**
  - 진행 중은 마감 임박 순(마감일 없으면 뒤), 예정은 시작 임박 순, 마감은 최근 마감 순. 같은 그룹·같은 날짜는 최신 정책 먼저
- 약 0.4초 (`sessionId` 검색은 0.4~0.5초)

**`GET /api/policies/{policyId}`** → `200 PolicyDetail`
- 없는 정책 `404 POLICY_NOT_FOUND`

**비활성 정책** (온통청년에서 내려간 정책)
- 정책 검색·추천·채팅 답변·스크랩 목록에서 빠지고, 상세와 스크랩 추가는 `404 POLICY_NOT_FOUND`
- **채팅 기록의 카드는 그대로 남음** (본문 번호와 맞추려고). 그 카드를 누르면 상세가 404이므로 "정책을 찾을 수 없음" 화면이 필요함
- 추천 에이전트의 카드(4.8)는 "지금 맞는 정책"이라 `GET …/agent`에서 **빠짐** (번호가 없어 맞출 본문이 없음). 그래서 카드가 5개보다 적을 수 있음

### 4.5 지역 목록 `GET /api/regions` → `200 SidoRegions[]`
```json
[
  { "code": "11000", "name": "서울특별시",
    "sigungu": [ { "code": "11000", "name": "서울특별시 전체" }, { "code": "11110", "name": "종로구" } ] },
  { "code": "41000", "name": "경기도",
    "sigungu": [ { "code": "41000", "name": "경기도 전체" }, { "code": "41110", "name": "수원시" } ] }
]
```
- 시도를 고르면 그 시도의 `sigungu`를 보여 줌. 첫 항목은 "○○ 전체"
- 순서는 서버가 정함 (서울 → 경기 → 인천 → 행정 표준 순서). **받은 순서대로** 그림
- 세종은 "세종특별자치시 전체" 하나
- 이 목록의 코드는 프로필 `region`과 검색 `region`에 모두 쓸 수 있음

### 4.6 스크랩
| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| `GET` | `/api/scraps` | — | `200 Scrap[]` |
| `POST` | `/api/scraps` | `{ "policyId": "..." }` | 새로 만들면 `201 Scrap`, 이미 있으면 `200`으로 처음 값 그대로 |
| `DELETE` | `/api/scraps/{policyId}` | — | `204`. 없어도 `204` |

- `X-Client-Id`별로 저장. 다른 사용자의 스크랩은 보이지 않고 지울 수도 없음
- 목록 정렬은 정책 검색과 같음 (**진행 중 → 예정 → 상시 → 정보 없음 → 마감**). 같은 그룹·같은 날짜는 최근에 스크랩한 것 먼저
- 추가: `policyId`가 없거나 비었거나 30자를 넘으면 `400 VALIDATION_FAILED`, 없는 정책·비활성 정책은 `404 POLICY_NOT_FOUND`
- 비활성 정책의 스크랩은 지우지 않고 목록에서만 뺌 (다시 활성이 되면 돌아옴)
- 스크랩 개수 상한 없음

### 4.7 신청 가이드 PDF `POST /api/sessions/{sessionId}/guides` (v1.3)
```json
// 요청
{ "policyId": "20251031005400211234" }
// 응답 201
{ "policyId": "20251031005400211234",
  "pdfUrl": "/api/guides/Xk3…Qz.pdf",
  "expiresAt": "2026-11-14T18:10:00+09:00",
  "outsideProfile": false }
```
- 동기 처리 (응답이 올 때 PDF가 만들어져 있음). LLM을 한 번 불러 보통 5~10초 (11-14 실측 4~10초, 서버 시간 제한 45초)
- `pdfUrl`은 **같은 출처 경로**. Traefik의 `/api` 경로를 그대로 거치므로 프론트 주소 기준으로 열면 됨 (6.1)
- `pdfUrl`은 **10분** 뒤 만료. 만료되면 같은 요청으로 다시 받음 (PDF에 프로필·대화 내용이 들어가서). 저장하지 않으므로 다시 만들 때마다 LLM을 부름
- 오류: 없는 세션 `404 SESSION_NOT_FOUND`, 없는 정책·비활성 정책 `404 POLICY_NOT_FOUND`, `policyId` 형식 오류 `400 VALIDATION_FAILED`, 요청 제한·하루 상한 `429 RATE_LIMITED` (채팅과 같은 기준, 1.6), LLM 실패 `502 RAG_UPSTREAM_ERROR`, 시간 초과 `504 RAG_TIMEOUT`, 그 밖의 생성 실패 `500 INTERNAL_ERROR`. 어느 경우든 `message`를 그대로 보여 주면 됨

**PDF 받기 `GET /api/guides/{token}.pdf`** → `200 application/pdf`
- 브라우저가 링크로 바로 여는 주소라 `X-Client-Id`가 **필요 없음**. 추측할 수 없는 토큰이 열쇠 역할
- `Content-Disposition: inline` (브라우저 PDF 뷰어로 열림, 파일 이름은 "정책 이름 신청 가이드.pdf"). `Cache-Control: no-store`. PDF 응답만 CSP가 `frame-ancestors 'none'` (1.7의 공통 CSP는 브라우저 PDF 뷰어를 막을 수 있어서)
- 만료되었거나 없는 토큰은 `404 GUIDE_NOT_FOUND` ("가이드 링크가 만료되었어요. 가이드를 다시 만들어 주세요.")

**PDF 내용** (형식이 아니라 참고용. 바뀌어도 API는 그대로)
1. 한눈에 보기 (첫 쪽): 정책 이름, 기관, 신청 상태·기간, 관련 사이트, 할 일 단계 수·서류 수, 경고(프로필 밖 정책, 마감 임박·마감·예정, 선착순)
2. 신청 방법 단계 표 (할 일 아래 줄에 어디서)
3. 제출 서류 체크리스트
4. 확인할 조건: 공고의 자격 조건 목록 (자격 판정은 하지 않음)
5. 주의사항
6. 내 상황: 세션 프로필 한 줄 + 대화에서 사용자가 직접 말한 사항
7. 용어 설명 (공고에 나온 용어만)
8. 안내: 자동으로 만든 참고 자료이므로 공고 원문과 담당 기관에서 확인할 것, 정보 기준일, 만든 시각
9. 부록: 제출 서류 공고 원문
- 날짜·금액·링크·기관명은 공고 데이터에서 그대로 넣음. 공고에 없는 항목은 "공고에 나와 있지 않음"과 문의처
- 세션 프로필의 1차 필터(채팅 추천과 같은 기준) 밖의 정책(검색·스크랩으로 연 다른 지역 정책 등)도 만들어 주되, 응답 `outsideProfile`이 `true`이고 PDF 첫 쪽 경고에 "프로필 조건으로 찾은 추천 정책이 아님"을 적음. 어느 조건이 안 맞는지는 알려 주지 않음 (자격 판정을 하지 않아서)

### 4.8 추천 에이전트 (v1.4)
채팅방 우측 패널. 에이전트가 먼저 묻고, 사용자가 답할 때마다 질문과 우측 카드가 바뀐다. 프로필은 그 채팅방의 프로필이고 다시 묻지 않는다. 채팅 대화와는 **따로** 저장되며 서로의 기록에 섞이지 않는다.

**`GET /api/sessions/{sessionId}/agent`** → `200 AgentState`
- 처음 열면 시작 상태. LLM을 쓰지 않음 (약 0.5초)
  ```json
  {
    "messages": [
      { "id": "start", "role": "assistant",
        "content": "안녕하세요! 이 대화의 프로필(경기도 · 만 16세 · 자영업자)에 맞는 정책을 찾아 드릴게요.\n몇 가지만 여쭤볼게요.",
        "question": "어떤 도움이 필요하세요?", "options": ["일자리", "주거", "교육", "복지·문화", "참여·권리"], "createdAt": null }
    ],
    "cards": [ { "policy": { "id": "…", "title": "청년 월세 한시 특별지원", "…": "…" }, "reason": null } ]
  }
  ```
  - 시작 메시지는 저장하지 않고 **항상 맨 앞**에 붙음 (id `"start"`). 다른 에이전트 답변과 같은 모양이라 `content` 말풍선 + `question` 말풍선 + 칩으로 그리면 됨
  - 시작 카드는 **프로필 기준만**으로 최대 5개: 추천 정책(4.2)과 같은 필터·순서(마감 제외, 신청 가능한 것 먼저)이되, 4.2의 "최근 채팅 분야로 좁히기"는 하지 않음 (첫 질문이 분야를 묻기 때문)
  - 분야를 고르기 전부터 보여 주는 이유: 무엇을 물을지 모르는 사용자에게 실제 정책 몇 개가 분야를 고르는 실마리가 됨. 시작 카드는 `reason`이 `null`이라 에이전트가 고른 카드와 구분됨
- 대화한 적이 있으면 시작 메시지 + 저장된 메시지. `cards`는 **마지막으로 보낸 카드 목록** (대화 뒤 한 번도 바뀌지 않았으면 시작 카드)

**`POST /api/sessions/{sessionId}/agent/messages`** → `200 text/event-stream`
```json
{ "content": "주거" }
```
- `content`: 앞뒤 공백을 지운 뒤 **1~1,000자** (채팅과 같음). 선택지 칩을 누르면 칩 글자 그대로 보냄
- 스트림 시작 전 오류는 JSON (1.4): 검증 실패 `400`, 없는 세션 `404`, 진행 중 `409 STREAM_IN_PROGRESS`, 요청 제한 `429`
- 응답 헤더·`:ping`은 채팅과 같음 (5.2, 5.6)

| event | data | 횟수 | 설명 |
|---|---|---|---|
| `progress` | `{ "text": "주거 정책 찾는 중…" }` | 0회 이상 | 에이전트가 정책을 찾을 때마다. 화면에 진행 문구로 표시 |
| `cards` | `AgentCard[]` | 0~1회 | 바뀐 카드 목록 **전체** (1~5개). 바뀌지 않았으면 보내지 않음 (빈 목록으로 지우는 일은 없음) |
| `message` | `AgentMessage` | 1회 | 에이전트 답변 (저장된 것) |
| `done` | `{ "messageId": "..." }` | 1회 | 정상 종료. `message`의 `id`와 같음 |
| `error` | `{ "code": "...", "message": "..." }` | 0~1회 | 생성 중 실패. 보낸 뒤 연결을 닫음 (`done` 없음). `code`는 `RAG_UPSTREAM_ERROR`, `RAG_TIMEOUT`, `INTERNAL_ERROR` |

- 순서: `progress`들 → `cards`(있으면) → `message` → `done`, 또는 `progress` 사이에 `error`
- 글자 단위로 흘려보내지 않음 (답변이 질문·선택지·카드가 묶인 하나의 결과라서). 보통 **3~6초**(첫 `progress`까지 1초 안팎), 정책을 여러 번 찾으면 10초 안팎. 30초를 넘으면 `error`(`RAG_TIMEOUT`)
- **저장**: 답변이 다 만들어지면 사용자 메시지·에이전트 답변·카드를 **함께** 저장한 뒤 `cards`·`message`를 보냄. 그 전에 실패하면(`error`) 아무것도 저장하지 않음 → 같은 `content`로 다시 보내면 됨 (그래서 채팅의 `clientMessageId`가 없음)
  - 연결이 끊겨도 서버는 생성을 멈추고 저장하지 않음. 다만 저장 직후에 끊기면 저장된 채로 남을 수 있어, `done`을 못 받았으면 `GET …/agent`로 확인한 뒤 다시 보냄
  - 사용자 메시지는 응답에 오지 않음 (`GET`에는 있음)
- **동시 요청**: 같은 세션에서 에이전트 답변을 만드는 중이면 `409 STREAM_IN_PROGRESS`. 채팅(5.4)과는 따로 세서 채팅 답변 중에도 에이전트는 쓸 수 있음
- **카드**: 세션 프로필의 1차 필터(채팅과 같은 기준)를 통과한 정책만, **최대 5개**. 마감 정책은 사용자가 원할 때만 나옴. 순서는 에이전트가 정한 추천 순 (번호 배지 없음)
  - 5개로 묶는 이유: 좁혀 주는 것이 에이전트의 역할이고, 카드마다 추천 이유를 써서 개수가 늘면 답변이 느려짐
  - 더 보고 싶으면 "더 보여줘"처럼 말하면 에이전트가 다른 정책으로 바꿔 보여 줌 (상한은 그대로). 목록 전체는 채팅방 안 정책 검색(4.4)으로
- **답변 내용**: 질문은 한 번에 하나. 프로필 항목은 다시 묻지 않음. 프로필과 다른 지역·나이를 말하면 이 대화는 채팅방 프로필 기준이라고 안내하고 새 채팅방을 권함 (일반 `message`). 정책 정보는 찾은 정책의 공고 내용에서만 씀
- 대화 길이 상한 없음 (사용자가 닫거나 새로 시작할 때까지). 오래된 대화는 저장만 되고 에이전트는 최근 대화 위주로 봄

**`DELETE /api/sessions/{sessionId}/agent`** → `204`
- 추천 에이전트 대화와 카드를 모두 지움 (채팅 대화·현재 정책은 그대로). 대화가 없어도 `204`
- 에이전트 답변을 만드는 중이면 `409 STREAM_IN_PROGRESS`

### 4.9 채팅에서 이야기할 정책 지정 `PUT /api/sessions/{sessionId}/current-policy` → `204` (v1.4)
```json
{ "policyId": "20251031005400211234" }
```
- 이 채팅방의 **현재 정책**(채팅이 "이 정책", "신청 기간은?"처럼 이름 없는 질문을 이어서 이해하는 기준)을 이 정책 하나로 바꿈. 메시지를 만들지 않고 LLM도 쓰지 않음
- 다음 채팅 질문부터 반영 (5.3 "현재 정책"). 채팅 기록에는 아무것도 남지 않음
- 프로필 1차 필터 밖의 정책(검색·스크랩으로 연 다른 지역 정책 등)도 지정할 수 있음
- 오류: `policyId` 형식 오류 `400 VALIDATION_FAILED`(가이드와 같은 기준), 없는 정책·비활성 정책 `404 POLICY_NOT_FOUND`, 없는 세션 `404 SESSION_NOT_FOUND`
- 채팅 답변을 만드는 중에 불러도 됨 (카드가 답변보다 먼저 와 답변 중에도 상세를 열 수 있음). 진행 중인 답변에는 반영되지 않지만, 그 답변이 끝나도 지정한 정책이 남아 다음 질문의 기준이 됨. 그래서 그 답변의 `done`에서 "이야기 중" 표시를 지우지 않음

## 5. 채팅 스트리밍 `POST /api/sessions/{sessionId}/chat`

### 5.1 요청
```
Content-Type: application/json
Accept: text/event-stream
X-Client-Id: <uuid>

{ "clientMessageId": "6f1c…-uuid", "content": "서울 강남구 사는데 월세 지원 정책 알려줘" }
```
- `clientMessageId`: 메시지마다 프론트가 만드는 UUID. 다시 보낼 때는 같은 값 (5.4)
- `content`: 앞뒤 공백을 지운 뒤 **1~1,000자**. 공백만 있으면 `400 VALIDATION_FAILED`. 저장·비교도 지운 값으로 함
- 스트림을 시작하기 전의 오류(세션 없음, 검증 실패, 충돌, 요청 제한)는 SSE가 아니라 1.4의 JSON 오류

### 5.2 응답
```
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
X-Accel-Buffering: no
```

| event | data | 횟수 | 설명 |
|---|---|---|---|
| `policies` | `PolicySummary[]` | 0~1회 | 카드. **첫 `token` 바로 앞**에 옴. 카드 없는 답변(인사, 조건 되묻기 등)은 보내지 않음 |
| `token` | JSON 문자열 (예: `"서울시 "`) | 0회 이상 | 답변 조각. 줄바꿈이 들어 있을 수 있음 |
| `done` | `{ "messageId": "..." }` | 1회 | 정상 종료. `messageId`는 저장된 답변 id (`GET …/messages`의 `id`) |
| `error` | `{ "code": "...", "message": "..." }` | 0~1회 | 생성 중 실패. 보낸 뒤 연결을 닫음 (`done` 없음). `code`는 `RAG_UPSTREAM_ERROR`, `RAG_TIMEOUT`, `INTERNAL_ERROR`. `message`는 화면에 그대로 보여 줄 수 있음 |
| (주석) | `:ping` | 15초마다 | 연결 유지용. 무시 |

- 순서: `policies`(있으면) → `token`들 → `done` 또는 `error`
- 모든 `data`는 한 줄 JSON. 이벤트 사이에 빈 줄 하나
- 질문을 보내고 첫 `token`까지 보통 4~8초 (질문 분석, 검색, 답변 첫 줄). 카드는 그 직전에 뜸

예시
```
event: policies
data: [{"id":"20251031005400211234","title":"청년 월세 지원","category":"HOUSING","regionLabel":"서울특별시","applyStart":"2026-09-01","applyEnd":"2026-11-30","status":"OPEN","organization":"서울특별시","sourceUrl":null}]

event: token
data: "조건에 맞는 정책을 찾았어요.\n"

:ping

event: token
data: "1. 청년 월세 지원 …"

event: done
data: {"messageId":"1234"}

```

### 5.3 답변 본문과 카드
- 본문은 마크다운이다. **CommonMark 범위만 씀**: 문단, 줄바꿈, 굵게, 번호·글머리 목록, 링크. 표·취소선 같은 확장 문법은 쓰지 않음 (정책 비교도 목록으로 씀)
- 카드는 형식상 **최대 10개**이고 지금은 **최대 6개**, 보통 3개 안팎. "5개 알려줘"처럼 개수를 말해도 개수를 맞추지 않음 (개수 지정은 하지 않기로 함, v1.4). 프론트는 10개까지 그릴 수 있으면 됨
- `policies` 순서 = 본문이 정책을 소개한 순서. **번호 배지 = 배열 위치 + 1** 이고 본문의 "1. 2. 3."과 같다
- 번호는 **답변마다 1부터** 센다. "다른 것도 알려줘"처럼 이어 물어 받은 정책도 그 답변의 카드로 1부터 다시 셈
- 사용자가 "두 번째 거 신청 방법은?"처럼 번호로 물으면 **직전 답변의 카드 번호**로 이해함
- **현재 정책**: 서버는 채팅방마다 "지금 이야기 중인 정책"(최대 3개)을 기억해 "신청 기간은?"처럼 이름 없는 질문을 그 정책으로 이해함. 답변에서 정책을 가리키면 그 정책으로 바뀌고, 주제를 바꾸면 비워짐. 정책 상세의 "채팅에서 물어보기"(4.9)로도 지정됨 (v1.4)
- 카드 순서는 질문 관련도 기준이라 정책 검색·스크랩 정렬과 다름
- **지역 정책만** ("인천 청년 정책 뭐 있어?", "인천 거만 알려줘"): 프로필 지역의 정책만 원하는 추천 질문이면 전국 정책을 빼고 그 지역 정책만 카드로 보여 줌. 분야를 말했거나("인천 주거 지원 알려줘") 사는 곳을 말한 것이면("인천 사는데 받을 수 있는 거") 지금처럼 전국 정책도 함께 (v1.4 보충)
- **정책 비교** ("A랑 B 차이가 뭐야?", "1번이랑 2번 중에 뭐가 나아?"): 한 번에 **두 정책**까지. 카드는 비교한 정책만 최대 2개 (카드 번호로 가리킨 정책이 먼저, 그다음 이름으로 말한 순서). 한쪽을 찾지 못하면 찾은 정책 카드 1개와 함께 본문에서 알림
  - 셋 이상을 비교해 달라고 하면 비교하지 않고 두 개를 골라 달라고 되물음 (일반 `token` 답변). 모두 카드 번호로 가리켰으면 그 정책들을 카드로 다시 보여 주고 "예: '1번이랑 2번 비교해 줘'"를 붙임

### 5.4 다시 보내기 (멱등 처리)
같은 세션에서 같은 `clientMessageId`로 다시 보내면:
| 서버 상태 | 응답 |
|---|---|
| 처음 보는 키 | 질문 저장 → 답변 생성 |
| 질문만 있음 (이전에 끊김·실패) | 질문은 다시 저장하지 않고 답변만 생성 |
| 답변까지 있음 (`done`을 못 받은 경우) | 새로 만들지 않고 저장된 답변을 일반 답변과 같은 순서로: `policies`(있으면) → `token` 1개(전체 답변) → 기존 `messageId`의 `done` |
| 키는 같은데 `content`가 다름 | `409 IDEMPOTENCY_CONFLICT` (JSON) |
| 같은 세션에서 다른 답변을 만드는 중 (키가 달라도) | `409 STREAM_IN_PROGRESS` (JSON) |

- 키는 세션 단위로 유일함 (`sessionId` + `clientMessageId`)
- 저장된 답변을 다시 보내는 것은 하루 LLM 상한에 세지 않음

### 5.5 끊김과 조건 되묻기
**끊김**
- `done`이나 `error` 없이 연결이 닫히면 프론트는 실패로 처리하고 다시 보내기 버튼을 보여 줌
- 프론트가 연결을 끊으면(세션 이동, 페이지 이탈) 서버도 생성을 멈추고 미완성 답변은 저장하지 않음. 질문은 남음 → 끊은 뒤 `GET …/messages`로 이력을 다시 받음

**조건 되묻기** (일반 `token` 답변, 카드 없음, 프론트 변경 없음)
- 질문의 지역·나이가 프로필과 다르면 정책을 찾지 않고 되물음 (지역은 2.2 필터 범위 밖이면 다른 지역)
  ```
  질문에 말씀하신 지역(인천)이 프로필 지역(서울특별시 강남구)과 달라요.
  - 프로필 기준으로 찾으려면 지역을 빼고 다시 물어봐 주세요.
  - 말씀하신 조건으로 찾으려면 프로필을 그 조건으로 정한 새 채팅방에서 물어봐 주세요.
  ```
- 취업 상태·학력이 다르면 되묻지 않고 프로필 기준으로 찾은 뒤 답변에서 알림. 어느 지역인지 모르는 지명("판교")도 같음

### 5.6 연결 유지
- 서버는 15초마다 `:ping`, 응답 헤더 `Cache-Control: no-cache`, `X-Accel-Buffering: no`
- 배포 경로(프록시·로드 밸런서)의 유휴 타임아웃은 120초 이상

## 6. 배포

### 6.1 경로
- k3s에 배포하고 Traefik이 `/api`는 FastAPI 서버로, 나머지는 프론트 nginx로 보냄. 브라우저는 한 주소만 부르는 **같은 출처**라 CORS 설정은 없음
- 시연은 HTTP로 함 (HTTPS·HSTS 없음)
- 개발 때는 로컬 백엔드에 Vite 프록시(`DEV_API_TARGET`)로 붙임

### 6.2 출처 확인 (나중에 켤 수 있음)
- 프록시(Traefik)가 `/api` 요청에 비밀 헤더 `X-Origin-Secret`을 붙이면, 서버로 직접 오는 요청을 `403 FORBIDDEN`으로 막을 수 있음 (서버 설정 `ORIGIN_SECRET`, 비어 있으면 검사하지 않음)
- 시연용 최소 보안 범위라 지금은 끔

### 6.3 배포 경로 SSE 확인 (끝남)
- 11-06 로컬 프록시에서 뼈대(15초 대기, 1초 간격 90개)와 실제 답변을 프론트가 확인, 11-10 배포 서버에서 실제 답변(첫 글자 최대 13초 포함)이 끊기지 않음을 확인
- 확인용 채팅 뼈대(`CHAT_SKELETON_ENABLED`)는 v1.4에서 지움
- 프록시 유휴 타임아웃 조건은 그대로 (5.6)

## 7. 과도기 호환 (v1.4에서 삭제)
프론트가 v1.0 형식으로 바뀌어(프론트 `origin/main` 11-15 확인) v0.3 값을 함께 받던 처리를 지웠다. 아래 값은 이제 `400 VALIDATION_FAILED`다.

| 항목 | v1.4부터 |
|---|---|
| 프로필 `region` | 시도 enum(`SEOUL` 등) → `400`. 5자리 지역 코드만 (2.2) |
| 정책 검색 `region` | 시도 enum → `400` |
| 정책 검색 `category` | `FINANCE` → `400`. 5개만 (2.1) |
| 카드 `region` | 보내지 않음. `regionLabel`만 (3.2) |
| 세션 생성 `category` | 모르는 필드라 지금처럼 무시 (4.1) |

## 8. 남은 일
| 항목 | 상태 |
|---|---|
| v1.4 운영 배포 | 아직 (13주차). 운영 DB에 에이전트 테이블을 먼저 적용한 뒤 서버 배포. 그 전까지 운영 서버는 v1.3처럼 동작함 |
| 추천 에이전트 | 열기·답하기·새로 시작 구현됨 (3.5, 4.8) |
| 채팅에서 이야기할 정책 지정 | 구현됨 (4.9, v1.4) |
| 신청 가이드 PDF | 구현됨 (4.7, v1.3). 배포 경로에서 PDF 열림 확인 필요 |
| 복합 질문 답변 | 하위 질문별로 나눠 검색하도록 보완 중 (13주차). SSE·카드 형식(5.2, 5.3)은 바뀌지 않음. 정책 비교는 구현됨 (5.3). 개수 지정은 하지 않기로 함 (v1.4) |

## 변경 이력
- v0.1~v0.4 (2026-10-25~26): 프론트 초안 v0.3에 대한 백엔드 검토. 시군구 지역과 지역 목록, 세션 분야 삭제, 조건 되묻기, 카드 `status`·`regionLabel`·`organization`, 상세 문구, 카드 번호, `policies`를 본문 앞에 보내기 제안. 오류 코드 추가
- v0.5~v0.7 (2026-10-29~11-01): 잠정 계약 본문. 카테고리 5개, 프로필 코드 대응, 과도기 호환, 공개 API 전체 구현 (검색·상세·지역·스크랩·추천·요약)
- **v1.0 (2026-11-04)**: 프론트 초안 없이 읽히는 단일 계약으로 정리. 구현 현황 문서(`front-sync.md`)를 합침. 새로 정함: 카드 최대 10개와 답변마다 1부터 번호, 답변 마크다운은 CommonMark 범위(표 없음), 가이드 PDF 형식 확정(v0.3 형식, 동기), 과도기 호환은 프론트 반영 뒤 삭제, 배포 경로 SSE 확인은 뼈대 → 실제 RAG 순서로 하고 둘 다 통과하면 뼈대 삭제
- **v1.1 (2026-11-09)**: 정책 검색에 선택 쿼리 `sessionId` 추가 (채팅방 안 검색). 세션 프로필로 1차 필터, `keyword`·`category`만 함께 쓰고 `region`은 400. 정렬·페이지는 정책 검색 그대로
  - 보충 (11-09, 형식 변경 없음): 정책 비교 동작 (5.3). 두 정책까지, 카드는 비교한 정책만, 셋 이상은 되물음
- **v1.2 (2026-11-13)**: 세션 삭제 `DELETE /api/sessions/{sessionId}` 추가 (완전 삭제, `204`, 없으면 `404`). 채팅방 프로필 표시는 기존 세션 응답의 `profile`을 쓴다고 적음
- **v1.3 (2026-11-14)**: 신청 가이드 PDF 세부 확정. `pdfUrl`은 같은 출처 경로(`/api/guides/{token}.pdf`)로 10분 만료, PDF 받기는 `X-Client-Id` 없이 열림, 만료 링크 `404 GUIDE_NOT_FOUND`, 요청 제한·하루 상한은 채팅과 같은 기준, PDF 내용 목차. 응답에 `outsideProfile`(프로필 1차 필터 밖 정책)
- **v1.4 (2026-11-22)**: 추천 에이전트 추가 (3.5, 4.8: 열기·답하기 SSE·새로 시작, 채팅과 따로 저장, 요청 제한은 채팅과 같은 기준). 채팅에서 이야기할 정책 지정 `PUT …/current-policy` 추가 (4.9, 5.3 현재 정책). 과도기 호환 삭제 (7). 채팅 뼈대 삭제, 배포 설명을 k3s + Traefik으로 정정 (6). 채팅 개수 지정("5개 알려줘")은 하지 않기로 하고 지금 동작(최대 6개)을 적음 (5.3). 맨 앞의 "프론트에서 확인할 것"을 v1.4 변경분으로 다시 씀 (0)
  - 보충 (11-23, 형식 변경 없음): 에이전트 질문 없는 턴에도 직접 입력을 보이게 (0). 채팅 "지역 정책만" 동작 (5.3). 남은 일에 운영 배포 상태와 복합·범용 질문 방향 (8)

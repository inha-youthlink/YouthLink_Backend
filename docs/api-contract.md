# YouthLink 공개 API 계약

- 버전: **v1.1** (2026-11-09)
- 범위: 브라우저(React) ↔ 백엔드(FastAPI) 공개 API `/api/*`. 평가 서버용 `/internal/*`은 다루지 않음
- 이 문서 하나가 기준이다. 프론트 초안 v0.3(`YouthLink_Front/spec/api-contract.md`)을 함께 읽지 않아도 되게 모든 규칙을 적었다. 필드·타입은 서버의 OpenAPI 명세(`/docs`)로도 확인할 수 있다
- 표시: **(나중)** = 형식은 확정, 구현은 아직

## 0. 프론트가 바꿀 것 (v0.3 → v1.1)

프론트 코드(타입 `src/api/types.ts`, mock `src/mocks/`)가 v0.3 형식이라 아래를 맞춰야 한다. 백엔드는 바뀌는 동안 v0.3 값도 받는다 (7절).

**세션·프로필**
- [ ] 세션 생성 화면에서 관심 분야(`category`) 선택 제거. 요청·응답에 `category` 없음 (4.1)
- [ ] 프로필 `region`을 시도 enum(`SEOUL`)에서 **5자리 지역 코드**로. 시도 → 시군구 두 단계 선택, 목록은 `GET /api/regions` (2.2, 4.5)
- [ ] 화면 상단 프로필 표시를 5자리 코드 기준으로 (표시명은 지역 목록에서 찾음)
- [ ] 첫 질문 전 세션 제목은 `"새 대화"`, 첫 질문 뒤 서버가 질문으로 만듦 (4.1)

**정책 카드 `PolicySummary`**
- [ ] `region`(시도 enum) 대신 표시 문구 `regionLabel` (`"서울특별시 강남구"`, `"경기도 5곳"`, `"전국"`). 카드 검사(`isPolicySummary`)를 이 기준으로
- [ ] 새 필드 `status`(신청 상태 5가지), `organization`(담당 기관), `sourceUrl`(관련 사이트). D-day는 `OPEN`·`UPCOMING`일 때만 (3.2)
- [ ] `category`가 `null`일 수 있음 (대분류 없는 정책)
- [ ] 카테고리 5개: `FINANCE` 없음, `PARTICIPATION` 추가, `WELFARE` 표시명 "복지·문화" (2.1)
- [ ] 링크 버튼 문구는 "신청하기"가 아니라 **"관련 사이트"**처럼 중립적으로 (3.2)

**정책 상세 `PolicyDetail`**
- [ ] `requiredDocuments`를 `string[]`에서 `string`으로 (3.3)
- [ ] 상세 문자열에 줄바꿈(`\n`)이 있음 → `white-space: pre-line`처럼 줄바꿈을 살려 표시
- [ ] 비활성 정책은 상세가 `404 POLICY_NOT_FOUND`. 채팅 기록 카드를 눌렀을 때 **"정책을 찾을 수 없음" 화면** 필요 (4.4)

**채팅**
- [ ] 카드에 **번호 배지**(`policies` 배열 위치 + 1). 답변 본문의 "1. 2. 3."과 같은 번호 (5.3)
- [ ] `policies` 이벤트는 첫 `token` **바로 앞**에 옴. 받는 즉시 카드를 그림 (5.2)
- [ ] 질문 입력창 상한 1,000자 (5.1)

**스크랩·검색**
- [ ] 정렬 규칙이 v0.3과 다름: **진행 중 → 예정 → 상시 → 정보 없음 → 마감**. 스크랩 직후 화면에서 먼저 정렬하는 규칙(`features/scrap/api/queries.ts`)과 mock의 `byAvailability`를 서버와 같게 (4.4, 4.6)
- [ ] 정책 검색 `region`은 지역 목록의 코드, `category`는 5개

**채팅방 안 정책 검색 (v1.1)**
- [ ] 채팅방 화면 상단 검색은 `GET /api/policies?sessionId=...`로 부름. 검색어(`keyword`)와 카테고리(`category`)만 고르고 **`region`은 보내지 않음** (보내면 400). 나머지 조건은 세션 프로필로 거름 (4.4)
- [ ] 결과가 0건일 때 빈 화면 (예: "조건에 맞는 정책이 없어요. 검색어나 카테고리를 바꿔 보세요"). 프로필 필터에 검색어·카테고리가 겹쳐 0건이 자주 나올 수 있음
- [ ] 결과는 "자격이 확인된 정책"이 아님 (1차 필터는 확실히 안 맞는 정책만 뺌). 화면 문구를 단정적으로 쓰지 않음

**배포 뒤**
- [ ] 배포 경로 SSE 확인: 뼈대 → 실제 RAG 순서로 확인하고 결과를 알려 줌 (6.3)

**오류**
- [ ] 요약은 `429`·`502`·`504`가 올 수 있음. `message`를 그대로 보여 주면 됨 (4.3)

## 1. 공통 규칙

### 1.1 호출 방식
- 모든 경로는 `/api`로 시작한다. 프론트는 상대 경로로 부르고, 개발 때는 Vite 프록시(`DEV_API_TARGET`), 배포 때는 Vercel rewrites가 **FastAPI 서버**로 넘긴다 (6절)
- 요청·응답 본문은 `application/json; charset=utf-8`. 채팅 응답만 `text/event-stream` (5절)
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
| 403 | `FORBIDDEN` | Vercel을 거치지 않은 요청 (출처 확인을 켰을 때만, 6.2) |
| 404 | `SESSION_NOT_FOUND` | 없는 세션, 다른 사용자의 세션, UUID가 아닌 세션 id |
| 404 | `POLICY_NOT_FOUND` | 없는 정책, 비활성 정책 |
| 404 | `NOT_FOUND` | 없는 경로 |
| 405 | `METHOD_NOT_ALLOWED` | 허용되지 않는 메서드 |
| 409 | `STREAM_IN_PROGRESS` | 같은 세션에서 답변을 만드는 중 (5.4) |
| 409 | `IDEMPOTENCY_CONFLICT` | 같은 `clientMessageId`에 다른 질문 (5.4) |
| 413 | `PAYLOAD_TOO_LARGE` | 본문 256KB 초과 |
| 429 | `RATE_LIMITED` | 요청 제한 초과, 하루 LLM 상한 (1.6). `Retry-After` 헤더(초) |
| 500 | `INTERNAL_ERROR` | 그 밖의 서버 오류 |
| 502 | `RAG_UPSTREAM_ERROR` | LLM 호출 실패 |
| 504 | `RAG_TIMEOUT` | LLM 응답 시간 초과 |

- 채팅은 스트림을 시작한 뒤의 실패를 JSON 대신 `error` 이벤트로 보낸다 (5.2)
- 이 형식이 아닌 응답도 올 수 있다 (Vercel 프록시의 502·504 HTML 등). `content-type`이 JSON이 아니면 상태 코드만으로 처리

### 1.5 페이지네이션
결과가 많은 목록(정책 검색)에만 쓴다.
- 요청 `?page=0&size=20` (`page`는 0부터)
- 응답 `{ "items": [...], "page": 0, "size": 20, "totalElements": 134, "totalPages": 7 }`

### 1.6 요청 제한
| 대상 | 기준 |
|---|---|
| `/api/*` 전체 | IP(`X-Forwarded-For` 첫 값)당 분당 300회 |
| 채팅, 대화 요약 | 위에 더해 IP당 분당 60회 + `X-Client-Id`당 분당 10회 |
| 서버 전체 LLM 호출 (채팅, 요약) | 하루 1,000회 |

- 넘으면 `429 RATE_LIMITED` + `Retry-After`(초)
- 하루 상한은 `message`가 다르다: "오늘 이용량이 많아 채팅을 잠시 멈췄어요. 내일 다시 이용해 주세요." (`Retry-After`는 한국 자정까지). 대화 요약도 같은 문구
- 수치는 시연 전에 서버 설정으로 바뀔 수 있음 (형식은 그대로)

### 1.7 캐시와 보안 헤더
- 사용자마다 다른 응답(세션 API 전체, 스크랩 목록, `sessionId`를 준 정책 검색)은 `Cache-Control: no-store`
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
  - 채팅 기록의 카드도 **조회 시점** 기준으로 다시 계산함
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

## 4. REST 엔드포인트

| 엔드포인트 | 설명 | 구현 |
|---|---|---|
| `POST /api/sessions` | 세션 만들기 | 됨 |
| `GET /api/sessions` | 내 세션 목록 | 됨 |
| `GET /api/sessions/{sessionId}` | 세션 하나 | 됨 |
| `GET /api/sessions/{sessionId}/messages` | 메시지 이력 | 됨 |
| `POST /api/sessions/{sessionId}/chat` | 채팅 (SSE, 5절) | 됨 |
| `GET /api/sessions/{sessionId}/recommendations` | 추천 정책 | 됨 |
| `POST /api/sessions/{sessionId}/summary` | 대화 요약 | 됨 |
| `POST /api/sessions/{sessionId}/guides` | 신청 가이드 PDF | **(나중)** |
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

### 4.7 신청 가이드 PDF `POST /api/sessions/{sessionId}/guides` (나중)
형식은 확정, 구현 전까지 프론트 mock 유지.
```json
// 요청
{ "policyId": "20251031005400211234" }
// 응답 201
{ "policyId": "20251031005400211234",
  "pdfUrl": "https://…/guides/….pdf",
  "expiresAt": "2026-11-04T18:10:00+09:00" }
```
- 동기 처리 (응답이 올 때 PDF가 만들어져 있음)
- `pdfUrl`은 **10분 이하**로 만료되는 링크. 만료되면 같은 요청으로 다시 받음 (PDF에 프로필 같은 민감 정보가 들어갈 수 있어서)
- 오류: 없는 세션 `404 SESSION_NOT_FOUND`, 없는 정책 `404 POLICY_NOT_FOUND`, 요청 제한 `429`. PDF 내용을 LLM으로 만들면 LLM 실패 `502 RAG_UPSTREAM_ERROR`·시간 초과 `504 RAG_TIMEOUT`, 그 밖의 생성 실패는 `500 INTERNAL_ERROR`. 어느 경우든 `message`를 그대로 보여 주면 됨
- PDF에 넣을 항목과 저장 위치는 구현할 때 백엔드가 정함 (API 형식은 바뀌지 않음)

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
- 카드는 **최대 10개**. 개수를 말하지 않은 질문은 보통 3개 안팎, "5개 알려줘"처럼 개수를 말하면 그만큼 (상한 10). 개수 지정은 구현 중이라 지금은 최대 6개 (8절)
- `policies` 순서 = 본문이 정책을 소개한 순서. **번호 배지 = 배열 위치 + 1** 이고 본문의 "1. 2. 3."과 같다
- 번호는 **답변마다 1부터** 센다. "더 알려줘"로 이어 받은 정책도 그 답변의 카드로 1부터 다시 셈
- 사용자가 "두 번째 거 신청 방법은?"처럼 번호로 물으면 **직전 답변의 카드 번호**로 이해함
- 카드 순서는 질문 관련도 기준이라 정책 검색·스크랩 정렬과 다름
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
- 프론트는 Vercel, `vercel.json` rewrites로 `/api/*`를 `http://<서버 주소>/api/*`에 넘김. 브라우저는 Vercel(HTTPS)만 부르므로 CORS 설정은 없음
- 서버 주소는 인프라 배포 뒤 알려 줌. 그 전에는 로컬 백엔드에 `DEV_API_TARGET`으로 붙임
- Vercel 경유 SSE가 통과하지 못하면 브라우저가 서버에 직접 붙는 방식(sslip.io + TLS)으로 바꾸고, 그때 백엔드에 CORS·HTTPS를 추가함

### 6.2 출처 확인 (나중에 켤 수 있음)
- Vercel이 `/api` 요청에 비밀 헤더 `X-Origin-Secret`을 붙이면, 서버로 직접 오는 요청을 `403 FORBIDDEN`으로 막을 수 있음
- **프론트 확인 요청**: `vercel.json` rewrites만으로 요청 헤더를 붙일 수 있는지

### 6.3 배포 경로 SSE 확인
서버가 뜨면 **뼈대 → 실제 RAG** 순서로 두 번 확인하고, 둘 다 통과하면 백엔드가 뼈대를 지운다. 실제 답변은 보통 10~20초라 프록시가 오래 걸리는 스트림을 끊지 않는지는 뼈대(90초 이상)로 먼저 본다.

| 단계 | 서버 설정 | 확인할 것 | 누가 |
|---|---|---|---|
| 1. 뼈대 | `CHAT_SKELETON_ENABLED=true` | 15초 대기 뒤 첫 `token`, 1초 간격으로 끊기지 않고 90개, 15초 `:ping`, `policies` → `done`으로 끝남. Vercel 경유와 서버 직접 모두 | 백엔드가 먼저 스크립트로, 이어서 프론트가 화면에서 |
| 2. 실제 RAG | `CHAT_SKELETON_ENABLED=false` (기본값) | 카드(`policies`)가 첫 `token` 바로 앞에 뜸, 본문 스트리밍, `done` 뒤 `GET …/messages`에 질문·답변·카드가 남음, 중지 버튼(연결 끊기) 뒤 이력에 질문만 남음, 같은 `clientMessageId`로 다시 보내기 | 프론트가 화면에서 |
| 3. 뼈대 삭제 | — | 1·2 통과를 프론트가 알려 주면 백엔드가 뼈대 코드와 설정을 지움 (프론트 변경 없음) | 백엔드 |

- 뼈대가 켜져 있는 동안 `/chat`은 **실제 답변 대신 검증용으로만 답함**. 1단계가 끝나면 바로 끄고 2단계로 감
- 뼈대 호출: `POST /api/sessions`로 세션을 만들고 그 id로 `/chat`
  ```bash
  curl -N -X POST <주소>/api/sessions/<id>/chat \
    -H 'X-Client-Id: <uuid>' -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
    -d '{"clientMessageId":"<uuid>","content":"SSE 검증"}'
  ```
- 뼈대가 보내는 것: 15초 대기 → `token` 90개 1초 간격 (`"검증용 답변 1/90. "` …) → `policies`(검증용 카드 `SKELETON-1`) → `done`(`skeleton-<clientMessageId>`). `:ping` 15초마다. 저장하지 않고 하루 LLM 상한에도 세지 않음
- 뼈대는 v0.3 시절의 확인용이라 `policies`가 본문 **뒤**에 옴 (실제 답변은 앞, 5.2). 순서 차이는 무시

## 7. 과도기 호환
프론트가 v0.3 형식이어도 깨지지 않게 서버가 잠시 함께 받거나 보낸다. **프론트가 v1.0 형식으로 바꿨다고 알려 주면 지운다.** 그 뒤에는 아래 v0.3 값이 `400 VALIDATION_FAILED`가 됨.

| 항목 | 지금 받는 것·보내는 것 |
|---|---|
| 프로필 `region` | 시도 enum(`SEOUL` 등)도 받아 "○○ 전체" 코드로 저장 (`SEOUL` → `"11000"`). `GWANGJU`·`JEONNAM`은 둘 다 `"12000"`. 응답은 5자리 코드 |
| 세션 생성 `category` | 보내도 무시 |
| 정책 검색 `region` | 시도 enum도 "○○ 전체"로 받음 |
| 정책 검색 `category` | `FINANCE`를 `WELFARE`로 받음 |
| 카드 `region` | `regionLabel`과 함께 v0.3 `region`도 보냄. 시도 하나면 그 시도 enum(통합특별시는 `JEONNAM`), 여러 시도·전국이면 `NATIONWIDE` |

## 8. 남은 일
| 항목 | 상태 |
|---|---|
| 신청 가이드 PDF | 형식 확정 (4.7), 구현 전 |
| 서버 주소 | 인프라 배포 뒤 (6.1) |
| 배포 경로 SSE 확인 | 서버가 뜨면 뼈대 → 실제 RAG 순서로 확인하고, 둘 다 통과하면 뼈대 삭제 (6.3) |
| 개수 지정("5개 알려줘", "더 알려줘")·복합 질문 답변 | 형식은 이 문서대로 (5.3), 답변 동작은 구현 중. 정책 비교는 구현됨 (5.3) |
| 과도기 호환 삭제 | 프론트 v1.0 반영 뒤 (7) |

## 변경 이력
- v0.1~v0.4 (2026-10-25~26): 프론트 초안 v0.3에 대한 백엔드 검토. 시군구 지역과 지역 목록, 세션 분야 삭제, 조건 되묻기, 카드 `status`·`regionLabel`·`organization`, 상세 문구, 카드 번호, `policies`를 본문 앞에 보내기 제안. 오류 코드 추가
- v0.5~v0.7 (2026-10-29~11-01): 잠정 계약 본문. 카테고리 5개, 프로필 코드 대응, 과도기 호환, 공개 API 전체 구현 (검색·상세·지역·스크랩·추천·요약)
- **v1.0 (2026-11-04)**: 프론트 초안 없이 읽히는 단일 계약으로 정리. 구현 현황 문서(`front-sync.md`)를 합침. 새로 정함: 카드 최대 10개와 답변마다 1부터 번호, 답변 마크다운은 CommonMark 범위(표 없음), 가이드 PDF 형식 확정(v0.3 형식, 동기), 과도기 호환은 프론트 반영 뒤 삭제, 배포 경로 SSE 확인은 뼈대 → 실제 RAG 순서로 하고 둘 다 통과하면 뼈대 삭제
- **v1.1 (2026-11-09)**: 정책 검색에 선택 쿼리 `sessionId` 추가 (채팅방 안 검색). 세션 프로필로 1차 필터, `keyword`·`category`만 함께 쓰고 `region`은 400. 정렬·페이지는 정책 검색 그대로
  - 보충 (11-09, 형식 변경 없음): 정책 비교 동작 (5.3). 두 정책까지, 카드는 비교한 정책만, 셋 이상은 되물음

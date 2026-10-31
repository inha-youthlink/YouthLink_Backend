# 프론트 연동 현황 (임시 문서)

- 기준: 2026-11-01, 백엔드 `main` (공개 API 1-1 #38, 배포 준비 #39, 공개 API 1-2 #41~#43, 공개 API 2 정책 검색·상세·지역 목록)
- 범위: 지금 백엔드 서버가 실제로 어디까지 되는지와 임시 형식. **약속(계약)은 [api-contract.md](api-contract.md)**, 이 문서는 구현 현황만 적는다
- 계약 본문(4절)이 채워지고 실연동이 끝나면 이 문서는 지운다

## 1. 지금 되는 것
| API | 상태 | 비고 |
|---|---|---|
| `POST /api/sessions` | 됨 | 프로필은 계약 형식 (계약 4.2), 과도기 호환 (3절) |
| `GET /api/sessions` | 됨 | 만든 시각 내림차순, `Cache-Control: no-store` |
| `GET /api/sessions/{id}` | 됨 | |
| `GET /api/sessions/{id}/messages` | 됨 | 오래된 순. 질문의 `policies`는 `null`, 카드 없는 답변은 `[]`. 질문에는 보낸 `clientMessageId`가 있음 (답변은 `null`). 연결이 끊기거나 생성이 실패한 질문은 답변 없이 남음 |
| `POST /api/sessions/{id}/chat` | **됨 (실제 RAG)** | 2절. 배포 경로 SSE 확인용 뼈대는 서버 설정으로만 켬 (2-1) |
| `GET /api/policies` | **됨** | 2-2절. 페이지 응답 `{items, page, size, totalElements, totalPages}`, 항목은 채팅 카드와 같은 형식 |
| `GET /api/policies/{id}` | **됨** | 2-2절. `requiredDocuments`는 문자열 하나, 문자열에 줄바꿈 있음 |
| `GET /api/regions` | **됨 (새 API)** | 2-2절. 시도 → 시군구 두 단계 선택용 |
| 요약·추천, 스크랩, 가이드 PDF | 아직 | mock 유지 (`VITE_MOCK_FEATURES=summary,guides,scraps`) |

공통 규칙은 계약대로 동작함
- `X-Client-Id`(UUID v4)가 없거나 형식이 틀리면 `400 INVALID_CLIENT_ID`. 대문자·하이픈 없는 형태도 같은 ID로 받음
- 다른 사용자의 세션, 없는 세션, UUID가 아닌 세션 id는 모두 `404 SESSION_NOT_FOUND`. 없는 정책은 `404 POLICY_NOT_FOUND`
- 오류는 `{code, message, details}`. 입력 검증 실패는 `400 VALIDATION_FAILED` + `details: [{field, reason}]` (`field`는 `profile.age`, `content`처럼 요청 필드 이름, `reason`은 한국어). 없는 경로 `404 NOT_FOUND`, 허용되지 않는 메서드 `405 METHOD_NOT_ALLOWED`, 그 외 `500 INTERNAL_ERROR`
- 일시는 `+09:00` (예: `2026-10-25T20:38:26.355621+09:00`, 소수점 초가 붙을 수 있음)

요청 제한과 보안 (배포 준비 PR, R8)
- IP(`X-Forwarded-For` 첫 값)당 분당 300회(`/api/*` 전체), 채팅은 IP당 분당 60회 + `X-Client-Id`당 분당 10회. 넘으면 `429 RATE_LIMITED` + `Retry-After`(초)
- 서버 전체 하루 LLM 1,000회. 넘으면 채팅이 `429 RATE_LIMITED`, `message`는 "오늘 이용량이 많아 채팅을 잠시 멈췄어요. 내일 다시 이용해 주세요." (다음 날 한국 자정까지의 `Retry-After`)
- 본문 256KB 초과 `413 PAYLOAD_TOO_LARGE`
- 모든 응답에 `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (Step 4 CSP 요청)
- 나중에 켤 수 있음: Vercel이 `/api` 요청에 비밀 헤더 `X-Origin-Secret`을 붙이면 EC2로 직접 오는 요청을 `403 FORBIDDEN`으로 막음. `vercel.json` rewrite만으로 요청 헤더를 붙일 수 있는지 프론트 확인이 필요함 (SSE 검증이 끝난 뒤)

## 2. 채팅 (`POST /api/sessions/{id}/chat`)
**이벤트 순서**
- 답변: `policies`(카드가 있을 때만) → `token`들 → `done`. 카드는 항상 첫 `token` 바로 앞 (B9). 인사·조건 되묻기처럼 카드가 없는 답변은 `policies` 없이 `token` → `done`
- `done`의 `messageId`는 저장된 답변 id (`GET …/messages`의 `id`와 같음)
- 질문 `content`는 앞뒤 공백을 지운 값으로 저장. 공백만 있으면 `400 VALIDATION_FAILED`

**같은 `clientMessageId`로 다시 보낼 때** (계약 §5.3)
| 서버 상태 | 응답 |
|---|---|
| 처음 보는 키 | 질문 저장 → 답변 생성 |
| 질문만 있음 (이전에 끊김·실패) | 질문은 다시 저장하지 않고 답변만 생성 |
| 답변까지 있음 (`done`을 못 받은 경우) | 새로 만들지 않고 저장된 답변을 같은 순서로: `policies`(있으면) → `token` 1개(전체 답변) → 기존 `messageId`의 `done` |
| 키는 같은데 `content`가 다름 | `409 IDEMPOTENCY_CONFLICT` (JSON) |
| 같은 세션에서 다른 답변을 만드는 중 | `409 STREAM_IN_PROGRESS` (JSON, 키가 달라도) |

**연결 끊김·실패**
- 연결을 끊으면 서버도 답변 생성을 멈추고 미완성 답변은 저장하지 않음. 질문은 남음 (끊은 뒤 `GET …/messages`로 이력을 다시 받으면 질문이 보임)
- 스트림 중 실패는 `error` 이벤트로 끝남 (`done` 없음): `RAG_TIMEOUT`(LLM 시간 초과), `RAG_UPSTREAM_ERROR`(LLM 호출 실패), `INTERNAL_ERROR`(그 밖). `message`는 화면에 그대로 보여 줄 수 있는 문장
- 하루 LLM 상한은 실제로 답변을 만들 때만 셈 (저장된 답변 다시 보내기는 세지 않음)

## 2-1. 배포 경로 SSE 검증 (R2)
더미 `POST /api/health/stream`은 만들지 않았다. 채팅 API 뼈대로 검증한다. 실제 답변은 10~20초라 긴 스트림 확인에는 뼈대를 쓴다.

**순서**
0. 서버 환경 변수 `CHAT_SKELETON_ENABLED=true`로 띄운다 (검증이 끝나면 끔. 켜져 있는 동안 `/chat`은 실제 답변 대신 뼈대로 답함)
1. `POST /api/sessions`로 세션을 만든다 (프로필 8항목 필요, 3절)
2. 그 `id`로 `POST /api/sessions/{id}/chat` 호출

```bash
curl -N -X POST <주소>/api/sessions/<id>/chat \
  -H 'X-Client-Id: <uuid>' -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"clientMessageId":"<uuid>","content":"SSE 검증"}'
```

**보내는 것** (계약 §5.4 조건)
- 응답 헤더: `text/event-stream; charset=utf-8`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`
- 15초 대기 → `token` 90개 1초 간격 (`"검증용 답변 1/90. "` …, 10번째마다 끝에 `\n`) → `policies` → `done`
- `:ping`은 15초마다 (token을 보내는 동안에도)
- `policies`: 검증용 카드 하나 `{"id":"SKELETON-1","title":"SSE 검증용 정책","status":"ALWAYS_OPEN","regionLabel":"전국",…}`
- `done`: `{"messageId":"skeleton-<clientMessageId>"}`. 같은 `clientMessageId`로 다시 보내면 같은 id. 뼈대는 저장하지 않음
- 세션·입력 오류는 스트림 전에 JSON (404, 400). `content`는 1~1000자

백엔드 로컬 확인 결과: 첫 token 15.00초, 간격 중앙값·최대 1.00초, ping 최대 간격 15.00초, `policies` → `done`으로 끝남. 연결을 중간에 끊으면 서버가 바로 멈춤

## 2-2. 정책 검색·상세, 지역 목록
자세한 규칙은 계약 4.3·4.4

**검색 `GET /api/policies?keyword=&category=&region=&page=0&size=20`**
- 모두 선택. 빈 값은 조건 없음. `keyword` 50자 이하(띄어쓰기로 나눈 낱말이 모두 있어야 걸림), `category`는 5개(`JOB`, `HOUSING`, `EDUCATION`, `WELFARE`, `PARTICIPATION`), `region`은 지역 목록의 코드, `size` 1~50. 어기면 `400 VALIDATION_FAILED` (`details[].field`가 쿼리 이름)
- 지역은 "고른 지역 + 그 시도 전체 + 전국" 정책을 보여 줌 (채팅 추천과 같은 범위)
- 정렬: 진행 중(마감 임박 순) → 예정(시작 임박 순) → 상시 → 기간 정보 없음 → 마감(최근 마감 순). **mock의 `byAvailability`와 다를 수 있음**
- 응답 시간 약 0.3~0.4초 (대부분 지역 표시 조회. 응답 속도 작업 때 줄임)

**상세 `GET /api/policies/{id}`**
- 카드 필드 + `summary`, `eligibility`, `benefits`, `howToApply`, `requiredDocuments`. **모두 문자열**이고 줄바꿈(`\n`)이 있어 `white-space: pre-line`처럼 줄바꿈을 살려 표시
- `requiredDocuments`는 v0.3의 `string[]`이 아니라 `string` (원본이 자유 문장)
- `eligibility`는 서버가 만든 여러 줄 (`- 나이: 만 19~39세`, `- 소득: 무관`, … 뒤에 `추가 자격`·`참여 제한 대상` 원문)
- 신청 방법·제출 서류가 비어 있으면 안내 문구가 들어 있음 (예: "공고에 나와 있지 않아요. 담당 기관(서울특별시)에 문의해 주세요."). 프론트에서 따로 빈 값 처리를 하지 않아도 됨
- 링크 버튼(`sourceUrl`)은 `null`인 정책이 많음 (3,543건 중 2,431건, 약 69%. 신청 URL이 비었거나 주소 모양이 아닌 값). 절반 이상이 기관 홈페이지 첫 화면이라 버튼 문구는 "관련 사이트"처럼 중립적으로 (B7)

**지역 목록 `GET /api/regions`**
- `[{code, name, sigungu: [{code, name}]}]`. 시도를 고르면 그 시도의 `sigungu`를 보여 주고, 첫 항목은 "○○ 전체"(`○○000`)
- 시도 순서는 서버가 정함 (서울 → 경기 → 인천 → 행정 표준 순서). 받은 순서대로 그리면 됨
- 프로필 입력과 정책 검색의 지역 선택 모두 이 목록의 `code`를 그대로 보냄. 구가 있는 시는 시 하나(수원시 `41110`)
- 옛 광주광역시의 구는 지역 표 이름 그대로 "동구", "서구"처럼 보임 (전남광주통합특별시 아래)

## 3. 형식과 과도기 호환
세션·카드는 계약 4절(v0.6) 형식. 프론트가 아직 v0.3 형식이어도 첫 실연동이 깨지지 않게 아래를 함께 받거나 보냄 (계약 4.6, 프론트가 바뀌면 지움)

| 항목 | 지금 | 프론트가 할 일 |
|---|---|---|
| 세션 생성 `profile` | 계약 enum 8항목 필수 (`{"region": "11680", "maritalStatus": "SINGLE", "age": 27, "annualIncome": 3000, "education": "COLLEGE_GRADUATED", "major": "ENGINEERING", "employmentStatus": "UNEMPLOYED", "specialField": "NONE"}`). `region`에 v0.3 시도 enum(`SEOUL`)을 보내도 받음 (`"11000"`으로 저장) | 시군구 선택은 지역 목록 API(B2) 뒤 |
| 응답의 `profile` | 같은 형식. `region`은 5자리 코드 (`SEOUL`로 만들었으면 `"11000"`) | 헤더의 지역 표시를 5자리 코드 기준으로 |
| 세션 `category` | 받지 않음 (보내도 무시), 응답에도 없음 (B3) | 세션 생성 화면의 분야 선택 제거 |
| 첫 질문 전 `title` | `"새 대화"` | |
| 정책 검색 `region`·`category` | 지역 목록 코드와 5개 분류. v0.3 시도 enum(`SEOUL`)은 "○○ 전체"로, `FINANCE`는 `WELFARE`로 받음 | 지역 목록 API와 5개 분류로 |
| 정책 상세 `requiredDocuments` | 문자열 하나 | 타입을 `string`으로 (v0.3 `string[]`) |
| 카드 (`policies`) | `id`, `title`, `category`, `applyStart`, `applyEnd`, `status`, `regionLabel`, `organization`, `sourceUrl` + 과도기 `region` (v0.3 시도 enum 또는 `NATIONWIDE`) | 카드 검사(`isPolicySummary`)를 `regionLabel` 기준으로, `category`가 `null`일 수 있음 (대분류 없는 정책 1건) |

## 4. 다음 (백엔드)
- 서버 주소(R1): 인프라 담당자가 띄우면 이 문서에 추가 (배포 방법은 [deploy.md](deploy.md))
- 공개 API 2: 스크랩, 요약·추천

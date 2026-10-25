# 프론트 연동 현황 (임시 문서)

- 기준: 2026-10-26, 백엔드 `main` (공개 API 1-1 #38, 배포 준비 PR)
- 범위: 지금 백엔드 서버가 실제로 어디까지 되는지와 임시 형식. **약속(계약)은 [api-contract.md](api-contract.md)**, 이 문서는 구현 현황만 적는다
- 계약 본문(4절)이 채워지고 실연동이 끝나면 이 문서는 지운다

## 1. 지금 되는 것
| API | 상태 | 비고 |
|---|---|---|
| `POST /api/sessions` | 됨 | 프로필은 임시 형식 (3절) |
| `GET /api/sessions` | 됨 | 만든 시각 내림차순, `Cache-Control: no-store` |
| `GET /api/sessions/{id}` | 됨 | |
| `GET /api/sessions/{id}/messages` | 됨 | 오래된 순. 질문의 `policies`는 `null`, 카드 없는 답변은 `[]`. `clientMessageId`는 아직 항상 `null` |
| `POST /api/sessions/{id}/chat` | **뼈대** | RAG 없이 고정 문장 (2절). 저장하지 않음 |
| 요약·추천, 정책 검색·상세, 스크랩, 가이드 PDF | 아직 | mock 유지 |

공통 규칙은 계약대로 동작함
- `X-Client-Id`(UUID v4)가 없거나 형식이 틀리면 `400 INVALID_CLIENT_ID`. 대문자·하이픈 없는 형태도 같은 ID로 받음
- 다른 사용자의 세션, 없는 세션, UUID가 아닌 세션 id는 모두 `404 SESSION_NOT_FOUND`
- 오류는 `{code, message, details}`. 입력 검증 실패는 `400 VALIDATION_FAILED` + `details: [{field, reason}]` (`field`는 `profile.age`, `content`처럼 요청 필드 이름, `reason`은 한국어). 없는 경로 `404 NOT_FOUND`, 허용되지 않는 메서드 `405 METHOD_NOT_ALLOWED`, 그 외 `500 INTERNAL_ERROR`
- 일시는 `+09:00` (예: `2026-10-25T20:38:26.355621+09:00`, 소수점 초가 붙을 수 있음)

요청 제한과 보안 (배포 준비 PR, R8)
- IP(`X-Forwarded-For` 첫 값)당 분당 300회(`/api/*` 전체), 채팅은 IP당 분당 60회 + `X-Client-Id`당 분당 10회. 넘으면 `429 RATE_LIMITED` + `Retry-After`(초)
- 서버 전체 하루 LLM 1,000회. 넘으면 채팅이 `429 RATE_LIMITED`, `message`는 "오늘 이용량이 많아 채팅을 잠시 멈췄어요. 내일 다시 이용해 주세요." (다음 날 한국 자정까지의 `Retry-After`)
- 본문 256KB 초과 `413 PAYLOAD_TOO_LARGE`
- 모든 응답에 `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (Step 4 CSP 요청)
- 나중에 켤 수 있음: Vercel이 `/api` 요청에 비밀 헤더 `X-Origin-Secret`을 붙이면 EC2로 직접 오는 요청을 `403 FORBIDDEN`으로 막음. `vercel.json` rewrite만으로 요청 헤더를 붙일 수 있는지 프론트 확인이 필요함 (SSE 검증이 끝난 뒤)

## 2. SSE 검증 (R2)
더미 `POST /api/health/stream`은 만들지 않았다. 채팅 API 뼈대로 검증한다.

**순서**
1. `POST /api/sessions`로 세션을 만든다 (본문 `{}`이어도 됨)
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
- `done`: `{"messageId":"skeleton-<clientMessageId>"}`. 같은 `clientMessageId`로 다시 보내면 같은 id
- 세션·입력 오류는 스트림 전에 JSON (404, 400). `content`는 1~1000자

백엔드 로컬 확인 결과: 첫 token 15.00초, 간격 중앙값·최대 1.00초, ping 최대 간격 15.00초, `policies` → `done`으로 끝남. 연결을 중간에 끊으면 서버가 바로 멈춤

## 3. 임시 형식 (회의 뒤 바뀜)
| 항목 | 지금 | 바뀔 방향 |
|---|---|---|
| 세션 생성 `profile` | 백엔드 DB 코드 그대로, 모두 선택, snake_case (`{"age": 27, "region_code": "11680", "job_code": "0013003", …}`) | 계약 enum(H2)과 지역 코드(H3), 8항목 필수·나이 15~39 검증 |
| 응답의 `profile` | 위와 같은 형식 | 위와 같음 |
| `ChatSession.category` | 없음 | B3 결과에 따라 |
| 첫 질문 전 `title` | `"새 대화"` | 그대로 (B3 제안) |
| 카드 (`policies`) | `id`, `title`, `applyStart`, `applyEnd`, `status`, `regionLabel`, `organization`, `sourceUrl` | 계약 B5~B7 합의대로. `category`는 H1 뒤 |

## 4. 다음 (백엔드)
- 서버 주소(R1): 인프라 담당자가 띄우면 이 문서에 추가 (배포 방법은 [deploy.md](deploy.md))
- 채팅 본구현: 실제 RAG 답변 스트리밍, `policies`를 첫 `token` 직전에 (B9), `clientMessageId` 멱등 처리(§5.3), 연결이 끊기면 생성 취소
- 공개 API 2: 정책 검색·상세, 요약·추천, 스크랩

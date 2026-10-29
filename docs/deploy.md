# 운영 안내 (인프라 담당용)

- 기준: 2026-10-29, `main`
- 컨테이너 이미지와 배포 설정(Dockerfile, compose, 워크플로)은 **별도 배포 저장소**에서 관리합니다. 이 문서는 그 설정을 만들 때 알아야 할 앱 쪽 조건과 주의사항만 적습니다
- 백엔드는 **FastAPI 서버 하나**입니다. 프론트(Vercel)가 `/api/*`를 이 서버로 넘기고, 평가 서버는 `/internal/*`을 직접 부릅니다

## 1. 실행 조건
```
브라우저 ──HTTPS──▶ Vercel ──/api/*──▶ 백엔드:8000 ──▶ PostgreSQL + pgvector
평가 서버 ─────────────/internal/*───▶ 백엔드:8000  └─▶ OpenAI API
```
- 실행 명령: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (의존성은 `uv sync --frozen --no-dev`로 `uv.lock` 그대로 설치)
- 헬스 체크 `GET /health`: DB·OpenAI는 확인하지 않고 항상 빠르게 200
- **워커(프로세스)는 하나**로 띄웁니다. 요청 제한 횟수를 프로세스 메모리에 세서, 여러 개면 따로 셉니다
- 비밀 값(`.env`)은 이미지에 넣지 않고 실행할 때 환경 변수로 넣습니다
- DB 스키마와 정책 데이터는 ETL 저장소가 만듭니다. 채팅 테이블은 ETL 마이그레이션 008(이후 009)이 적용돼 있어야 합니다

## 2. 환경 변수
전체 목록과 기본값은 `.env.example`에 있습니다. 운영에서 꼭 볼 것:

| 변수 | 운영 값 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | 필수 | |
| `DATABASE_URL` | 필수 | `postgresql+asyncpg://user:password@host:5432/dbname`. 컨테이너 안에서 호스트의 DB에 붙을 때 `localhost`는 컨테이너 자신을 가리킴 |
| `APP_ENV` | `prod` | API 문서(`/docs`)를 끄고, 아래 안전 설정이 없으면 **시작하지 않음** |
| `DEBUG` | 비움 | `prod`에서 `true`면 시작하지 않음 (오류 화면에 내부 정보가 나감) |
| `INTERNAL_API_KEY` | 필수 (32자 이상) | `/internal` 호출 키. 평가 서버가 `X-Internal-Key` 헤더로 같은 값을 보냄. 만들기: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `TRUST_PROXY_HEADERS` | 비움 (기본 `true`) | Vercel 뒤라 `X-Forwarded-For` 첫 값을 사용자 IP로 봄. 끄면 모든 사용자가 Vercel IP 하나로 묶여 요청 제한에 같이 걸림 |
| `RATE_LIMIT_PER_MINUTE` 등 | 비움 (기본값) | 요청 제한. `0`이면 끔 (3절) |
| `ORIGIN_SECRET` | 비움 (나중에) | Vercel이 붙이는 `X-Origin-Secret` 값. 켜면 서버로 직접 오는 `/api` 요청을 403으로 막음. Vercel 쪽에서 헤더를 붙일 수 있을 때만 |
| `CHAT_SKELETON_*` | 비움 | 채팅 뼈대의 대기·길이 (SSE 검증용) |

- **env 파일은 값 뒤에 주석을 쓰면 안 됩니다.** Docker `--env-file` 같은 도구는 `KEY=값  # 메모`의 `# 메모`까지 값으로 읽습니다. 주석은 윗줄에 씁니다 (`.env.example` 형식)
- 설정이 틀리면(숫자 칸에 글자, `prod`인데 키 없음 등) 서버가 이유를 출력하고 멈춥니다. 오류 출력에 설정 값은 찍히지 않습니다
- 환경 변수를 바꾸면 **프로세스(컨테이너)를 새로 띄워야** 적용됩니다. 컨테이너는 만들 때의 환경 변수를 계속 쓰므로 `docker restart`로는 바뀌지 않습니다. 새로 띄우는 몇 초 동안 요청이 실패할 수 있어 시연 직전에는 피합니다

## 3. 보안·요청 제한 (앱이 직접 함)
| 항목 | 동작 |
|---|---|
| `/internal/*` | `X-Internal-Key`가 `INTERNAL_API_KEY`와 같을 때만 실행, 아니면 401 |
| 공개 API 요청 제한 | IP당 분당 300회 (`/api/*` 전체), 채팅은 IP당 분당 60회 + 브라우저(`X-Client-Id`)당 분당 10회. 넘으면 `429` + `Retry-After` |
| 하루 LLM 상한 | 서버 전체 하루 1,000회 (한국 자정 초기화). 넘으면 채팅 `429` |
| 제외 | `/health`, `/internal/*`은 요청 제한 없음 |
| 본문 크기 | 256KB 넘으면 `413` |
| 응답 헤더 | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy` |

- 횟수는 메모리에 있어 **프로세스를 새로 띄우면 0부터** 셉니다
- OpenAI 계정의 월 예산 상한은 키 관리자가 따로 설정합니다 (모든 방어가 뚫려도 비용이 멈추는 마지막 장치)

## 4. 프록시(Nginx 등)를 앞에 둘 때
없어도 됩니다 (Vercel → 백엔드 8000 직접). 두면 HTTPS(Let's Encrypt)를 붙이기 쉽습니다. 채팅이 SSE(긴 스트리밍 응답)라 아래 설정이 필요합니다:
```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;        # 앱도 X-Accel-Buffering: no를 보냄
    proxy_read_timeout 300s;    # 답변 생성 중 연결 유지 (앱이 15초마다 :ping)
    client_max_body_size 256k;
}
```
- `X-Forwarded-For`는 위처럼 **덧붙이기**(`$proxy_add_x_forwarded_for`)로 둡니다. 앱은 첫 값을 사용자 IP로 봅니다
- 로드 밸런서를 쓰면 유휴 타임아웃을 120초 이상으로 둡니다 (계약 R4)

## 5. 배포 후 확인
```bash
curl http://<주소>:8000/health                                   # {"status":"ok"}
curl -o /dev/null -w '%{http_code}\n' http://<주소>:8000/docs      # 404 (운영에서 꺼짐)
curl -X POST http://<주소>:8000/internal/pipeline -d '{}'          # 401 (키 없음)
```
SSE는 백엔드 저장소의 확인 스크립트로 시간을 잴 수 있습니다 (백엔드 담당에게 요청).

## 6. 백엔드에 알려 주면 좋을 것
- **서버 주소**: 프론트 연동 문서(`docs/front-sync.md`)에 적고, 배포 경로(Vercel 경유 포함) SSE를 백엔드가 먼저 확인합니다
- **프록시·HTTPS 사용 여부**: 쓰시면 4절 SSE 설정만 확인 부탁드립니다
- **환경 변수를 넣는 방식**: 바뀐 설정을 반영하는 순서를 이 문서에 맞춰 적겠습니다

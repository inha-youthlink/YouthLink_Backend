# 배포 안내 (인프라 담당용)

- 기준: 2026-10-26, `main` (배포 준비 PR 반영)
- 백엔드는 **FastAPI 컨테이너 하나**입니다. 프론트(Vercel)가 `/api/*`를 이 서버로 넘기고, 평가 서버는 `/internal/*`을 직접 부릅니다
- 이 문서의 Dockerfile·설정은 기본안입니다. 팀 규칙(베이스 이미지, 레지스트리, compose)에 맞게 고쳐도 됩니다

## 1. 구성
```
브라우저 ──HTTPS──▶ Vercel ──/api/*──▶ EC2:8000 (이 컨테이너) ──▶ PostgreSQL + pgvector
평가 서버 ─────────────/internal/*───▶ EC2:8000                 └─▶ OpenAI API
```
- 컨테이너 포트 `8000`, 헬스 체크 `GET /health` (DB·OpenAI는 확인하지 않음, 항상 빠르게 200)
- **워커(프로세스)는 하나**로 띄웁니다. 요청 제한 횟수를 프로세스 메모리에 세서 여러 개면 따로 셉니다
- DB 스키마와 정책 데이터는 ETL 저장소가 만듭니다. 채팅 테이블은 ETL 마이그레이션 008이 적용돼 있어야 합니다

## 2. 이미지
```bash
docker build -t youthlink-backend .
```
- Python 3.12-slim, `uv.lock` 그대로 설치(`--frozen`), root가 아닌 사용자(`appuser`)로 실행, `HEALTHCHECK` 포함
- 이미지에는 `app/`과 의존성만 들어갑니다. `.env`·테스트·문서는 `.dockerignore`로 뺍니다
- **Apple Silicon Mac에서 빌드해 x86 EC2에 올릴 때**는 `docker build --platform linux/amd64 …`. GitHub Actions(x86)에서 빌드하면 필요 없습니다

## 3. 실행
```bash
docker run -d --name youthlink-backend --restart unless-stopped \
  --env-file /opt/youthlink/.env -p 8000:8000 youthlink-backend
```
- 비밀 값은 이미지에 넣지 않고 실행할 때 `--env-file`이나 `-e`로 넣습니다
- **env 파일은 값 뒤에 주석을 쓰면 안 됩니다.** Docker는 `KEY=값  # 메모`의 `# 메모`까지 값으로 읽습니다. 주석은 윗줄에 씁니다 (`.env.example` 형식)

## 4. 환경 변수
전체 목록과 기본값은 `.env.example`에 있습니다. 운영에서 꼭 볼 것:

| 변수 | 운영 값 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | 필수 | |
| `DATABASE_URL` | 필수 | `postgresql+asyncpg://user:password@host:5432/dbname` |
| `APP_ENV` | `prod` | API 문서(`/docs`)를 끄고, 아래 안전 설정이 없으면 **시작하지 않음** |
| `DEBUG` | 비움 | `prod`에서 `true`면 시작하지 않음 (오류 화면에 내부 정보가 나감) |
| `INTERNAL_API_KEY` | 필수 (32자 이상) | `/internal` 호출 키. 평가 서버가 `X-Internal-Key` 헤더로 같은 값을 보냄. 만들기: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `TRUST_PROXY_HEADERS` | 비움 (기본 `true`) | Vercel 뒤라 `X-Forwarded-For` 첫 값을 사용자 IP로 봄. 끄면 모든 사용자가 Vercel IP 하나로 묶여 요청 제한에 같이 걸림 |
| `RATE_LIMIT_PER_MINUTE` 등 | 비움 (기본값) | 요청 제한. `0`이면 끔 (5절) |
| `ORIGIN_SECRET` | 비움 (나중에) | Vercel이 붙이는 `X-Origin-Secret` 값. 켜면 EC2로 직접 오는 `/api` 요청을 403으로 막음. Vercel 쪽에서 헤더를 붙일 수 있을 때만 |
| `CHAT_SKELETON_*` | 비움 | 채팅 뼈대의 대기·길이 (SSE 검증용, 본구현에서 없어짐) |

설정이 틀리면(숫자 칸에 글자, `prod`인데 키 없음 등) 서버가 이유를 출력하고 멈춥니다. 오류 출력에 설정 값은 찍히지 않습니다.

## 5. 보안·요청 제한 (앱이 직접 함)
| 항목 | 동작 |
|---|---|
| `/internal/*` | `X-Internal-Key`가 `INTERNAL_API_KEY`와 같을 때만 실행, 아니면 401 |
| 공개 API 요청 제한 | IP당 분당 300회 (`/api/*` 전체), 채팅은 IP당 분당 60회 + 브라우저(`X-Client-Id`)당 분당 10회. 넘으면 `429` + `Retry-After` |
| 하루 LLM 상한 | 서버 전체 하루 1,000회 (한국 자정 초기화). 넘으면 채팅 `429` |
| 제외 | `/health`, `/internal/*`은 요청 제한 없음 |
| 본문 크기 | 256KB 넘으면 `413` |
| 응답 헤더 | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy` |

- 횟수는 메모리에 있어 **컨테이너를 다시 만들면 0부터** 셉니다
- 시연 전에 제한을 풀거나 올리려면 환경 변수를 바꾸고 컨테이너를 새로 만듭니다 (6절)
- OpenAI 계정의 월 예산 상한은 키 관리자가 따로 설정합니다 (모든 방어가 뚫려도 비용이 멈추는 마지막 장치)

## 6. 설정 바꾸기
```bash
vi /opt/youthlink/.env
docker rm -f youthlink-backend && docker run -d … (3절 명령 그대로)
# compose를 쓰면: docker compose up -d
```
- **`docker restart`로는 바뀐 값이 적용되지 않습니다.** 컨테이너는 만들 때의 환경 변수를 계속 씁니다. 새로 만들어야 합니다
- 새로 만드는 몇 초 동안 요청이 실패할 수 있어 시연 직전에는 피합니다

## 7. Nginx를 앞에 둘 때 (선택)
없어도 됩니다 (Vercel → 컨테이너 8000 직접). 두면 EC2에 HTTPS(Let's Encrypt)를 붙이기 쉽습니다. 채팅이 SSE(긴 스트리밍 응답)라 아래 설정이 필요합니다:
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

## 8. 배포 후 확인
```bash
curl http://<주소>:8000/health                                   # {"status":"ok"}
curl -o /dev/null -w '%{http_code}\n' http://<주소>:8000/docs      # 404 (운영에서 꺼짐)
curl -X POST http://<주소>:8000/internal/pipeline -d '{}'          # 401 (키 없음)
```
SSE는 백엔드 저장소의 확인 스크립트로 시간을 잴 수 있습니다 (백엔드 담당에게 요청). Vercel 경유 검증은 프론트가 합니다.

## 9. 함께 정하면 좋을 것
인프라 구성은 편하신 방식으로 정해 주시면 백엔드가 맞추겠습니다. 아래는 백엔드 쪽 설정이나 문서에 영향이 있어 알려 주시면 좋은 것들입니다.
- **빌드·배포 방식** (GitHub Actions, 레지스트리): 워크플로 작성을 도와야 하면 말씀해 주세요
- **환경 변수를 넣는 방식** (env 파일, compose, GitHub Secrets 등): 이 문서의 6절 "설정 바꾸기"를 그 방식에 맞게 고치겠습니다
- **Nginx·HTTPS 사용 여부**: 쓰시면 7절 SSE 설정만 확인 부탁드립니다. HTTPS가 있으면 평가 서버가 보내는 `/internal` 키도 암호화돼서 좋지만, 시연 일정에 부담이 되면 없어도 괜찮습니다
- **DB 위치와 EC2 아키텍처** (x86 / ARM): 이미지 빌드 옵션이 달라질 수 있습니다

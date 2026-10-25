FROM python:3.12-slim

# 로컬과 같은 uv 버전으로 uv.lock을 그대로 설치함
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 의존성을 코드보다 먼저 설치해 코드만 바뀔 때는 이 단계를 캐시로 건너뜀
# 내려받은 패키지 캐시는 실행에 필요 없어 이미지에 남기지 않음
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache

COPY app ./app

# 앱이 뚫려도 컨테이너 안에서 할 수 있는 일을 줄이려고 root가 아닌 사용자로 실행함
RUN useradd --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8000

# slim 이미지에 curl이 없어 Python으로 확인함
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# 요청 횟수를 프로세스 메모리에 세서 워커는 하나만 둠
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

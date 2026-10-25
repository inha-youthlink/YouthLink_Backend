import json

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import is_public_path

# JSON API라 페이지를 그리지 않음. 응답이 다른 사이트에 끼워지거나 다른 형식으로 해석되지 않게 막음
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
# Swagger 화면은 CDN 스크립트를 써서 CSP를 걸면 깨짐 (운영에서는 꺼져 있음)
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


# 응답 본문을 감싸지 않고 시작 헤더만 고쳐 SSE 스트리밍에 영향이 없음
class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(_DOCS_PATHS):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in SECURITY_HEADERS.items():
                    headers.setdefault(key, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


# 큰 본문으로 메모리를 쓰게 하는 요청을 앱에 닿기 전에 거절함
# Content-Length가 없는 요청(chunked)은 상한까지만 받아 두고 넘으면 거절함
class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        length = Headers(scope=scope).get("content-length")
        if length is not None:
            if not length.isdigit() or int(length) > self.max_bytes:
                await _too_large(scope, send)
                return
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await _too_large(scope, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        # 받아 둔 본문을 한 번 돌려준 뒤에는 원래 receive로 넘겨 연결 끊김(SSE)을 알 수 있게 함
        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


# 미들웨어는 예외 처리기를 거치지 않아 오류 형식을 여기서 맞춤 (/api는 계약 형식, 나머지는 FastAPI 기본 형식)
async def _too_large(scope: Scope, send: Send) -> None:
    message = "요청 본문이 너무 큽니다."
    if is_public_path(scope["path"]):
        body = {"code": "PAYLOAD_TOO_LARGE", "message": message}
    else:
        body = {"detail": message}
    payload = json.dumps(body, ensure_ascii=False).encode()
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
    })
    await send({"type": "http.response.body", "body": payload})

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.public import ErrorDetail, ErrorResponse

PUBLIC_PREFIX = "/api"

# 라우팅 단계에서 나는 오류(없는 경로, 허용 안 된 메서드)는 ApiError를 거치지 않아 여기서 코드를 붙임
_HTTP_ERRORS = {
    status.HTTP_404_NOT_FOUND: ("NOT_FOUND", "요청한 주소를 찾을 수 없습니다."),
    status.HTTP_405_METHOD_NOT_ALLOWED: ("METHOD_NOT_ALLOWED", "지원하지 않는 요청 방식입니다."),
}

# pydantic 기본 문구는 영어라 화면에 그대로 보여 줄 수 있게 자주 나는 유형만 바꿈
_VALIDATION_REASONS = {
    "missing": "필수 항목입니다.",
    "string_too_short": "{min_length}자 이상이어야 합니다.",
    "string_too_long": "{max_length}자 이하여야 합니다.",
    "greater_than_equal": "{ge} 이상이어야 합니다.",
    "less_than_equal": "{le} 이하여야 합니다.",
    "int_parsing": "정수여야 합니다.",
    "int_type": "정수여야 합니다.",
    "string_type": "문자열이어야 합니다.",
    "uuid_parsing": "UUID 형식이어야 합니다.",
    "json_invalid": "JSON 형식이 올바르지 않습니다.",
}
_DEFAULT_REASON = "형식이 올바르지 않습니다."


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


class InvalidClientIdError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_400_BAD_REQUEST, "INVALID_CLIENT_ID", "사용자 식별 정보가 올바르지 않습니다.")


# 다른 사용자의 세션이 있는지 숨기려고 없는 경우와 같은 응답을 씀
class SessionNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", "세션을 찾을 수 없습니다.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)


# 평가 서버가 /internal의 기존 오류 형식을 쓰고 있어 공개 경로에만 계약 형식을 적용함
def _is_public(request: Request) -> bool:
    path = request.url.path
    return path == PUBLIC_PREFIX or path.startswith(PUBLIC_PREFIX + "/")


def _error_response(error: ApiError) -> JSONResponse:
    body = ErrorResponse(code=error.code, message=error.message, details=error.details)
    return JSONResponse(
        body.model_dump(exclude_none=True), status_code=error.status_code, headers=error.headers
    )


async def _handle_api_error(request: Request, error: ApiError) -> JSONResponse:
    return _error_response(error)


async def _handle_validation_error(request: Request, error: RequestValidationError) -> Response:
    if not _is_public(request):
        return await request_validation_exception_handler(request, error)
    details = [ErrorDetail(field=_field_name(item), reason=_reason(item)) for item in error.errors()]
    return _error_response(
        ApiError(status.HTTP_400_BAD_REQUEST, "VALIDATION_FAILED", "입력값을 확인해 주세요.", details)
    )


async def _handle_http_error(request: Request, error: StarletteHTTPException) -> Response:
    if not _is_public(request):
        return await http_exception_handler(request, error)
    code, message = _HTTP_ERRORS.get(error.status_code, ("HTTP_ERROR", "요청을 처리할 수 없습니다."))
    return _error_response(ApiError(error.status_code, code, message, headers=error.headers))


# Starlette가 응답 뒤 예외를 다시 올려 원인은 서버 로그에 남음. 응답에는 내부 정보를 넣지 않음
async def _handle_unexpected_error(request: Request, error: Exception) -> Response:
    if not _is_public(request):
        return PlainTextResponse("Internal Server Error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return _error_response(
        ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        )
    )


# 첫 칸은 body·query 같은 위치라 뺌. 깨진 JSON은 둘째 칸이 필드가 아니라 문자 위치라 body로 표시함
def _field_name(item: dict[str, Any]) -> str:
    loc = item["loc"]
    if item["type"] == "json_invalid":
        return str(loc[0])
    return ".".join(str(part) for part in loc[1:]) or str(loc[0])


def _reason(item: dict[str, Any]) -> str:
    template = _VALIDATION_REASONS.get(item["type"], _DEFAULT_REASON)
    try:
        return template.format(**item.get("ctx", {}))
    except KeyError:
        return _DEFAULT_REASON

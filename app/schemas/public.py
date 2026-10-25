from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

from app.schemas.policy_info import KST


# 프론트 계약이 camelCase라 공개 API 스키마만 별칭을 씀. /internal은 snake_case 유지함
class PublicModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# DB는 UTC로 돌려줘 계약 예시(+09:00)와 같은 한국 시각으로 바꿔 보냄
KstDateTime = Annotated[
    datetime,
    PlainSerializer(lambda value: value.astimezone(KST).isoformat(), return_type=str),
]


class ErrorDetail(BaseModel):
    field: str
    reason: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None

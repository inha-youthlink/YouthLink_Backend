import pytest
from pydantic import ValidationError

from app.core.config import Settings

REQUIRED = {"openai_api_key": "test-key", "database_url": "postgresql+asyncpg://u:p@localhost/db"}


def _settings(**values) -> Settings:
    # 개발자 .env 값이 섞이지 않게 파일을 읽지 않음
    return Settings(_env_file=None, **REQUIRED, **values)


def test_local_allows_debug():
    assert _settings(app_env="local", debug=True).debug is True


def test_prod_rejects_debug():
    with pytest.raises(ValidationError, match="DEBUG") as error:
        _settings(app_env="prod", debug=True)

    # 오류 메시지에 비밀 값이 찍히지 않아야 함
    assert "test-key" not in str(error.value)


def test_prod_flag():
    assert _settings(app_env="prod").is_prod is True
    assert _settings(app_env="dev").is_prod is False

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REQUIRED = {"openai_api_key": "test-key", "database_url": "postgresql+asyncpg://u:p@localhost/db"}
PROD_KEY = "k" * 43


def _settings(**values) -> Settings:
    # 개발자 .env 값이 섞이지 않게 파일을 읽지 않음
    return Settings(_env_file=None, **REQUIRED, **values)


def test_local_allows_debug():
    assert _settings(app_env="local", debug=True).debug is True


def test_prod_rejects_debug():
    with pytest.raises(ValidationError, match="DEBUG") as error:
        _settings(app_env="prod", debug=True, internal_api_key=PROD_KEY)

    # 오류 메시지에 비밀 값이 찍히지 않아야 함
    assert "test-key" not in str(error.value)


def test_prod_flag():
    assert _settings(app_env="prod", internal_api_key=PROD_KEY).is_prod is True
    assert _settings(app_env="dev").is_prod is False


# 운영에서 키를 빠뜨리면 /internal이 열린 채로 뜨므로 시작을 막음
@pytest.mark.parametrize("key", [None, "", "short-key"])
def test_prod_requires_long_internal_key(key):
    with pytest.raises(ValidationError, match="INTERNAL_API_KEY") as error:
        _settings(app_env="prod", internal_api_key=key)

    assert "short-key" not in str(error.value)


def test_local_allows_no_internal_key():
    assert _settings(app_env="local").internal_api_key is None

import httpx
import pytest
from openai import APIStatusError

from starter_agent.domain.errors import (
    ProviderAuthenticationError,
    ProviderContextLengthError,
    ProviderInvalidRequestError,
    ProviderModelUnavailableError,
    ProviderPermissionDeniedError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderServiceUnavailableError,
)
from starter_agent.providers.openai_compatible import _classify_status_error


def make_status_error(status: int, body: dict) -> APIStatusError:
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return APIStatusError("upstream request failed", response=response, body=body)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": "invalid_api_key"}, ProviderAuthenticationError),
        (403, {"error": "access denied"}, ProviderPermissionDeniedError),
        (400, {"error": {"code": "model_not_found"}}, ProviderModelUnavailableError),
        (404, {"error": "not found"}, ProviderModelUnavailableError),
        (400, {"error": "unsupported parameter"}, ProviderInvalidRequestError),
        (400, {"error": "maximum context length exceeded"}, ProviderContextLengthError),
        (429, {"error": "rate limit exceeded"}, ProviderRateLimitError),
        (429, {"error": "insufficient quota"}, ProviderQuotaExceededError),
        (503, {"error": "temporarily unavailable"}, ProviderServiceUnavailableError),
    ],
)
def test_provider_status_error_is_classified(status, body, expected) -> None:
    assert _classify_status_error(make_status_error(status, body)) is expected


def test_public_error_is_actionable_and_does_not_include_upstream_body() -> None:
    error = ProviderModelUnavailableError(
        status=400,
        provider="example",
        model="missing-model",
    )

    assert error.to_public_dict() == {
        "code": "provider_model_unavailable",
        "message": "当前模型不可用或不存在",
        "suggestion": "请检查模型名称，或选择该服务商支持的模型",
        "retryable": False,
        "status": 400,
        "provider": "example",
        "model": "missing-model",
    }

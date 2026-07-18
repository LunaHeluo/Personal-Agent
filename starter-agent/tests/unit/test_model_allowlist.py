import pytest

from starter_agent.domain.errors import ProviderModelUnavailableError


async def test_application_rejects_model_not_configured_for_provider(application) -> None:
    with pytest.raises(ProviderModelUnavailableError) as caught:
        await application.chat(
            content="hello",
            provider_name="zhipu",
            model="glm-typo",
        )

    payload = caught.value.to_public_dict()
    assert payload["code"] == "provider_model_unavailable"
    assert payload["provider"] == "zhipu"
    assert "glm-4.7" in payload["suggestion"]
    assert "glm-5.1" in payload["suggestion"]

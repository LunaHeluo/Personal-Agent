from __future__ import annotations

from typing import Any


class AgentError(Exception):
    code = "agent_error"
    default_message = "请求处理失败"
    default_suggestion = "请稍后重试"
    retryable = False
    http_status = 400

    def __init__(self, message: str | None = None, *, suggestion: str | None = None,
                 status: int | None = None, provider: str | None = None,
                 model: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.suggestion = suggestion or self.default_suggestion
        self.status = status
        self.provider = provider
        self.model = model

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code, "message": str(self),
            "suggestion": self.suggestion, "retryable": self.retryable,
        }
        if self.status is not None:
            payload["status"] = self.status
        if self.provider:
            payload["provider"] = self.provider
        if self.model:
            payload["model"] = self.model
        return payload


class ConfigurationError(AgentError):
    code = "configuration_error"
    default_message = "服务配置不正确"
    default_suggestion = "请检查服务配置后重试"


class ProviderNotConfiguredError(ConfigurationError):
    code = "provider_not_configured"
    default_message = "所选模型服务尚未配置"
    default_suggestion = "请重新选择模型服务，或在配置文件中添加该服务"


class ProviderApiKeyMissingError(ConfigurationError):
    code = "provider_api_key_missing"
    default_message = "当前模型服务尚未配置 API Key"
    default_suggestion = "请在环境变量或项目 .env 文件中配置 API Key，然后重试"


class ProviderBaseUrlMissingError(ConfigurationError):
    code = "provider_base_url_missing"
    default_message = "当前模型服务尚未配置访问地址"
    default_suggestion = "请在配置文件中设置该服务的 Base URL，然后重试"


class ProviderError(AgentError):
    code = "provider_error"


class ProviderAuthenticationError(ProviderError):
    code = "provider_authentication_error"
    default_message = "API Key 无效、已过期或无法使用"
    default_suggestion = "请检查当前服务商的 API Key 配置"


class ProviderPermissionDeniedError(ProviderError):
    code = "provider_permission_denied"
    default_message = "当前 API Key 没有访问该模型的权限"
    default_suggestion = "请更换模型，或检查账号和模型访问权限"


class ProviderModelUnavailableError(ProviderError):
    code = "provider_model_unavailable"
    default_message = "当前模型不可用或不存在"
    default_suggestion = "请检查模型名称，或选择该服务商支持的模型"


class ProviderInvalidRequestError(ProviderError):
    code = "provider_invalid_request"
    default_message = "当前模型不支持本次请求参数"
    default_suggestion = "请检查模型是否支持工具调用、thinking 或 temperature 等参数"


class ProviderContextLengthError(ProviderError):
    code = "provider_context_length_exceeded"
    default_message = "对话内容超过模型的上下文限制"
    default_suggestion = "请缩短输入、新建会话，或选择上下文更大的模型"


class ProviderContentBlockedError(ProviderError):
    code = "provider_content_blocked"
    default_message = "请求内容被模型服务的安全策略拒绝"
    default_suggestion = "请调整输入内容后重试"


class ProviderRateLimitError(ProviderError):
    code = "provider_rate_limit_error"
    default_message = "请求过于频繁"
    default_suggestion = "请稍后重试"
    retryable = True


class ProviderQuotaExceededError(ProviderError):
    code = "provider_quota_exceeded"
    default_message = "API 额度已用完或账户余额不足"
    default_suggestion = "请检查账户余额、增加额度或更换 API Key"


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout_error"
    default_message = "模型响应超时"
    default_suggestion = "请稍后重试，或检查模型服务状态"
    retryable = True


class ProviderConnectionError(ProviderError):
    code = "provider_connection_error"
    default_message = "无法连接模型服务"
    default_suggestion = "请检查网络、Base URL 或模型服务状态"
    retryable = True


class ProviderServiceUnavailableError(ProviderError):
    code = "provider_service_unavailable"
    default_message = "模型服务暂时不可用"
    default_suggestion = "请稍后重试"
    retryable = True


class RuntimeBudgetExceeded(AgentError):
    code = "runtime_budget_exceeded"


class RuntimeContinuationRequired(RuntimeBudgetExceeded):
    code = "runtime_continuation_required"
    default_message = "本轮模型调用次数已达到上限，可以继续完成"
    default_suggestion = "点击继续，系统会保留已完成的工具结果并从中断点续写"
    retryable = True

    def __init__(
        self,
        *,
        generated: list[Any],
        usage: dict[str, Any],
        tool_calls: int,
        model_calls: int,
    ) -> None:
        super().__init__()
        self.generated = generated
        self.usage = usage
        self.tool_calls = tool_calls
        self.model_calls = model_calls


class ToolPolicyError(AgentError):
    code = "tool_policy_error"


class ToolNotAvailableError(ToolPolicyError):
    code = "tool_not_available"
    default_message = "指定工具不存在或尚未启用"
    default_suggestion = "请从页面显示的工具列表中重新选择"


class RequiredToolNotCalledError(ToolPolicyError):
    code = "required_tool_not_called"
    default_message = "当前模型未能按要求调用指定工具"
    default_suggestion = "请确认当前模型支持工具调用，或更换模型后重试"

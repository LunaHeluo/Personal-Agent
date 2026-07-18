import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from starter_agent.agent.memory import AutoMemoryWriter
from starter_agent.domain.models import Message, ModelResponse
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.providers.base import Provider
from starter_agent.providers.mock import MockProvider
from starter_agent.settings import MemoryConfig


class MemoryResponseProvider(Provider):
    name = "memory-test"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def complete(self, messages, model, tools, **kwargs):
        return ModelResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            provider=self.name,
            model=model,
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        )

    async def health(self, model: str) -> tuple[bool, str]:
        return True, "ok"


def store(tmp_path) -> SQLiteSessionStore:
    return SQLiteSessionStore("sqlite:///auto-memory.db", tmp_path)


async def run_writer(tmp_path, user_message: str, memories: list[dict]):
    target_store = store(tmp_path)
    outcome = await AutoMemoryWriter(
        target_store, MemoryConfig()
    ).analyze_and_store(
        provider=MemoryResponseProvider({"memories": memories}),
        model="test-model",
        user_message=user_message,
        assistant_response="主回复已经完成",
        source_message_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
    )
    return target_store, outcome


async def test_auto_memory_writes_grounded_user_preference(tmp_path) -> None:
    evidence = "我希望长期在悉尼寻找 AI Agent 工程师岗位"
    target_store, outcome = await run_writer(
        tmp_path,
        evidence,
        [
            {
                "key": "target_city",
                "value": "悉尼",
                "category": "preference",
                "confidence": 0.9,
                "expires_in_days": 180,
                "sensitivity": "personal",
                "evidence_quote": evidence,
            }
        ],
    )

    assert len(outcome.created) == 1
    item = target_store.list_memories(active_only=True)[0]
    assert item.key == "target_city"
    assert item.value == "悉尼"
    assert item.source_type == "conversation_inferred"
    assert item.verified_by == "memory_model"
    assert item.confidence == 0.9
    assert item.source_ref.startswith("message:")
    assert outcome.usage["total_tokens"] == 120


async def test_auto_memory_rejects_external_content_secrets_and_fake_evidence(
    tmp_path,
) -> None:
    user_message = (
        "岗位职责：https://example.com 要求 Python。"
        "我的邮箱是 luna@example.com，API key 是 sk-secretvalue。"
    )
    candidates = [
        {
            "key": "job_skill",
            "value": "Python",
            "category": "verified_skill",
            "confidence": 0.9,
            "expires_in_days": 365,
            "sensitivity": "personal",
            "evidence_quote": "岗位职责：https://example.com 要求 Python",
        },
        {
            "key": "email",
            "value": "luna@example.com",
            "category": "profile",
            "confidence": 0.9,
            "expires_in_days": 365,
            "sensitivity": "sensitive",
            "evidence_quote": "我的邮箱是 luna@example.com",
        },
        {
            "key": "invented_city",
            "value": "上海",
            "category": "preference",
            "confidence": 0.9,
            "expires_in_days": 180,
            "sensitivity": "personal",
            "evidence_quote": "我希望在上海工作",
        },
    ]

    target_store, outcome = await run_writer(tmp_path, user_message, candidates)

    assert outcome.rejected_count == 3
    assert target_store.list_memories() == []


async def test_auto_memory_does_not_treat_assistant_email_body_as_user_fact(
    tmp_path,
) -> None:
    target_store = store(tmp_path)
    outcome = await AutoMemoryWriter(
        target_store, MemoryConfig()
    ).analyze_and_store(
        provider=MemoryResponseProvider(
            {
                "memories": [
                    {
                        "key": "interview_date",
                        "value": "2026-07-20",
                        "category": "application_state",
                        "confidence": 0.9,
                        "expires_in_days": 30,
                        "sensitivity": "personal",
                        "evidence_quote": "面试时间是 2026-07-20",
                    }
                ]
            }
        ),
        model="test-model",
        user_message="请读取面试邀请并总结",
        assistant_response="邮件正文：面试时间是 2026-07-20",
        source_message_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
    )

    assert outcome.rejected_count == 1
    assert target_store.list_memories() == []


async def test_auto_memory_never_overwrites_user_confirmed_memory(tmp_path) -> None:
    target_store = store(tmp_path)
    target_store.create_memory(
        key="target_city",
        value="上海",
        category="preference",
        source_ref="user:memory-panel",
        source_type="user_confirmed",
        confidence=1.0,
        verified_by="user",
        expires_at=datetime.now(UTC) + timedelta(days=180),
        sensitivity="personal",
    )
    evidence = "我现在也考虑深圳的岗位"
    outcome = await AutoMemoryWriter(
        target_store, MemoryConfig()
    ).analyze_and_store(
        provider=MemoryResponseProvider(
            {
                "memories": [
                    {
                        "key": "target_city",
                        "value": "深圳",
                        "category": "preference",
                        "confidence": 0.9,
                        "expires_in_days": 180,
                        "sensitivity": "personal",
                        "evidence_quote": evidence,
                    }
                ]
            }
        ),
        model="test-model",
        user_message=evidence,
        assistant_response="好的",
        source_message_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
    )

    assert len(outcome.preserved) == 1
    assert target_store.list_memories()[0].value == "上海"


async def test_application_auto_memory_runs_after_main_reply_without_blocking(
    application, monkeypatch
) -> None:
    application.settings.providers["memory-background-test"] = (
        application.settings.providers["mock"].model_copy(deep=True)
    )
    memory_started = asyncio.Event()
    release_memory = asyncio.Event()
    main_requests = []

    async def complete(self, messages, model, tools, **kwargs):
        if messages[0].content.startswith("你是 Starter Agent 的后台长期记忆整理器"):
            memory_started.set()
            await release_memory.wait()
            evidence = "我长期偏好在上海工作"
            return ModelResponse(
                content=json.dumps(
                    {
                        "memories": [
                            {
                                "key": "target_city",
                                "value": "上海",
                                "category": "preference",
                                "confidence": 0.9,
                                "expires_in_days": 180,
                                "sensitivity": "personal",
                                "evidence_quote": evidence,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                provider=self.name,
                model=model,
            )
        main_requests.append(messages)
        return ModelResponse(
            content="主回复",
            provider=self.name,
            model=model,
        )

    monkeypatch.setattr(MockProvider, "complete", complete)

    result = await application.chat(
        "我长期偏好在上海工作", provider_name="memory-background-test"
    )

    assert result.content == "主回复"
    assert application.store.list_memories() == []
    await asyncio.wait_for(memory_started.wait(), timeout=1)
    assert application.store.list_memories() == []
    release_memory.set()
    await application.wait_for_background_tasks()
    assert application.store.list_memories()[0].value == "上海"

    application.settings.memory.auto_write_enabled = False
    await application.chat("新会话问题", provider_name="memory-background-test")
    second_context = main_requests[-1]
    assert any(
        message.role == "system"
        and "Long-term memory" in message.content
        and "上海" in message.content
        for message in second_context
    )

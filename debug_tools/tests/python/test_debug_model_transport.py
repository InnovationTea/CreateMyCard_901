"""调试 Agent 的模型传输配置回归。"""

import pytest
from config.config import Settings
from custom.official_http_client import OfficialHttpCompletion, OfficialHttpToolCall
from debug_tools.end_to_end_debug.backend.debug_agent import platform_chat_client as model_adapter
from debug_tools.end_to_end_debug.backend.debug_agent.agent import DebugAgentSession
from debug_tools.end_to_end_debug.backend.debug_agent.platform_chat_client import PlatformChatClient
from models.generation import ModelRequestContext


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "deepseek_official_http_url": "https://api.deepseek.com/chat/completions",
        "deepseek_official_http_api_key": "test-key",
        "deepseek_official_http_model": "deepseek-flash",
        "deepseek_official_http_temperature": 0.7,
        "deepseek_official_http_top_p": 0.9,
        "deepseek_official_http_max_tokens": 8192,
        "deepseek_official_http_enable_thinking": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_debug_agent_uses_official_http_thinking_setting_when_selected():
    debug_settings = type("DebugSettingsStub", (), {"request_timeout_seconds": 1.0})()
    session = object.__new__(DebugAgentSession)
    session.debug_settings = debug_settings
    settings = _settings(
        openai_master_client="deepseek_official_http",
        deepseek_official_http_enable_thinking=True,
    )

    client = session._default_model_client(None, settings)

    assert isinstance(client, PlatformChatClient)
    assert client.thinking_mode == "high"


@pytest.mark.asyncio
async def test_platform_chat_client_reuses_official_http_api_key_for_tool_calls(monkeypatch):
    settings = _settings(
        openai_master_client="deepseek_official_http",
        model_queue_timeout_seconds=1.0,
        model_request_timeout_seconds=1.0,
    )
    captured: dict[str, object] = {}

    async def fake_request_official_http(**kwargs):
        captured.update(kwargs)
        return OfficialHttpCompletion(
            tool_calls=(
                OfficialHttpToolCall(
                    id="call-1",
                    name="load_skill",
                    arguments='{"resourceId":"instructions"}',
                ),
            ),
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(model_adapter, "request_official_http", fake_request_official_http)
    client = PlatformChatClient(
        settings,
        ModelRequestContext(
            session_id="s1",
            interaction_id="i1",
            device_id="d1",
            country_code="CN",
            app_version="1.0",
            app_name="debug",
        ),
        thinking_mode="disable",
        request_timeout=1.0,
    )

    completion = await client.complete(
        [{"role": "user", "content": "create a card"}],
        tools=[{"type": "function", "function": {"name": "load_skill"}}],
        tool_choice="auto",
        max_tokens=128,
        enable_thinking=False,
    )

    assert completion.tool_calls[0].name == "load_skill"
    assert captured.get("api_key") == "test-key"
    assert captured.get("url") == "https://api.deepseek.com/chat/completions"
    assert captured.get("thinking_enabled") is False

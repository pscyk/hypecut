"""Provider-gateway tests (llm.py). Kimi HTTP is mocked — no network, no keys."""
from __future__ import annotations

import json

import pytest

from clipper.config import Settings
from clipper import llm


class _Resp:
    def __init__(self, payload: dict, status: int = 200, text: str = ""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


def _kimi(s: Settings) -> Settings:
    s.provider, s.kimi_api_key = "kimi", "test-key"
    s.kimi_base_url = "https://api.moonshot.ai/v1"  # pin: don't inherit the user's .env
    return s


def _tool_reply(args: dict) -> _Resp:
    return _Resp({"choices": [{"message": {"role": "assistant", "content": None,
                                           "tool_calls": [{"id": "c1", "type": "function",
                                                           "function": {"name": "submit_clips",
                                                                        "arguments": json.dumps(args)}}]}}]})


def _prose_reply(text: str) -> _Resp:
    return _Resp({"choices": [{"message": {"role": "assistant", "content": text}}]})


# --- provider selection & model tiers ---

def test_provider_defaults_to_anthropic():
    s = Settings()
    assert llm.provider(s) == "anthropic"
    assert llm.supports_batch(s) is True


def test_kimi_provider_disables_batch():
    assert llm.supports_batch(_kimi(Settings())) is False


def test_resolve_model_kimi_maps_claude_defaults_to_kimi_tiers():
    s = _kimi(Settings())
    assert s.pick_model.startswith("claude")  # the stock default
    assert llm.resolve_model(s, "pick_model") == s.kimi_pick_model == "kimi-k3"
    assert llm.resolve_model(s, "jury_model") == "kimi-k2.6"
    assert llm.resolve_model(s, "discover_model") == "kimi-k2.6"
    assert llm.resolve_model(s, "model") == "kimi-k2.6"


def test_resolve_model_respects_explicit_non_claude_model():
    s = _kimi(Settings())
    s.jury_model = "kimi-k2.7-code-highspeed"  # explicit override: kept
    assert llm.resolve_model(s, "jury_model") == "kimi-k2.7-code-highspeed"


def test_resolve_model_anthropic_unchanged():
    s = Settings()
    assert llm.resolve_model(s, "pick_model") == s.pick_model
    assert llm.resolve_model(s, "jury_model") == s.jury_model


# --- kimi tool_json ---

TOOL = {"name": "submit_clips", "description": "d",
        "input_schema": {"type": "object", "properties": {"clips": {"type": "array"}}}}


def test_kimi_tool_json_happy_path_and_payload_shape(monkeypatch):
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, body=json)
        return _tool_reply({"clips": [{"start": 1.0, "end": 20.0, "title": "x"}]})

    monkeypatch.setattr("requests.post", fake_post)
    s = _kimi(Settings())
    out = llm.tool_json(s, model="kimi-k3", tool=TOOL, prompt="pick clips",
                        system="sys", b64="QUJD", max_tokens=500)

    assert out["clips"][0]["title"] == "x"
    assert sent["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    body = sent["body"]
    assert body["model"] == "kimi-k3"
    # anthropic-shaped tool converted to OpenAI function shape + forced choice
    assert body["tools"] == [{"type": "function", "function": {
        "name": "submit_clips", "description": "d",
        "parameters": {"type": "object", "properties": {"clips": {"type": "array"}}}}}]
    assert body["tool_choice"] == "required"  # k3: specified tool_choice is rejected
    # system + multimodal content in OpenAI form
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    content = body["messages"][1]["content"]
    assert content[0] == {"type": "image_url",
                          "image_url": {"url": "data:image/jpeg;base64,QUJD"}}
    assert content[1]["type"] == "text"
    # k3 always thinks; keep effort low so reasoning can't eat the output budget,
    # and force the call with "required" (k3 rejects a specified tool_choice)
    assert body["reasoning_effort"] == "low"
    assert "thinking" not in sent["body"]
    assert body["tool_choice"] == "required"


def test_kimi_k2x_disables_thinking(monkeypatch):
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["body"] = json
        return _tool_reply({"clips": []})

    monkeypatch.setattr("requests.post", fake_post)
    llm.tool_json(_kimi(Settings()), model="kimi-k2.6", tool=TOOL, prompt="p")
    assert sent["body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in sent["body"]
    assert sent["body"]["tool_choice"] == {"type": "function", "function": {"name": "submit_clips"}}


def test_kimi_tool_json_falls_back_to_prose_json(monkeypatch):
    # some vision models ignore tool_choice and answer in prose
    monkeypatch.setattr("requests.post",
                        lambda *a, **k: _prose_reply('Sure! ```json\n{"clips": [{"start": 5}]}\n```'))
    out = llm.tool_json(_kimi(Settings()), model="kimi-k2.6", tool=TOOL, prompt="p")
    assert out["clips"] == [{"start": 5}]


def test_kimi_missing_key_raises():
    s = Settings()
    s.provider, s.kimi_api_key = "kimi", ""
    with pytest.raises(llm.LLMError, match="KIMI_API_KEY"):
        llm.tool_json(s, model="kimi-k3", tool=TOOL, prompt="p")


def test_kimi_http_error_raises(monkeypatch):
    monkeypatch.setattr("requests.post",
                        lambda *a, **k: _Resp({"error": {"message": "bad key"}}, status=401,
                                              text='{"error": {"message": "bad key"}}'))
    with pytest.raises(llm.LLMError, match="401"):
        llm.tool_json(_kimi(Settings()), model="kimi-k3", tool=TOOL, prompt="p")


def test_kimi_text_json(monkeypatch):
    monkeypatch.setattr("requests.post",
                        lambda *a, **k: _prose_reply('Here you go: {"recommended_hooks": ["a"], "notes": "n"}'))
    out = llm.text_json(_kimi(Settings()), model="kimi-k2.6", prompt="brief")
    assert out["recommended_hooks"] == ["a"]


# --- json extraction ---

def test_extract_json_tolerates_fences_and_prose():
    assert llm._extract_json('blah ```json\n{"a": 1}\n``` tail') == {"a": 1}
    with pytest.raises(llm.LLMError):
        llm._extract_json("no json here")

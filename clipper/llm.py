"""Provider-agnostic LLM gateway — one call surface, switchable backend.

CLIPPER_PROVIDER=anthropic (default) | kimi.

  anthropic  Claude via the anthropic SDK. Supports the Batches API (50% off,
             async) for vision fan-outs — jury / vision-discover use it.
  kimi       Moonshot AI's Kimi, OpenAI-compatible chat/completions via plain
             HTTPS (requests). Native vision on kimi-k3/kimi-k2.6. NO batch API
             — fan-outs run as parallel live calls instead.

Cost-tier defaults mirror the anthropic setup: the taste-critical single pick
call gets the flagship (kimi-k3 ~ opus), the per-candidate vision fan-outs get
the cheaper general model (kimi-k2.6 ~ haiku). Override with KIMI_PICK_MODEL /
KIMI_JURY_MODEL / KIMI_DISCOVER_MODEL / KIMI_MODEL.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .config import Settings


class LLMError(RuntimeError):
    """Any provider failure (missing key, HTTP error, unparseable reply)."""


def provider(s: Settings) -> str:
    return (s.provider or "anthropic").strip().lower()


def supports_batch(s: Settings) -> bool:
    """Only anthropic has a Batches API; kimi fan-outs run live + parallel."""
    return provider(s) == "anthropic"


def resolve_model(s: Settings, task: str) -> str:
    """The model for a task ('model' | 'pick_model' | 'jury_model' | 'discover_model').

    On kimi, an unset or claude-named per-task model maps to the kimi tier
    default; an explicitly-set non-claude model is respected on either provider.
    """
    configured = getattr(s, task, "") or s.model
    if provider(s) == "kimi" and (not configured or configured.startswith("claude")):
        return getattr(s, f"kimi_{task}", "") or s.kimi_model
    return configured


def _extract_json(text: str) -> dict:
    """Parse a JSON object from a model reply, tolerating code fences / prose."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise LLMError("no JSON object in reply")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMError(f"unparseable JSON in reply: {e}")


# ---------------------------------------------------------------------------
# public call surface


def tool_json(s: Settings, *, model: str, tool: dict, prompt: str,
              system: Optional[str] = None, b64: Optional[str] = None,
              max_tokens: int = 2000) -> dict:
    """Forced-tool call returning the tool's input as a dict.

    `tool` is in ANTHROPIC shape ({name, description, input_schema}); the kimi
    path converts it to OpenAI function shape. On kimi, if the model answers
    with prose instead of a tool call (some vision models do), we fall back to
    extracting JSON from the reply text.

    The reply is validated against the schema's `required` keys: models
    occasionally return a tool call with EMPTY arguments ({}), and callers
    subscript required keys unguarded — one flaky reply would otherwise kill
    a whole render job with a bare KeyError. Missing keys retry the call
    (transient by observation: the very next attempt succeeded) and only
    raise after the budget is spent.
    """
    required = tool.get("input_schema", {}).get("required", [])
    data: dict = {}
    for attempt in range(3):
        if provider(s) == "kimi":
            data = _kimi_tool_json(s, model=model, tool=tool, prompt=prompt,
                                   system=system, b64=b64, max_tokens=max_tokens)
        else:
            data = _anthropic_tool_json(model=model, tool=tool, prompt=prompt,
                                        system=system, b64=b64, max_tokens=max_tokens)
        missing = [k for k in required if k not in data]
        if not missing:
            return data
        print(f"[llm] tool '{tool['name']}' reply missing required {missing} "
              f"(attempt {attempt + 1}/3); retrying")
    raise LLMError(
        f"tool '{tool['name']}' reply missing required keys {required} "
        f"after 3 attempts: {str(data)[:200]}")


def text_json(s: Settings, *, model: str, prompt: str,
              system: Optional[str] = None, max_tokens: int = 1000) -> dict:
    """Plain prompt -> parsed JSON object (for free-form synthesis like the trend brief)."""
    if provider(s) == "kimi":
        return _extract_json(_kimi_chat(s, model=model, prompt=prompt,
                                        system=system, max_tokens=max_tokens))
    import anthropic

    kw: dict[str, Any] = {}
    if system:
        kw["system"] = system
    msg = anthropic.Anthropic().messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}], **kw)
    return _extract_json("".join(b.text for b in msg.content if b.type == "text"))


# ---------------------------------------------------------------------------
# anthropic backend


def _anthropic_tool_json(*, model: str, tool: dict, prompt: str,
                         system: Optional[str], b64: Optional[str],
                         max_tokens: int) -> dict:
    import anthropic

    content: list[dict] = []
    if b64:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": prompt})
    kw: dict[str, Any] = {}
    if system:
        kw["system"] = system
    msg = anthropic.Anthropic().messages.create(
        model=model, max_tokens=max_tokens,
        tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": content}], **kw)
    try:
        return next(b.input for b in msg.content if b.type == "tool_use")
    except StopIteration:
        raise LLMError("anthropic reply had no tool_use block")


# ---------------------------------------------------------------------------
# kimi backend (OpenAI-compatible)


def _openai_tool(tool: dict) -> dict:
    return {"type": "function", "function": {
        "name": tool["name"], "description": tool.get("description", ""),
        "parameters": tool["input_schema"]}}


def _openai_messages(prompt: str, system: Optional[str], b64: Optional[str]) -> list[dict]:
    content: list[dict] = []
    if b64:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": prompt})
    msgs = ([{"role": "system", "content": system}] if system else [])
    return msgs + [{"role": "user", "content": content}]


def _kimi_request(s: Settings, body: dict) -> dict:
    import requests

    if not s.kimi_api_key:
        raise LLMError("KIMI_API_KEY (or MOONSHOT_API_KEY) is not set")
    r = requests.post(
        f"{s.kimi_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.kimi_api_key}"},
        json=body, timeout=180,
    )
    if r.status_code != 200:
        raise LLMError(f"kimi HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _thinking_off(model: str) -> dict:
    """Keep reasoning from eating the output budget on structured-extraction calls.
    kimi-k2.x accepts a thinking switch; kimi-k3 always thinks but takes
    reasoning_effort=low (docs: low/high/max, default max)."""
    if model.startswith("kimi-k3"):
        return {"reasoning_effort": "low"}
    return {"thinking": {"type": "disabled"}}


def _kimi_chat(s: Settings, *, model: str, prompt: str,
               system: Optional[str] = None, max_tokens: int = 1000) -> str:
    resp = _kimi_request(s, {
        "model": model, "max_tokens": max_tokens,
        "messages": _openai_messages(prompt, system, None)})
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"kimi reply had no message content: {str(resp)[:300]}")


def _kimi_tool_json(s: Settings, *, model: str, tool: dict, prompt: str,
                    system: Optional[str], b64: Optional[str],
                    max_tokens: int) -> dict:
    # kimi-k3 (thinking always on) rejects a SPECIFIED tool_choice
    # ('tool_choice specified is incompatible with thinking enabled') —
    # "required" forces a tool call the same way with only one tool offered.
    tool_choice: Any = {"type": "function", "function": {"name": tool["name"]}}
    if model.startswith("kimi-k3"):
        tool_choice = "required"
    resp = _kimi_request(s, {
        "model": model, "max_tokens": max_tokens,
        "messages": _openai_messages(prompt, system, b64),
        "tools": [_openai_tool(tool)],
        "tool_choice": tool_choice,
        **_thinking_off(model),
    })
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"kimi reply had no message: {str(resp)[:300]}")

    for call in msg.get("tool_calls") or []:
        if call.get("function", {}).get("name") == tool["name"]:
            try:
                return json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError as e:
                raise LLMError(f"kimi tool arguments were not JSON: {e}")
    # fallback: some vision models answer in prose despite tool_choice
    return _extract_json(msg.get("content") or "")

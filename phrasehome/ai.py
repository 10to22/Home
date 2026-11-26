"""One place for the hub's background AI calls (cameras, meal planning, diary, watcher...).
The spoken assistant lives in assistant.py; this is for single questions that return text or
JSON. Uses the assistant's model and API key settings."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

log = logging.getLogger("phrasehome.ai")


class AIUnavailable(RuntimeError):
    pass


def _client(home):
    if home.assistant is not None:
        return home.assistant.client
    if getattr(home, "_ai_client", None) is None:
        try:
            from .local_ai import make_client

            home._ai_client = make_client(home.config.assistant)  # Claude, or local (provider: local)
        except Exception as e:
            raise AIUnavailable(f"AI not available: {e}")
    return home._ai_client


def _model(home) -> str:
    if home.assistant is not None:
        return home.assistant.model  # follows the smart/fast switch
    return home.config.assistant.get("model", "claude-opus-5-5")


def image_block(jpeg: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(jpeg).decode()}}


def ask(home, prompt: str, images: list[bytes] | None = None, system: str = "",
        schema: dict | None = None, effort: str = "low", quick: bool = False, max_tokens: int = 16000) -> Any:
    """Returns text, or a dict when `schema` (a JSON schema) is given.
    quick: use the fast model (Claude's Haiku) whatever the smart/fast switch says, for small
    decisions that hold up listening."""
    content: list[dict] = [image_block(j) for j in images or []]
    content.append({"type": "text", "text": prompt})
    output_config: dict[str, Any] = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    model = _model(home)
    a = home.assistant
    if quick and a is not None and not getattr(a, "local", False):
        model = a.fast_model
    elif quick and a is None and not str(home.config.assistant.get("provider", "claude")).lower() in ("local", "ollama"):
        model = home.config.assistant.get("fast_model", "claude-haiku-5-5")
    kwargs: dict[str, Any] = dict(
        model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": content}],
        output_config=output_config)
    from .assistant import fallback_kw

    kwargs.update(fallback_kw(kwargs["model"]))
    if system:
        kwargs["system"] = system
    try:
        resp = _client(home).beta.messages.create(**kwargs)
    except Exception as e:
        from .local_ai import LocalAIError

        if isinstance(e, LocalAIError):
            raise AIUnavailable(str(e)) from None
        raise
    if resp.stop_reason == "refusal":
        raise AIUnavailable("the AI declined this request")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return json.loads(text) if schema else text


def obj(props: dict[str, Any]) -> dict:
    """A strict JSON-schema object where every property is required."""
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}

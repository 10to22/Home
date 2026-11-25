"""A locally hosted AI for the house: Ollama (https://ollama.com), free and private.

assistant:
  provider: local
  local:
    url: http://localhost:11434    # where Ollama runs. On the Pi, point at a laptop/desktop on
                                   # your Wi-Fi running Ollama (a Pi on its own is very slow)
    model: qwen3:8b                # needs a model that can use tools; qwen3:4b is about twice as fast
    vision_model: qwen2.5vl:7b     # optional, for camera questions (what's at the door, packages)
    timeout: 180

The rest of the house talks to the AI in one format (the one Claude uses); `LocalClient`
answers in that same format, so the assistant, meal planner, diary, watcher and cameras work
unchanged. What a local model can't do: web search (no internet lookups), and it's less
reliable at choosing the right house tool than Claude.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

log = logging.getLogger("phrasehome.local_ai")

DEFAULTS = {"url": "http://localhost:11434", "model": "qwen3:8b", "vision_model": "", "timeout": 180,
            "keep_alive": "30m", "max_tokens": 2048,
            # the house's instructions + tools are ~4-5k tokens: Ollama's default (often 4096) would cut them off
            "num_ctx": 16384}


class LocalAIError(RuntimeError):
    pass


class _Obj:
    """Looks like the SDK's response objects (attributes), and can turn back into a dict."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)

    def model_dump(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def __repr__(self) -> str:
        return f"_Obj({self.__dict__})"


def _f(b: Any, key: str, default: Any = None) -> Any:
    return b.get(key, default) if isinstance(b, dict) else getattr(b, key, default)


def to_ollama(system: Any, messages: list) -> tuple[list[dict], bool]:
    """Claude-style system + messages -> Ollama chat messages. Returns (messages, has_images)."""
    out: list[dict] = []
    if system:
        text = system if isinstance(system, str) else "\n\n".join(str(_f(b, "text", "")) for b in system)
        out.append({"role": "system", "content": text})
    names: dict[str, str] = {}  # tool_use id -> tool name
    has_images = False
    for m in messages:
        role, content = _f(m, "role"), _f(m, "content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        texts, images, calls, tool_msgs = [], [], [], []
        for b in content or []:
            t = _f(b, "type")
            if t == "text":
                texts.append(str(_f(b, "text", "")))
            elif t == "image":
                data = _f(_f(b, "source") or {}, "data")
                if data:
                    images.append(data)
            elif t == "tool_use":
                names[_f(b, "id")] = _f(b, "name")
                calls.append({"function": {"name": _f(b, "name"), "arguments": dict(_f(b, "input") or {})}})
            elif t == "tool_result":
                c = _f(b, "content")
                if isinstance(c, str) or c is None:
                    text = c or ""
                else:
                    text = "\n".join(str(_f(x, "text", "")) for x in c if _f(x, "type") == "text")
                    images += [_f(_f(x, "source") or {}, "data") for x in c if _f(x, "type") == "image"]
                if _f(b, "is_error"):
                    text = "ERROR: " + text
                msg = {"role": "tool", "content": text or "(done)"}
                if names.get(_f(b, "tool_use_id")):
                    msg["tool_name"] = names[_f(b, "tool_use_id")]
                tool_msgs.append(msg)
            # thinking blocks, web search results, fallback markers: nothing to send locally
        if role == "assistant":
            msg = {"role": "assistant", "content": "\n".join(texts)}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        out.extend(tool_msgs)
        images = [i for i in images if i]
        if texts or images:
            msg = {"role": "user", "content": "\n".join(texts) or "Here is the picture."}
            if images:
                msg["images"] = images
                has_images = True
            out.append(msg)
    return out, has_images


def to_ollama_tools(tools: list | None) -> list[dict]:
    """House tools -> Ollama function tools. Hosted tools like web search are skipped."""
    out = []
    for t in tools or []:
        if "input_schema" not in t:
            continue
        out.append({"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                     "parameters": t["input_schema"]}})
    return out


def from_ollama(data: dict) -> _Obj:
    msg = data.get("message") or {}
    blocks: list[_Obj] = []
    text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
    if text:
        blocks.append(_Obj(type="text", text=text))
    for c in msg.get("tool_calls") or []:
        fn = c.get("function") or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        blocks.append(_Obj(type="tool_use", id=f"toolu_local_{uuid.uuid4().hex[:12]}", name=fn.get("name", ""),
                           input=args if isinstance(args, dict) else {}))
    if any(b.type == "tool_use" for b in blocks):
        stop = "tool_use"
    elif data.get("done_reason") == "length":
        stop = "max_tokens"
    else:
        stop = "end_turn"
    usage = _Obj(input_tokens=data.get("prompt_eval_count", 0), output_tokens=data.get("eval_count", 0))
    return _Obj(content=blocks, stop_reason=stop, stop_details=None, model=data.get("model", ""), usage=usage)


class _Messages:
    def __init__(self, client: "LocalClient") -> None:
        self._c = client

    def create(self, *, messages: list, system: Any = None, tools: list | None = None,
               max_tokens: int = 2048, output_config: dict | None = None, **_ignored: Any) -> _Obj:
        return self._c.chat(messages, system, tools, max_tokens, output_config or {})


class LocalClient:
    """Answers `client.messages.create(...)` / `client.beta.messages.create(...)` like the Claude
    SDK does, using a model running in Ollama."""

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.url = str(self.cfg["url"]).rstrip("/")
        self.model = str(self.cfg["model"])
        self.vision_model = str(self.cfg.get("vision_model") or "")
        self.messages = _Messages(self)
        self.beta = _Obj(messages=self.messages)

    def chat(self, messages: list, system: Any, tools: list | None, max_tokens: int,
             output_config: dict) -> _Obj:
        import requests

        msgs, has_images = to_ollama(system, messages)
        model = self.vision_model if has_images and self.vision_model else self.model
        body: dict[str, Any] = {"model": model, "messages": msgs, "stream": False, "think": False,
                                "keep_alive": self.cfg["keep_alive"],
                                "options": {"num_predict": min(int(max_tokens), int(self.cfg["max_tokens"])),
                                            "num_ctx": int(self.cfg["num_ctx"])}}
        otools = to_ollama_tools(tools)
        if otools:
            body["tools"] = otools
        fmt = output_config.get("format") or {}
        if fmt.get("type") == "json_schema":
            body["format"] = fmt["schema"]
        for attempt in range(2):
            try:
                r = requests.post(f"{self.url}/api/chat", json=body, timeout=float(self.cfg["timeout"]))
            except requests.ConnectionError:
                raise LocalAIError(f"can't reach Ollama at {self.url}. Is it running? (open the Ollama app, "
                                   f"or run `ollama serve`)") from None
            except requests.Timeout:
                raise LocalAIError(f"{model} took longer than {self.cfg['timeout']}s. Try a smaller model "
                                   f"(e.g. qwen3:4b) or a faster computer") from None
            if r.ok:
                return from_ollama(r.json())
            err = _error_text(r)
            if "not found" in err and "model" in err:
                raise LocalAIError(f"the model {model} isn't downloaded. Run:  ollama pull {model}")
            if attempt == 0 and "does not support tools" in err and "tools" in body:
                log.warning("%s can't use tools; answering without them (pick a model that can)", model)
                body.pop("tools")
                continue
            if attempt == 0 and "think" in err and "think" in body:  # an older Ollama
                body.pop("think")
                continue
            raise LocalAIError(f"Ollama said: {err}")
        raise LocalAIError("Ollama didn't answer")

    # ---------- for `phrasehome ai-check` ----------
    def installed_models(self) -> list[str]:
        import requests

        try:
            r = requests.get(f"{self.url}/api/tags", timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            raise LocalAIError(f"can't reach Ollama at {self.url} ({e.__class__.__name__}). Is it running? "
                               f"(open the Ollama app, or run `ollama serve`)") from None
        return [m.get("name", "") for m in r.json().get("models", [])]


def _error_text(r) -> str:
    try:
        return str(r.json().get("error") or r.text)
    except ValueError:
        return r.text or f"HTTP {r.status_code}"


def make_client(assistant_cfg: dict):
    """The AI client the config asks for: Claude (default) or a local model."""
    if str(assistant_cfg.get("provider", "claude")).lower() in ("local", "ollama"):
        return LocalClient(assistant_cfg.get("local") or {})
    import anthropic

    return anthropic.Anthropic()


def is_local(assistant_cfg: dict) -> bool:
    return str(assistant_cfg.get("provider", "claude")).lower() in ("local", "ollama")

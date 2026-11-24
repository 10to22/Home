"""Linking the AI: .env loading and `phrasehome ai-check`."""

import os
from types import SimpleNamespace

import pytest

from phrasehome import cli

CFG = """
backends: { dev: { type: log } }
settings: { default_backend: dev }
assistant: { names: [house], model: claude-opus-5-5, effort: low }
commands:
  - { phrases: [hello], action: { backend: dev } }
"""


def test_env_file_is_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PHRASEHOME_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "# secrets\n"
        "ANTHROPIC_API_KEY=“sk-ant-test-1234”\n"   # TextEdit's curly quotes
        "export PHRASEHOME_TOKEN='abc'  # dashboard\n"
        "SPOTIFY_CLIENT_ID=\n", encoding="utf-8")
    keys = cli.load_env(tmp_path, tmp_path)
    assert sorted(keys) == ["ANTHROPIC_API_KEY", "PHRASEHOME_TOKEN"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-1234" and os.environ["PHRASEHOME_TOKEN"] == "abc"


def test_env_file_doesnt_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\n")
    cli.load_env(tmp_path)
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_ai_check_success(tmp_path, monkeypatch, capsys):
    import anthropic

    sent = {}

    class Fake:
        def __init__(self, *a, **k):
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

        def create(self, **kw):
            sent.update(kw)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="linked")])

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxxxxxx-abcd")
    (tmp_path / "config.yaml").write_text(CFG)
    assert cli.main(["-c", str(tmp_path / "config.yaml"), "ai-check"]) == 0
    out = capsys.readouterr().out
    assert "The AI is linked" in out and "sk-ant-...abcd" in out
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
    assert "thinking" not in sent and "temperature" not in sent and "tool_choice" not in sent


def test_ai_check_bad_key(tmp_path, monkeypatch, capsys):
    import anthropic
    import httpx2 as httpx

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=req), body=None)

    class Fake:
        def __init__(self, *a, **k):
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

        def create(self, **kw):
            raise err

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    (tmp_path / "config.yaml").write_text(CFG)
    assert cli.main(["-c", str(tmp_path / "config.yaml"), "ai-check"]) == 1
    assert "the key was refused" in capsys.readouterr().out

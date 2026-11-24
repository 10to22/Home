"""The locally hosted AI (Ollama), against a fake Ollama server: no internet, no API key."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from phrasehome import ai, cli
from phrasehome.assistant import Assistant
from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.local_ai import LocalClient, LocalAIError
from tests.test_assistant import AI_CONFIG
from tests.test_door import Recorder


class FakeOllama:
    def __init__(self):
        self.requests, self.script, self.models = [], [], ["qwen3:8b", "qwen2.5vl:7b"]
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, {"models": [{"name": m} for m in outer.models]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                code, reply = outer.script.pop(0)
                self._send(code, reply)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def say(self, text="", tool_calls=None, code=200):
        msg = {"role": "assistant", "content": text}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.script.append((code, {"model": "qwen3:8b", "message": msg, "done": True, "done_reason": "stop",
                                   "prompt_eval_count": 100, "eval_count": 10}))

    def error(self, code, text):
        self.script.append((code, {"error": text}))


@pytest.fixture
def ollama():
    f = FakeOllama()
    yield f
    f.httpd.shutdown()


@pytest.fixture
def home(tmp_path, ollama):
    cfg = AI_CONFIG + f"""  provider: local
  local: {{ url: "{ollama.url}", model: "qwen3:8b", vision_model: "qwen2.5vl:7b" }}
"""
    (tmp_path / "c.yaml").write_text(cfg)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.voice.dry_run = True
    h.assistant = Assistant(h)
    yield h
    h.close()


def test_assistant_uses_house_tools_through_the_local_model(home, ollama):
    ollama.say(tool_calls=[{"function": {"name": "run_command", "arguments": {"command": "lights_here"}}}])
    ollama.say("Lights are on. Try not to trip over anything.")
    answer = home.assistant.ask("turn the lights on", room="kitchen")
    assert answer == "Lights are on. Try not to trip over anything."
    assert Recorder.calls == [{"node": "kitchen", "channel": "lights", "state": True}]
    first, second = ollama.requests
    assert first["model"] == "qwen3:8b" and first["stream"] is False
    names = [t["function"]["name"] for t in first["tools"]]
    assert "run_command" in names and "web_search" not in names  # no web search locally
    assert first["messages"][0]["role"] == "system" and "house" in first["messages"][0]["content"].lower()
    tool_msg = [m for m in second["messages"] if m["role"] == "tool"]
    assert tool_msg and tool_msg[0]["tool_name"] == "run_command"
    assert second["messages"][-2]["tool_calls"][0]["function"]["name"] == "run_command"


def test_structured_answers_for_the_meal_planner(home, ollama):
    ollama.say(json.dumps({"meals": ["tacos", "pasta"]}))
    out = ai.ask(home, "plan two dinners", schema=ai.obj({"meals": {"type": "array", "items": {"type": "string"}}}))
    assert out == {"meals": ["tacos", "pasta"]}
    assert ollama.requests[0]["format"]["properties"]["meals"]["type"] == "array"


def test_camera_pictures_go_to_the_vision_model(home, ollama):
    ollama.say("A delivery driver with a box.")
    assert ai.ask(home, "who's at the door?", images=[b"\xff\xd8jpeg"]) == "A delivery driver with a box."
    req = ollama.requests[0]
    assert req["model"] == "qwen2.5vl:7b" and req["messages"][-1]["images"]


def test_missing_model_says_how_to_get_it(home, ollama):
    ollama.error(404, 'model "qwen3:8b" not found, try pulling it first')
    with pytest.raises(ai.AIUnavailable, match="ollama pull qwen3:8b"):
        ai.ask(home, "hello")


def test_model_without_tools_still_answers(ollama):
    ollama.error(400, "registry.ollama.ai/library/gemma:2b does not support tools")
    ollama.say("Hi.")
    c = LocalClient({"url": ollama.url, "model": "gemma:2b"})
    r = c.beta.messages.create(messages=[{"role": "user", "content": "hi"}],
                               tools=[{"name": "x", "description": "", "input_schema": {"type": "object"}}])
    assert r.content[0].text == "Hi." and "tools" not in ollama.requests[1]


def test_not_running_is_explained():
    c = LocalClient({"url": "http://127.0.0.1:9", "model": "qwen3:8b"})
    with pytest.raises(LocalAIError, match="Is it running"):
        c.messages.create(messages=[{"role": "user", "content": "hi"}])


def test_thinking_tags_are_not_read_aloud(ollama):
    ollama.say("<think>The user wants the time.</think>It's late. Go to bed.")
    c = LocalClient({"url": ollama.url})
    assert c.messages.create(messages=[{"role": "user", "content": "time?"}]).content[0].text == "It's late. Go to bed."


def test_ai_check_local(tmp_path, ollama, capsys):
    ollama.say("linked")
    (tmp_path / "config.yaml").write_text(f"""
backends: {{ dev: {{ type: log }} }}
settings: {{ default_backend: dev }}
assistant: {{ provider: local, local: {{ url: "{ollama.url}", model: "qwen3:8b" }} }}
commands:
  - {{ phrases: [hello], action: {{ backend: dev }} }}
""")
    assert cli.main(["-c", str(tmp_path / "config.yaml"), "ai-check"]) == 0
    assert "The local AI is linked" in capsys.readouterr().out

"""Speed: speaking while the answer is still being written, and the smart/fast switch."""

import json
import time
import types

import pytest

from phrasehome.assistant import Assistant, SentenceSpeaker, fallback_kw
from tests.test_local_ai import FakeOllama, home, ollama  # noqa: F401  (fixtures)


class FakeVoice:
    def __init__(self):
        self.said, self.hush_count = [], 0

    def speak(self, text, room=None):
        self.said.append((round(time.monotonic(), 3), text))


def test_sentences_are_spoken_as_they_arrive():
    v = FakeVoice()
    sp = SentenceSpeaker(v, "kitchen")
    for piece in ["Rain all ", "day. Take a ", "brolly", ", ya galah! And ", "maybe boots"]:
        sp.feed(piece)
    time.sleep(0.1)
    assert [t for _, t in v.said] == ["Rain all day.", "Take a brolly, ya galah!"]  # before the end
    sp.finish()
    assert [t for _, t in v.said][-1] == "And maybe boots" and sp.spoke


def test_shut_up_stops_the_rest_of_the_answer():
    v = FakeVoice()
    sp = SentenceSpeaker(v, None)
    sp.feed("First thing. ")
    time.sleep(0.05)
    v.hush_count += 1  # "shut up"
    sp.feed("Second thing. Third thing.")
    sp.finish()
    assert [t for _, t in v.said] == ["First thing."]


def test_long_answers_are_cut_to_a_few_sentences():
    v = FakeVoice()
    sp = SentenceSpeaker(v, None, max_sentences=2)
    sp.feed("One. Two. Three. Four.")
    sp.finish()
    assert [t for _, t in v.said] == ["One.", "Two."]


def _stream_lines(ollama, chunks, tool_calls=None):
    """Queue a streamed Ollama reply: several JSON lines, the last with done=true."""
    lines = [{"message": {"role": "assistant", "content": c}, "done": False} for c in chunks]
    last = {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"}
    if tool_calls:
        last["message"]["tool_calls"] = tool_calls
    ollama.script.append((200, "\n".join(json.dumps(x) for x in lines + [last])))


def test_local_streaming_and_think_tags(ollama):
    import http.server  # noqa: F401
    from phrasehome.local_ai import LocalClient

    # FakeOllama sends JSON; for streaming we send pre-joined lines as a JSON string, so unwrap it
    orig = ollama.httpd.RequestHandlerClass.do_POST

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ollama.requests.append(body)
        code, reply = ollama.script.pop(0)
        data = (reply if isinstance(reply, str) else json.dumps(reply)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    ollama.httpd.RequestHandlerClass.do_POST = do_POST
    try:
        _stream_lines(ollama, ["<think>hmm", " ok</think>It's ", "sunny. ", "Go outside."])
        c = LocalClient({"url": ollama.url})
        got = []
        with c.beta.messages.stream(messages=[{"role": "user", "content": "weather?"}]) as st:
            for piece in st.text_stream:
                got.append(piece)
            final = st.get_final_message()
        assert "".join(got) == "It's sunny. Go outside." and ollama.requests[0]["stream"] is True
        assert final.content[0].text == "It's sunny. Go outside." and final.stop_reason == "end_turn"
        _stream_lines(ollama, [], tool_calls=[{"function": {"name": "run_command", "arguments": {"command": "x"}}}])
        with c.messages.stream(messages=[{"role": "user", "content": "lights"}]) as st:
            assert list(st.text_stream) == []
            assert st.get_final_message().content[0].name == "run_command"
    finally:
        ollama.httpd.RequestHandlerClass.do_POST = orig


def test_smart_and_fast(home, ollama):
    a = home.assistant
    assert a.model == "qwen3:8b"
    a.set_mode("fast")
    assert a.model == "qwen3:4b" and home.events.query("ai.mode")[-1]["model"] == "qwen3:4b"
    a.set_mode("smart")
    assert a.model == "qwen3:8b"


def test_claude_fast_mode_is_haiku_without_fallbacks():
    a = Assistant.__new__(Assistant)
    a.local, a.smart_model, a.fast_model, a.mode = False, "claude-opus-5-5", "claude-haiku-5-5", "fast"
    assert a.model == "claude-haiku-5-5" and fallback_kw(a.model) == {}
    a.mode = "smart"
    assert fallback_kw(a.model) == {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


def test_dashboard_ai_switch(home, ollama):
    from phrasehome.web import WebServer

    web = WebServer.__new__(WebServer)
    web.home = home
    out = web.handle_post("/api/ai", {"mode": "fast"})
    assert out == {"mode": "fast", "model": "qwen3:4b"} and home.assistant.mode == "fast"


def test_claude_answers_stream_too(tmp_path):
    """With Claude, the same loop uses client.beta.messages.stream (text_stream + get_final_message)."""
    from types import SimpleNamespace as NS

    from phrasehome.config import load_config
    from phrasehome.dispatcher import Dispatcher
    from tests.test_assistant import AI_CONFIG

    sent = []

    class Stream:
        def __init__(self, **kw):
            sent.append(kw)
            self.text_stream = iter(["Sunny ", "all day. ", "Wear a hat."])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get_final_message(self):
            return NS(content=[NS(type="text", text="Sunny all day. Wear a hat.")], stop_reason="end_turn")

    client = NS(beta=NS(messages=NS(stream=Stream, create=None)))
    (tmp_path / "c.yaml").write_text(AI_CONFIG)
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    a = Assistant(h, client=client)
    pieces = []
    assert a.ask("weather?", room="kitchen", on_text=pieces.append) == "Sunny all day. Wear a hat."
    assert pieces == ["Sunny ", "all day. ", "Wear a hat."]
    assert sent[0]["model"] == "claude-opus-5-5" and sent[0]["fallbacks"] == "default"
    h.close()

"""Sounds-like matching, learning from a second try, learned words priming Whisper, Whisper settings."""

from phrasehome.matcher import Matcher
from phrasehome.speech_tuning import phonetic, phonetic_word, retry_for
from phrasehome.understanding import Aliases, candidates_with_aliases
from tests.test_assistant import home  # noqa: F401  (fixture)


def test_phonetic_keys():
    assert phonetic_word("kitchin") == phonetic_word("kitchen")
    assert phonetic_word("lites") == phonetic_word("lights")
    assert phonetic_word("fone") == phonetic_word("phone")
    assert phonetic_word("fire") != phonetic_word("five")
    assert phonetic("turn on the kitchin lites") == phonetic("turn on the kitchen lights")


def test_sounds_like_match_scores_lower(home):
    s = home.config.settings
    m = Matcher(candidates_with_aliases(home.config), s.match_mode, s.match_threshold, s.block_words)
    hit = m.match("tirn on the kichin lites")
    assert hit is not None and hit.command.name == "room_lights_on" and hit.slots == {"room": "kitchen"}
    assert hit.score < 92  # so risky commands still ask
    assert m.match("cozy time") is None and m.match("light zone") is None
    off = Matcher(candidates_with_aliases(home.config), s.match_mode, s.match_threshold, s.block_words, phonetic=False)
    assert off.match("tirn on the kichin lites") is None


def test_learns_from_a_second_try(home):
    r = retry_for(home)
    r.observe({"kind": "none", "room": "kitchen", "heard": "turn on the kitten lights"})
    r.observe({"kind": "command", "room": "kitchen", "heard": "turn on the kitchen lights",
               "matched": "room_lights_on", "slots": {"room": "kitchen"}})
    taught = Aliases(home.config).all()
    assert taught and taught[0]["heard"] == "turn on the kitten lights" and taught[0]["command"] == "room_lights_on"
    # unrelated sentences aren't linked
    r.observe({"kind": "none", "room": "kitchen", "heard": "what a day"})
    r.observe({"kind": "command", "room": "kitchen", "heard": "lockdown", "matched": "lockdown"})
    assert len(Aliases(home.config).all()) == 1


def test_learned_words_prime_whisper(home):
    from phrasehome.hearing import house_words

    Aliases(home.config).add("turn on the kitten lights", "room_lights_on", {"room": "kitchen"})
    assert "kitchen" in house_words(home.config)


def test_whisper_beam_and_model_choice(home, monkeypatch):
    import sys

    from phrasehome import hearing, speech_tuning

    home.config.settings.whisper_beam = 0
    monkeypatch.setattr(sys, "platform", "darwin")
    assert speech_tuning.whisper_beam(home.config) == 5
    monkeypatch.setattr(speech_tuning, "memory_gb", lambda: 16.0)
    assert hearing.default_whisper_model() == "distil-medium.en"
    monkeypatch.setattr(speech_tuning, "memory_gb", lambda: 8.0)
    assert hearing.default_whisper_model() == "small.en"

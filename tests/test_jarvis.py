"""
Tests for the JARVIS core. These run fully offline (no microphone, no network,
no API keys) by using jarvis.process_command(), which captures spoken output
as text instead of playing audio.

Run:  pytest -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jarvis  # noqa: E402


def responses(query):
    """Run a command and return the joined spoken text (lowercased)."""
    out = jarvis.process_command(query)
    return " ".join(out["responses"]).lower()


# ---------- safe math ----------
def test_safe_math_basic():
    assert jarvis._safe_math("2 + 2") == 4
    assert jarvis._safe_math("15 times 12") == 180
    assert jarvis._safe_math("100 divided by 4") == 25


def test_safe_math_rejects_non_math():
    assert jarvis._safe_math("hello world") is None
    # must not execute arbitrary code
    assert jarvis._safe_math("__import__('os')") is None


def test_extract_number():
    assert jarvis._extract_number("remind me in 30 minutes") == 30
    assert jarvis._extract_number("no numbers here", 5) == 5


# ---------- routing ----------
def test_time_command():
    assert "time is" in responses("what time is it")


def test_math_not_confused_with_time():
    # "15 times 12" contains the substring "time" but must route to math.
    assert "180" in responses("calculate 15 times 12")


def test_help_lists_commands():
    text = responses("help")
    assert "weather" in text and "todo" in text


def test_coin_flip():
    assert responses("flip a coin") in ("heads", "tails")


def test_exit_returns_keep_open_false():
    out = jarvis.process_command("exit")
    assert out["keep_open"] is False


def test_unknown_without_ai_is_graceful():
    # With no Gemini key configured, unknown queries fall back gracefully.
    if not (jarvis.HAS_GEMINI and jarvis.GEMINI_API_KEY):
        text = responses("tell me about quantum entanglement please friend")
        assert "help" in text or "not sure" in text


def test_alarm_parsing():
    assert "alarm set for" in responses("set an alarm for 7:30 am")


def test_currency_bad_input_is_guided():
    # Malformed conversion should guide the user, not crash.
    text = responses("convert to")
    assert "convert" in text or "couldn't" in text


def test_process_command_never_raises():
    # A grab-bag of inputs, including empty, should always return a dict.
    for q in ["", "   ", "!!!", "open ", "translate", "play "]:
        out = jarvis.process_command(q)
        assert isinstance(out, dict) and "responses" in out

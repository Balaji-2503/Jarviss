# 🤖 JARVIS — Voice & Text AI Assistant

A cross-platform personal assistant (Windows / macOS / Linux) inspired by Iron Man's
J.A.R.V.I.S. Talk to it or type to it — it can answer questions, control your system,
manage a to-do list, set reminders, fetch weather and news, do math, and more.

It's **"batteries-optional"**: every heavy dependency and API key is optional. JARVIS
always starts (in text mode at minimum) and simply reports which features are unavailable
instead of crashing.

---

## ✨ Features

| Category | Commands |
|----------|----------|
| **Conversation** | Ask anything — powered by **Groq** (fast) or Google Gemini, with memory of the chat |
| **Apps & Web** | `open youtube`, `open notepad`, `open github.com` |
| **Info** | `what time is it`, `date`, `weather in London`, `news`, `define serendipity`, `wikipedia python` |
| **Productivity** | `todo add buy milk`, `todo list`, `complete task 1`, `remind me to call mom in 30 minutes`, `set timer for 60 seconds`, `set an alarm for 7:30 am`, `take a note ...` |
| **Daily briefing** | `briefing` / `good morning` — time, weather, agenda, tasks & headlines in one go |
| **System** | `system stats`, `volume up/down/mute`, `lock`, `sleep`, `shutdown`, `restart`, `screenshot` |
| **Math & Convert** | `calculate 15 times 12`, `convert 10 usd to eur` |
| **Media** | `play <song>` (YouTube), `play <song> on spotify` |
| **Comms** | `send email`, `send a whatsapp`, `my calendar` / `upcoming events` |
| **Language** | `translate good morning to French` |
| **Clipboard** | `copy <text> to clipboard`, `read clipboard` |
| **Fun & Utils** | `joke`, `flip a coin`, `roll a dice`, `random number`, `generate a password`, `my ip` |
| **Meta** | `help`, `exit` |

---

## 🦾 The J.A.R.V.I.S. HUD (Iron Man style web UI)

A movie-inspired heads-up display: a glowing **arc-reactor** core with rotating
HUD rings, a **radial voice visualizer** that reacts as JARVIS listens and speaks,
live telemetry readouts, a subsystem status panel, and a particle field — all in
your browser.

```bash
python jarvis.py          # opens http://localhost:5000 automatically
```

> **Using PyCharm?** Just open `jarvis.py` and click **Run ▶** — that's it. The
> HUD launches in your browser. Everything (assistant, web server, and the HUD
> page itself) lives in this one file.

- **Talk to it** — click the 🎙 button and speak (voice recognition + speech run
  in the browser via the Web Speech API, so **no microphone drivers or PyAudio
  needed**). Works best in Chrome or Edge.
- **Or type** — a command bar is always there.
- Reminders and alarms pop up on the HUD in real time.
- It's **all one file** (`jarvis.py`) — the assistant, a tiny stdlib web server,
  and the HUD page itself. **No Flask, no separate front-end, nothing to build.**

> The reactor glows **cyan** when idle, **teal** while listening, and **gold**
> while JARVIS is speaking.

---

## 🚀 Quick start

Everything is in **one file** — `jarvis.py`. In PyCharm, just open it and hit **Run ▶**.

```bash
# 1. (Optional) install extras — everything is optional; JARVIS runs without them
pip install -r requirements.txt

# 2. (Optional) add your API keys for full power
cp .env.example .env        # then edit .env and paste in your keys

# 3. Run it
python jarvis.py            # launches the Iron Man HUD in your browser (default)
python jarvis.py --cli      # classic terminal assistant (voice if mic, else text)
python jarvis.py --text     # terminal, force text mode
python jarvis.py --voice    # terminal, force voice mode
python jarvis.py --port 8080          # HUD on a different port
python jarvis.py --no-browser         # HUD without auto-opening a tab
```

On launch, JARVIS prints a **capability check** showing exactly which features are
active based on your installed libraries and API keys.

---

## 🔑 Configuration

All secrets live in a `.env` file (never commit it — it's git-ignored). Copy
`.env.example` to `.env` and fill in whatever you have:

- **AI brain** — JARVIS can think with **Groq** or **Gemini**. Set `AI_PROVIDER` to `groq`, `gemini`, or `auto` (default — prefers Groq for speed, falls back to Gemini):
  - `GROQ_API_KEY` — [Groq Console](https://console.groq.com/keys) — very fast; needs no extra Python package. Model via `GROQ_MODEL` (default `llama-3.3-70b-versatile`).
  - `GEMINI_API_KEY` — [Google AI Studio](https://aistudio.google.com/app/apikey) — needs `google-generativeai`. Model via `GEMINI_MODEL`.
- `OPENWEATHER_API_KEY` — [OpenWeatherMap](https://openweathermap.org/api) — enables `weather`.
- `GNEWS_API_KEY` — [GNews](https://gnews.io) — real headlines for `news` (otherwise Gemini is used).
- `WOLFRAM_APP_ID` — [WolframAlpha](https://developer.wolframalpha.com) — advanced math.

Preferences (assistant name, how it addresses you, speech rate, custom app shortcuts)
are stored in `config.json` inside your user data folder:
`%APPDATA%\Jarvis` on Windows, `~/.jarvis` on macOS/Linux.

---

## 🎤 Voice mode notes

Voice input needs `SpeechRecognition` **and** `PyAudio` (for microphone access). PyAudio
can be fiddly to install:

- **Windows:** `pip install pipwin && pipwin install pyaudio`
- **macOS:** `brew install portaudio && pip install pyaudio`
- **Linux:** `sudo apt install portaudio19-dev && pip install pyaudio`

No microphone? No problem — JARVIS automatically falls back to text mode.

---

## 🗂️ Project layout

```
jarvis.py          # EVERYTHING — assistant, web server, and the HUD page (one file)
tests/             # offline pytest suite (no mic, network, or keys needed)
requirements.txt   # optional dependencies with install notes
.env.example       # template for API keys
.gitignore         # keeps secrets & runtime files out of git
```

Two ways to run the single file:

- **`python jarvis.py`** → the Iron Man browser HUD (default; best voice experience).
- **`python jarvis.py --cli`** → the classic terminal/desktop app (voice or text).

## 🧪 Tests

```bash
pip install pytest
pytest -q
```

The suite runs fully offline by driving `jarvis.process_command()`, which
captures spoken output as text instead of playing audio.

---

## ⚠️ Notes

- System commands like `shutdown`/`restart` do exactly what they say — use with care.
- Voice recognition uses Google's free web API and needs an internet connection.
- This is a personal/educational project, not a production security tool.

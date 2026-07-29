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
| **Conversation** | Ask anything — powered by Google Gemini, with memory of the chat |
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
python server.py          # opens http://localhost:5000 automatically
```

- **Talk to it** — click the 🎙 button and speak (voice recognition + speech run
  in the browser via the Web Speech API, so **no microphone drivers or PyAudio
  needed**). Works best in Chrome or Edge.
- **Or type** — a command bar is always there.
- Reminders and alarms pop up on the HUD in real time.
- The whole backend uses only Python's standard library — **no Flask required.**

> The reactor glows **cyan** when idle, **teal** while listening, and **gold**
> while JARVIS is speaking.

---

## 🚀 Quick start

```bash
# 1. Install dependencies (install only what you need — all are optional)
pip install -r requirements.txt

# 2. Add your API keys (optional but recommended for full power)
cp .env.example .env
#   then edit .env and paste in your keys

# 3. Run it
python jarvis.py            # auto: voice if a mic is available, else text
python jarvis.py --text     # force text mode (no microphone needed)
python jarvis.py --voice    # force voice mode
python jarvis.py --no-gui   # run without the small status window
```

On launch, JARVIS prints a **capability check** showing exactly which features are
active based on your installed libraries and API keys.

---

## 🔑 Configuration

All secrets live in a `.env` file (never commit it — it's git-ignored). Copy
`.env.example` to `.env` and fill in whatever you have:

- `GEMINI_API_KEY` — [Google AI Studio](https://aistudio.google.com/app/apikey) — enables the conversational "brain".
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
jarvis.py          # the assistant core + CLI (voice/text, all features)
server.py          # stdlib web server that bridges the browser to the core
web/index.html     # the Iron Man style HUD (self-contained HTML/CSS/JS)
tests/             # offline pytest suite (no mic, network, or keys needed)
requirements.txt   # optional dependencies with install notes
.env.example       # template for API keys
.gitignore         # keeps secrets & runtime files out of git
```

Two ways to run JARVIS:

- **`python server.py`** → the browser HUD (recommended; best voice experience).
- **`python jarvis.py`** → the classic terminal/desktop app (voice or text).

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

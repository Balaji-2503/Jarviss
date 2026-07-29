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
| **Productivity** | `todo add buy milk`, `todo list`, `complete task 1`, `remind me to call mom in 30 minutes`, `set timer for 60 seconds`, `take a note ...` |
| **System** | `system stats`, `volume up/down/mute`, `lock`, `sleep`, `shutdown`, `restart`, `screenshot` |
| **Math & Convert** | `calculate 15 times 12`, `convert 10 usd to eur` |
| **Media** | `play <song>` (YouTube) |
| **Fun & Utils** | `joke`, `flip a coin`, `roll a dice`, `random number`, `generate a password`, `my ip` |
| **Meta** | `help`, `exit` |

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
jarvis.py          # the entire assistant (single file, well-sectioned)
requirements.txt   # optional dependencies with install notes
.env.example       # template for API keys
.gitignore         # keeps secrets & runtime files out of git
```

---

## ⚠️ Notes

- System commands like `shutdown`/`restart` do exactly what they say — use with care.
- Voice recognition uses Google's free web API and needs an internet connection.
- This is a personal/educational project, not a production security tool.

"""
JARVIS - A cross-platform, voice + text AI assistant.

This is a "batteries-optional" assistant: every heavy dependency is imported
lazily/optionally so JARVIS still starts (in text mode at minimum) even when a
library or API key is missing. Missing capabilities are reported instead of
crashing the whole program.

Run:
    python jarvis.py            # auto: voice if a mic + speech libs exist, else text
    python jarvis.py --text     # force text mode (no microphone needed)
    python jarvis.py --voice    # force voice mode
    python jarvis.py --no-gui   # don't open the Tk status window

Configuration lives in a `.env` file (see `.env.example`).
"""

import os
import sys
import json
import time
import random
import string
import platform
import datetime
import argparse
import threading
import webbrowser

# --------------------------------------------------------------------------
# Optional dependencies. Each is guarded so a missing package never prevents
# JARVIS from starting; the relevant feature simply reports being unavailable.
# --------------------------------------------------------------------------
def _try_import(name):
    try:
        return __import__(name)
    except Exception:
        return None


requests = _try_import("requests")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # .env support is optional

try:
    import speech_recognition as sr
    HAS_SR = True
except Exception:
    sr = None
    HAS_SR = False

try:
    import pyttsx3
    HAS_TTS = True
except Exception:
    pyttsx3 = None
    HAS_TTS = False

try:
    import wikipedia
    HAS_WIKI = True
except Exception:
    wikipedia = None
    HAS_WIKI = False

try:
    import psutil
    HAS_PSUTIL = True
except Exception:
    psutil = None
    HAS_PSUTIL = False

try:
    import pyautogui
    HAS_PYAUTOGUI = True
except Exception:
    pyautogui = None
    HAS_PYAUTOGUI = False

try:
    import pyjokes
    HAS_JOKES = True
except Exception:
    pyjokes = None
    HAS_JOKES = False

try:
    import pywhatkit
    HAS_PYWHATKIT = True
except Exception:
    pywhatkit = None
    HAS_PYWHATKIT = False

try:
    import wolframalpha
    HAS_WOLFRAM = True
except Exception:
    wolframalpha = None
    HAS_WOLFRAM = False

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except Exception:
    genai = None
    HAS_GEMINI = False

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    HAS_SPOTIFY = True
except Exception:
    spotipy = None
    HAS_SPOTIFY = False

try:
    import pyperclip
    HAS_CLIPBOARD = True
except Exception:
    pyperclip = None
    HAS_CLIPBOARD = False

try:
    from google.oauth2.credentials import Credentials  # noqa: F401
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    HAS_GCAL = True
except Exception:
    HAS_GCAL = False


# ================= CONFIGURATION =================
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# Store per-user data in a sensible location on every OS.
if IS_WINDOWS and os.environ.get("APPDATA"):
    CONFIG_DIR = os.path.join(os.environ["APPDATA"], "Jarvis")
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".jarvis")
os.makedirs(CONFIG_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TODO_FILE = os.path.join(CONFIG_DIR, "todos.json")
REMINDER_FILE = os.path.join(CONFIG_DIR, "reminders.json")

DEFAULT_CONFIG = {
    "assistant_name": "Jarvis",
    "user_title": "Sir",
    "voice_rate": 180,
    "apps": {
        "youtube": "https://www.youtube.com",
        "github": "https://www.github.com",
        "google": "https://www.google.com",
        "gmail": "https://mail.google.com",
    },
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            # merge defaults so new keys appear for old configs
            merged = dict(DEFAULT_CONFIG)
            merged.update(data)
            merged["apps"] = {**DEFAULT_CONFIG["apps"], **data.get("apps", {})}
            return merged
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"Could not save config: {e}")


config = load_config()
ASSISTANT_NAME = config.get("assistant_name", "Jarvis")
USER_TITLE = config.get("user_title", "Sir")


def env(key, default=""):
    return os.environ.get(key, default)


# ---- Secrets / API keys (all optional, read from environment / .env) ----
GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-1.5-flash")
# Groq (fast open-model inference via an OpenAI-compatible API). No SDK needed.
GROQ_API_KEY = env("GROQ_API_KEY")
GROQ_MODEL = env("GROQ_MODEL", "llama-3.3-70b-versatile")
# Which brain to use when both are set: "groq", "gemini", or "auto" (Groq first).
AI_PROVIDER = env("AI_PROVIDER", "auto").lower()
WEATHER_API_KEY = env("OPENWEATHER_API_KEY")
WOLFRAM_APP_ID = env("WOLFRAM_APP_ID")
EMAIL_SENDER = env("EMAIL_SENDER")
EMAIL_PASSWORD = env("EMAIL_PASSWORD")
DEFAULT_CITY = env("DEFAULT_CITY", "")
SPOTIFY_CLIENT_ID = env("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = env("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = env("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")

# --------------------------------------------------------------------------
# Output plumbing. speak() normally prints + speaks aloud. When the web layer
# (or a test) is capturing a single command, spoken lines are collected into a
# thread-local buffer and returned instead. Proactive messages (reminders,
# alarms) go onto a shared event queue that the web UI can poll.
# --------------------------------------------------------------------------
import collections  # noqa: E402

_capture = threading.local()
_event_queue = collections.deque(maxlen=50)
_event_lock = threading.Lock()


def push_event(text):
    """Queue a proactive message (reminder/alarm) for the web UI to pick up."""
    with _event_lock:
        _event_queue.append({
            "text": text,
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
        })


def drain_events():
    with _event_lock:
        items = list(_event_queue)
        _event_queue.clear()
    return items

# ================= SPEECH ENGINE (lazy) =================
_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    global _engine
    if not HAS_TTS:
        return None
    with _engine_lock:
        if _engine is None:
            try:
                _engine = pyttsx3.init("sapi5") if IS_WINDOWS else pyttsx3.init()
                voices = _engine.getProperty("voices")
                if voices:
                    _engine.setProperty("voice", voices[0].id)
                _engine.setProperty("rate", config.get("voice_rate", 180))
            except Exception as e:
                print(f"TTS init failed: {e}")
                _engine = None
    return _engine


def speak(text):
    """Say something out loud, print it, and/or capture it for the web layer."""
    print(f"{ASSISTANT_NAME.upper()}: {text}")
    buf = getattr(_capture, "buffer", None)
    if buf is not None:
        # We're servicing a captured command (web/test): collect, don't speak.
        buf.append(text)
        return
    engine = _get_engine()
    if engine is not None:
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"(speech error: {e})")


# ================= AI BRAIN (multi-provider, lazy, with memory) =================
# Two interchangeable backends: Groq (fast open models via an OpenAI-compatible
# REST API — needs only `requests`) and Google Gemini. Both keep conversation
# memory. ask_ai() picks a provider from what's configured + AI_PROVIDER.

def _system_prompt():
    return (
        f"You are {ASSISTANT_NAME}, a concise, helpful voice assistant "
        f"inspired by Iron Man's AI. Address the user as '{USER_TITLE}'. "
        "Keep answers short and speakable unless asked for detail."
    )


def has_groq():
    return bool(GROQ_API_KEY) and requests is not None


def has_gemini():
    return HAS_GEMINI and bool(GEMINI_API_KEY)


def active_ai_provider():
    """Resolve which brain to use given config + AI_PROVIDER preference."""
    if AI_PROVIDER == "groq":
        return "groq" if has_groq() else None
    if AI_PROVIDER == "gemini":
        return "gemini" if has_gemini() else None
    # auto: prefer Groq (faster), fall back to Gemini
    if has_groq():
        return "groq"
    if has_gemini():
        return "gemini"
    return None


# ---- Groq ----
_groq_history = []  # OpenAI-style [{"role","content"}, ...]


def _ask_groq(prompt):
    _groq_history.append({"role": "user", "content": prompt})
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "system", "content": _system_prompt()}]
                            + _groq_history[-12:],  # keep recent context
                "temperature": 0.6,
                "max_tokens": 600,
            },
            timeout=30,
        )
        data = resp.json()
        if resp.status_code != 200:
            _groq_history.pop()  # drop the failed turn
            print(f"Groq error {resp.status_code}: {data}")
            return None
        text = data["choices"][0]["message"]["content"].strip()
        _groq_history.append({"role": "assistant", "content": text})
        return text
    except Exception as e:
        if _groq_history:
            _groq_history.pop()
        print(f"Groq error: {e}")
        return None


# ---- Gemini ----
_gemini_model = None
_chat_session = None


def _get_chat():
    """Return a Gemini chat session that keeps conversation history, or None."""
    global _gemini_model, _chat_session
    if not has_gemini():
        return None
    if _chat_session is None:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            try:
                _gemini_model = genai.GenerativeModel(
                    GEMINI_MODEL, system_instruction=_system_prompt()
                )
            except TypeError:
                # Older SDKs don't support system_instruction.
                _gemini_model = genai.GenerativeModel(GEMINI_MODEL)
            _chat_session = _gemini_model.start_chat(history=[])
        except Exception as e:
            print(f"Gemini init failed: {e}")
            return None
    return _chat_session


def _ask_gemini(prompt):
    chat = _get_chat()
    if chat is None:
        return None
    try:
        resp = chat.send_message(prompt)
        return (resp.text or "").strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        return None


def ask_ai(prompt):
    """Ask the active AI brain, remembering context. Returns text or None."""
    provider = active_ai_provider()
    if provider == "groq":
        return _ask_groq(prompt)
    if provider == "gemini":
        return _ask_gemini(prompt)
    return None


# ================= INPUT (voice or text) =================
class Listener:
    def __init__(self, mode="auto"):
        self.mode = mode
        if mode == "auto":
            self.voice = HAS_SR and self._mic_available()
        elif mode == "voice":
            self.voice = HAS_SR
        else:
            self.voice = False

    @staticmethod
    def _mic_available():
        if not HAS_SR:
            return False
        try:
            return len(sr.Microphone.list_microphone_names()) > 0
        except Exception:
            return False

    def listen(self):
        if self.voice:
            return self._listen_voice()
        return self._listen_text()

    def _listen_text(self):
        try:
            return input("You> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "exit"

    def _listen_voice(self):
        r = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                print("Listening...")
                r.pause_threshold = 1
                r.adjust_for_ambient_noise(source, duration=1)
                audio = r.listen(source, timeout=6, phrase_time_limit=8)
            print("Recognizing...")
            query = r.recognize_google(audio, language="en-in")
            print(f"You said: {query}")
            return query.lower()
        except sr.WaitTimeoutError:
            return ""
        except sr.UnknownValueError:
            return ""
        except Exception as e:
            print(f"Listen error: {e}")
            return ""


# ================= GREETING =================
def wish_me():
    hour = datetime.datetime.now().hour
    if hour < 12:
        greeting = "Good Morning"
    elif hour < 18:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"
    speak(f"{greeting} {USER_TITLE}. I am {ASSISTANT_NAME}. How may I assist you today?")


# ================= APPLICATION / WEB CONTROL =================
def open_target(name):
    """Open a configured app/site, a known site, or a bare URL/domain."""
    name = name.strip()
    key = name.lower()

    if key in config["apps"]:
        target = config["apps"][key]
        if isinstance(target, str) and target.startswith("http"):
            webbrowser.open(target)
        else:
            _open_local_app(target, key)
        speak(f"Opening {key}")
        return

    # Looks like a URL / domain
    if "." in key and " " not in key:
        url = key if key.startswith("http") else f"https://{key}"
        webbrowser.open(url)
        speak(f"Opening {name}")
        return

    # Try to launch as a local application name
    if _open_local_app(name, key):
        speak(f"Opening {name}")
        return

    # Fall back to a web search
    webbrowser.open(f"https://www.google.com/search?q={name}")
    speak(f"I couldn't find an app called {name}, so I searched the web for it.")


def _open_local_app(target, key):
    paths = target if isinstance(target, list) else [target]
    for path in paths:
        try:
            expanded = os.path.expandvars(os.path.expanduser(path))
            if IS_WINDOWS:
                os.startfile(expanded)  # noqa: works on Windows only
                return True
            elif IS_MAC:
                os.system(f'open -a "{expanded}" 2>/dev/null')
                return True
            else:
                os.system(f'{expanded} >/dev/null 2>&1 &')
                return True
        except Exception:
            continue
    return False


# ================= SYSTEM COMMANDS (cross-platform) =================
def system_command(command):
    try:
        if "shutdown" in command:
            speak("Shutting down the system")
            os.system("shutdown /s /t 1" if IS_WINDOWS else "shutdown -h now")
        elif "restart" in command:
            speak("Restarting the system")
            os.system("shutdown /r /t 1" if IS_WINDOWS else "shutdown -r now")
        elif "sleep" in command:
            speak("Putting the system to sleep")
            if IS_WINDOWS:
                os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
            elif IS_MAC:
                os.system("pmset sleepnow")
            else:
                os.system("systemctl suspend")
        elif "lock" in command:
            speak("Locking the system")
            if IS_WINDOWS:
                os.system("rundll32.exe user32.dll,LockWorkStation")
            elif IS_MAC:
                os.system("pmset displaysleepnow")
            else:
                os.system("loginctl lock-session 2>/dev/null || xdg-screensaver lock")
    except Exception as e:
        speak("Sorry, I couldn't execute that system command.")
        print(e)


def system_stats():
    if not HAS_PSUTIL:
        speak("System statistics need the psutil library, which isn't installed.")
        return
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        parts = [
            f"CPU is at {cpu} percent",
            f"memory at {mem.percent} percent",
            f"disk at {disk.percent} percent",
        ]
        battery = getattr(psutil, "sensors_battery", lambda: None)()
        if battery is not None:
            plugged = "charging" if battery.power_plugged else "on battery"
            parts.append(f"battery at {int(battery.percent)} percent and {plugged}")
        speak(". ".join(parts))
    except Exception as e:
        speak("I couldn't read the system statistics.")
        print(e)


def volume_control(command):
    """Media-key based volume control (requires pyautogui)."""
    if not HAS_PYAUTOGUI:
        speak("Volume control needs the pyautogui library, which isn't installed.")
        return
    try:
        if "mute" in command:
            pyautogui.press("volumemute")
            speak("Toggled mute")
        elif "up" in command or "increase" in command:
            for _ in range(5):
                pyautogui.press("volumeup")
            speak("Volume up")
        elif "down" in command or "decrease" in command or "lower" in command:
            for _ in range(5):
                pyautogui.press("volumedown")
            speak("Volume down")
    except Exception as e:
        speak("I couldn't change the volume.")
        print(e)


# ================= INFORMATION =================
def get_time():
    speak(f"{USER_TITLE}, the time is {datetime.datetime.now().strftime('%I:%M %p')}")


def get_date():
    speak(f"Today is {datetime.datetime.now().strftime('%A, %B %d, %Y')}")


def wiki_search(query):
    if not HAS_WIKI:
        speak("Wikipedia support isn't installed. Let me use my AI knowledge instead.")
        answer = ask_ai(query)
        speak(answer or "I couldn't find that information.")
        return
    try:
        query = query.replace("wikipedia", "").strip()
        speak("Searching Wikipedia...")
        result = wikipedia.summary(query, sentences=2)
        speak("According to Wikipedia,")
        speak(result)
    except Exception:
        answer = ask_ai(query)
        speak(answer or "I couldn't find that on Wikipedia.")


def define_word(word):
    """Free dictionary lookup via dictionaryapi.dev."""
    if requests is None:
        speak("I need the requests library to look up definitions.")
        return
    word = word.strip()
    try:
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
        data = requests.get(url, timeout=10).json()
        meaning = data[0]["meanings"][0]
        pos = meaning["partOfSpeech"]
        definition = meaning["definitions"][0]["definition"]
        speak(f"{word}, {pos}: {definition}")
    except Exception:
        speak(f"I couldn't find a definition for {word}.")


def get_weather(city):
    if not WEATHER_API_KEY:
        speak("Weather needs an OpenWeatherMap API key set in your .env file.")
        return
    if requests is None:
        speak("I need the requests library for weather.")
        return
    try:
        url = (
            "https://api.openweathermap.org/data/2.5/weather"
            f"?q={city}&appid={WEATHER_API_KEY}&units=metric"
        )
        r = requests.get(url, timeout=10).json()
        if r.get("cod") != 200:
            speak(f"I couldn't find weather for {city}.")
            return
        temp = r["main"]["temp"]
        desc = r["weather"][0]["description"]
        humidity = r["main"]["humidity"]
        wind = r["wind"]["speed"]
        speak(
            f"Weather in {city}: {desc}. Temperature {temp} degrees Celsius, "
            f"humidity {humidity} percent, wind {wind} meters per second."
        )
    except Exception as e:
        speak("I couldn't fetch the weather.")
        print(e)


def get_news(num=5):
    """Fetch headlines. Uses GNews API key if present, else Gemini."""
    api_key = env("GNEWS_API_KEY")
    if api_key and requests is not None:
        try:
            url = (
                "https://gnews.io/api/v4/top-headlines"
                f"?lang=en&max={num}&apikey={api_key}"
            )
            articles = requests.get(url, timeout=10).json().get("articles", [])
            if articles:
                speak("Here are the latest headlines:")
                for i, a in enumerate(articles[:num], 1):
                    speak(f"{i}. {a['title']}")
                return
        except Exception as e:
            print(f"News error: {e}")
    answer = ask_ai("Give me a short list of today's top news headlines.")
    speak(answer or "I couldn't fetch the news right now.")


def public_ip():
    if requests is None:
        speak("I need the requests library to check your IP address.")
        return
    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text
        speak(f"Your public IP address is {ip}")
    except Exception:
        speak("I couldn't determine your public IP address.")


def convert_currency(amount, base, target):
    if requests is None:
        speak("I need the requests library for currency conversion.")
        return
    try:
        base, target = base.upper(), target.upper()
        url = f"https://open.er-api.com/v6/latest/{base}"
        rates = requests.get(url, timeout=10).json().get("rates", {})
        if target not in rates:
            speak(f"I don't have a rate for {target}.")
            return
        result = round(amount * rates[target], 2)
        speak(f"{amount} {base} is about {result} {target}")
    except Exception:
        speak("I couldn't convert that currency.")


# ================= UTILITIES =================
def tell_joke():
    if HAS_JOKES:
        speak(pyjokes.get_joke())
    else:
        speak(ask_ai("Tell me a short, clean programming joke.") or "Why did the developer go broke? He used up all his cache.")


def calculate(expression):
    # Prefer Wolfram if configured, otherwise safe local math, otherwise AI.
    if HAS_WOLFRAM and WOLFRAM_APP_ID:
        try:
            client = wolframalpha.Client(WOLFRAM_APP_ID)
            res = client.query(expression)
            speak(f"The answer is {next(res.results).text}")
            return
        except Exception:
            pass
    local = _safe_math(expression)
    if local is not None:
        speak(f"The answer is {local}")
        return
    answer = ask_ai(f"Compute or explain: {expression}")
    speak(answer or "I couldn't calculate that.")


def _safe_math(expression):
    """Evaluate a plain arithmetic expression safely, or return None."""
    words = {
        "plus": "+", "add": "+", "minus": "-", "subtract": "-",
        "times": "*", "multiplied by": "*", "into": "*",
        "divided by": "/", "over": "/", "power": "**", "mod": "%",
    }
    expr = expression.lower()
    for w, sym in words.items():
        expr = expr.replace(w, sym)
    expr = "".join(c for c in expr if c in "0123456789+-*/%.() ")
    expr = expr.strip()
    if not expr or not any(c.isdigit() for c in expr):
        return None
    try:
        return eval(expr, {"__builtins__": {}}, {})  # sandboxed: only arithmetic
    except Exception:
        return None


def flip_coin():
    speak(random.choice(["Heads", "Tails"]))


def roll_dice(sides=6):
    speak(f"You rolled a {random.randint(1, sides)}")


def random_number(lo=1, hi=100):
    speak(f"Your random number is {random.randint(lo, hi)}")


def generate_password(length=16):
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    pwd = "".join(random.choice(chars) for _ in range(length))
    print(f"Generated password: {pwd}")
    speak(f"I've generated a {length} character password and printed it to the screen.")


def take_screenshot():
    if not HAS_PYAUTOGUI:
        speak("Screenshots need the pyautogui library.")
        return
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(CONFIG_DIR, f"screenshot_{ts}.png")
        pyautogui.screenshot(path)
        speak("Screenshot saved.")
        print(f"Saved to {path}")
    except Exception as e:
        speak("I couldn't take a screenshot.")
        print(e)


# ================= NOTES / TO-DO / REMINDERS (persistent) =================
def note(text):
    date = datetime.datetime.now()
    path = os.path.join(CONFIG_DIR, f"note_{date.strftime('%d-%m-%Y')}.txt")
    with open(path, "a") as f:
        f.write(f"{date.strftime('%H:%M:%S')}: {text}\n")
    speak("Note added.")


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def todo_add(item):
    todos = _load_json(TODO_FILE, [])
    todos.append({"task": item, "done": False,
                  "added": datetime.datetime.now().isoformat(timespec="seconds")})
    _save_json(TODO_FILE, todos)
    speak(f"Added to your to-do list: {item}")


def todo_list():
    todos = _load_json(TODO_FILE, [])
    if not todos:
        speak("Your to-do list is empty.")
        return
    pending = [t for t in todos if not t["done"]]
    if not pending:
        speak("You've completed everything on your list. Well done.")
        return
    speak(f"You have {len(pending)} pending tasks:")
    for i, t in enumerate(pending, 1):
        speak(f"{i}. {t['task']}")


def todo_done(index):
    todos = _load_json(TODO_FILE, [])
    pending = [t for t in todos if not t["done"]]
    if 1 <= index <= len(pending):
        pending[index - 1]["done"] = True
        _save_json(TODO_FILE, todos)
        speak(f"Marked '{pending[index - 1]['task']}' as done.")
    else:
        speak("That task number doesn't exist.")


def notify(text):
    """Proactive announcement: speak it AND queue it for the web UI."""
    push_event(text)
    speak(text)


def add_reminder(text, minutes):
    """Persistent reminder. A background thread fires it (and survives within run)."""
    fire_at = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
    reminders = _load_json(REMINDER_FILE, [])
    reminders.append({"text": text, "fire_at": fire_at.isoformat()})
    _save_json(REMINDER_FILE, reminders)
    speak(f"I'll remind you to {text} in {minutes} minutes.")


def _reminder_worker():
    """Background loop that fires due reminders roughly every 15 seconds."""
    while True:
        try:
            reminders = _load_json(REMINDER_FILE, [])
            if reminders:
                now = datetime.datetime.now()
                remaining = []
                for r in reminders:
                    try:
                        due = datetime.datetime.fromisoformat(r["fire_at"])
                    except Exception:
                        continue
                    if due <= now:
                        notify(f"Reminder, {USER_TITLE}: {r['text']}")
                    else:
                        remaining.append(r)
                if len(remaining) != len(reminders):
                    _save_json(REMINDER_FILE, remaining)
        except Exception as e:
            print(f"Reminder worker error: {e}")
        time.sleep(15)


def set_timer(seconds):
    def _timer():
        time.sleep(seconds)
        notify(f"{USER_TITLE}, your {seconds} second timer is up.")
    threading.Thread(target=_timer, daemon=True).start()
    speak(f"Timer set for {seconds} seconds.")


def set_alarm(when_text):
    """Set an alarm for a clock time like '7:30 am' or '18:45'."""
    now = datetime.datetime.now()
    target = None
    for fmt in ("%I:%M %p", "%I %p", "%H:%M", "%I:%M%p"):
        try:
            t = datetime.datetime.strptime(when_text.strip().upper(), fmt)
            target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            break
        except ValueError:
            continue
    if target is None:
        speak("Try something like: set an alarm for 7:30 am.")
        return
    if target <= now:
        target += datetime.timedelta(days=1)  # next day
    delay = (target - now).total_seconds()

    def _alarm():
        time.sleep(delay)
        notify(f"{USER_TITLE}, this is your alarm for {target.strftime('%I:%M %p')}.")
    threading.Thread(target=_alarm, daemon=True).start()
    speak(f"Alarm set for {target.strftime('%I:%M %p')}.")


# ================= MEDIA =================
def play_on_youtube(song):
    if HAS_PYWHATKIT:
        try:
            speak(f"Playing {song} on YouTube")
            pywhatkit.playonyt(song)
            return
        except Exception as e:
            print(f"YouTube error: {e}")
    webbrowser.open(f"https://www.youtube.com/results?search_query={song}")
    speak(f"Here are YouTube results for {song}.")


# ================= SPOTIFY (lazy) =================
_spotify = None


def _get_spotify():
    global _spotify
    if not (HAS_SPOTIFY and SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
        return None
    if _spotify is None:
        try:
            _spotify = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope="user-read-playback-state,user-modify-playback-state",
                cache_path=os.path.join(CONFIG_DIR, ".spotify_cache")))
        except Exception as e:
            print(f"Spotify init failed: {e}")
            return None
    return _spotify


def play_on_spotify(song):
    sp = _get_spotify()
    if sp is None:
        speak("Spotify isn't configured. Set your Spotify keys in .env, "
              "or I can play it on YouTube instead.")
        play_on_youtube(song)
        return
    try:
        results = sp.search(q=song, limit=1)
        items = results["tracks"]["items"]
        if not items:
            speak(f"I couldn't find {song} on Spotify.")
            return
        sp.start_playback(uris=[items[0]["uri"]])
        speak(f"Playing {song} on Spotify.")
    except Exception as e:
        speak("I couldn't play that on Spotify. Make sure Spotify is open and active.")
        print(f"Spotify error: {e}")


# ================= EMAIL =================
def send_email(recipient, subject, body):
    if not (EMAIL_SENDER and EMAIL_PASSWORD):
        speak("Email isn't configured. Add EMAIL_SENDER and EMAIL_PASSWORD to your .env "
              "(use a Google App Password).")
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart()
        msg["From"] = EMAIL_SENDER
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        speak("Email sent successfully.")
    except Exception as e:
        speak("I couldn't send the email.")
        print(f"Email error: {e}")


# ================= WHATSAPP =================
def send_whatsapp(number, message):
    if not HAS_PYWHATKIT:
        speak("WhatsApp messaging needs the pywhatkit library.")
        return
    try:
        speak(f"Opening WhatsApp to message {number}.")
        # Sends via WhatsApp Web; opens a browser tab.
        pywhatkit.sendwhatmsg_instantly(number, message, wait_time=15, tab_close=True)
        speak("Message queued in WhatsApp Web.")
    except Exception as e:
        speak("I couldn't send the WhatsApp message.")
        print(f"WhatsApp error: {e}")


# ================= GOOGLE CALENDAR (lazy) =================
_calendar_service = None


def _get_calendar():
    """Lazily authenticate Google Calendar. Never crashes on startup."""
    global _calendar_service
    if not HAS_GCAL:
        return None
    if _calendar_service is not None:
        return _calendar_service
    creds = None
    token_path = os.path.join(CONFIG_DIR, "token.pickle")
    creds_path = os.path.join(CONFIG_DIR, "credentials.json")
    scopes = ["https://www.googleapis.com/auth/calendar"]
    try:
        import pickle
        if os.path.exists(token_path):
            with open(token_path, "rb") as t:
                creds = pickle.load(t)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif os.path.exists(creds_path):
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
                creds = flow.run_local_server(port=0)
            else:
                return None  # no credentials.json yet
            with open(token_path, "wb") as t:
                pickle.dump(creds, t)
        _calendar_service = build("calendar", "v3", credentials=creds)
        return _calendar_service
    except Exception as e:
        print(f"Calendar auth failed: {e}")
        return None


def calendar_events(num=5):
    svc = _get_calendar()
    if svc is None:
        speak("Google Calendar isn't set up. Place your credentials.json in "
              f"{CONFIG_DIR} and try again.")
        return
    try:
        now = datetime.datetime.utcnow().isoformat() + "Z"
        result = svc.events().list(calendarId="primary", timeMin=now, maxResults=num,
                                   singleEvents=True, orderBy="startTime").execute()
        events = result.get("items", [])
        if not events:
            speak("You have no upcoming events.")
            return
        speak(f"You have {len(events)} upcoming events:")
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date"))
            speak(f"{e.get('summary', 'Untitled')} at {start}")
    except Exception as e:
        speak("I couldn't read your calendar.")
        print(f"Calendar error: {e}")


def add_calendar_event(summary, start_iso, minutes=60):
    svc = _get_calendar()
    if svc is None:
        speak("Google Calendar isn't set up.")
        return
    try:
        end = (datetime.datetime.fromisoformat(start_iso)
               + datetime.timedelta(minutes=minutes)).isoformat()
        svc.events().insert(calendarId="primary", body={
            "summary": summary,
            "start": {"dateTime": start_iso, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
        }).execute()
        speak(f"Added '{summary}' to your calendar.")
    except Exception as e:
        speak("I couldn't add that event.")
        print(f"Calendar error: {e}")


# ================= TRANSLATE / CLIPBOARD =================
def translate(text, target_language):
    """Translate via Gemini (no extra dependency needed)."""
    answer = ask_ai(
        f"Translate the following text into {target_language}. "
        f"Reply with ONLY the translation, nothing else:\n\n{text}"
    )
    if answer:
        speak(answer)
    else:
        speak("Translation needs an AI brain (Groq or Gemini) configured in your .env.")


def clipboard_copy(text):
    if not HAS_CLIPBOARD:
        speak("Clipboard access needs the pyperclip library.")
        return
    try:
        pyperclip.copy(text)
        speak("Copied to your clipboard.")
    except Exception:
        speak("I couldn't access the clipboard.")


def clipboard_read():
    if not HAS_CLIPBOARD:
        speak("Clipboard access needs the pyperclip library.")
        return
    try:
        content = pyperclip.paste()
        speak(f"Your clipboard says: {content}" if content else "Your clipboard is empty.")
    except Exception:
        speak("I couldn't access the clipboard.")


# ================= DAILY BRIEFING =================
def briefing():
    """A quick 'good morning' rundown: time, weather, agenda, tasks, headlines."""
    hour = datetime.datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    speak(f"Good {part}, {USER_TITLE}. Here is your briefing.")
    speak(f"It is {datetime.datetime.now().strftime('%A, %B %d, %I:%M %p')}.")
    if DEFAULT_CITY and WEATHER_API_KEY:
        get_weather(DEFAULT_CITY)
    todos = _load_json(TODO_FILE, [])
    pending = [t for t in todos if not t["done"]]
    if pending:
        speak(f"You have {len(pending)} pending tasks. "
              f"The first is: {pending[0]['task']}.")
    if HAS_GCAL:
        calendar_events(3)
    get_news(3)
    speak("That's your briefing. Have a productive day.")


# ================= HELP =================
def show_help():
    # A compact, grouped overview — spoken and shown in the web HUD.
    speak(f"Here are some things you can ask me, {USER_TITLE}:")
    speak("Info: time, date, weather in a city, news, define a word, or wikipedia a topic.")
    speak("Productivity: todo add and todo list, remind me, set a timer or an alarm, take a note.")
    speak("System: system stats, volume up or down, lock, sleep, screenshot.")
    speak("Media and more: play a song, translate text, convert currency, my calendar, send email.")
    speak("Fun: joke, flip a coin, roll a dice, generate a password. Or just ask me anything.")
    # Full reference table to the console for desktop users.
    lines = [
        "open <app or website>            - launch an app or site",
        "what is / who is / search <x>    - ask me anything",
        "time / date                      - current time or date",
        "weather in <city>                - current weather",
        "news                             - today's headlines",
        "define <word>                    - dictionary definition",
        "calculate <expression>           - do math",
        "convert 10 usd to eur            - currency conversion",
        "todo add <task> / todo list      - manage your to-do list",
        "remind me to <x> in <n> minutes  - set a reminder",
        "set timer for <n> seconds        - quick timer",
        "system stats                     - CPU / memory / battery",
        "volume up / down / mute          - control volume",
        "play <song> [on spotify]         - play music",
        "briefing / good morning          - time, weather, agenda, news",
        "set an alarm for 7:30 am         - alarm at a clock time",
        "translate <text> to <language>   - translate anything",
        "my calendar / upcoming events    - Google Calendar agenda",
        "send email / send a whatsapp     - messaging",
        "copy <text> to clipboard         - clipboard control",
        "take a note <text>               - save a note",
        "screenshot                       - capture the screen",
        "joke / flip a coin / roll a dice - fun",
        "generate a password              - random strong password",
        "my ip                            - your public IP address",
        "lock / sleep / shutdown / restart- system control",
        "exit / quit / goodbye            - shut me down",
    ]
    print("\n".join("  " + l for l in lines))


# ================= COMMAND ROUTER =================
WAKE_WORDS = ("jarvis", "hey jarvis", "okay jarvis")


def _extract_number(text, default=None):
    for token in text.replace(":", " ").split():
        if token.isdigit():
            return int(token)
    return default


def handle(query, listener):
    """Route a single command. Returns False to exit, True otherwise."""
    if not query:
        return True

    # Strip wake word if present (voice mode nicety).
    for w in WAKE_WORDS:
        if query.startswith(w):
            query = query[len(w):].strip()
            break
    if not query:
        return True

    q = query
    tokens = q.split()

    # ---- exit ----
    if any(w in q for w in ("exit", "quit", "goodbye", "good bye", "shut down jarvis", "stop listening")):
        speak(f"Goodbye {USER_TITLE}. Have a great day!")
        return False

    # ---- help ----
    if q in ("help", "what can you do", "commands"):
        show_help()
        return True

    # ---- open ----
    if q.startswith("open "):
        open_target(q[5:])
        return True

    # ---- time / date ---- (word-based so "15 times 12" isn't caught)
    if ("time" in tokens or "what time" in q or "the time" in q) and "timer" not in q:
        get_time(); return True
    if "date" in tokens or "day is it" in q or q == "today":
        get_date(); return True

    # ---- system control ----
    if any(w in q for w in ("shutdown", "shut down", "restart", "lock the", "put to sleep")) or q in ("lock", "sleep"):
        system_command(q); return True
    if "system stat" in q or "system info" in q or "cpu" in q or ("battery" in q):
        system_stats(); return True
    if "volume" in q or "mute" in q:
        volume_control(q); return True

    # ---- notes / todo / reminders / timer ----
    if q.startswith("take a note") or q.startswith("note ") or q.startswith("remember "):
        text = q.replace("take a note", "").replace("note", "", 1).replace("remember", "", 1).strip()
        if not text:
            speak("What should I note down?")
            text = listener.listen()
        if text:
            note(text)
        return True
    if q.startswith("todo add") or q.startswith("add task") or q.startswith("add to do"):
        item = q.replace("todo add", "").replace("add task", "").replace("add to do", "").strip()
        if item:
            todo_add(item)
        else:
            speak("What task should I add?")
            item = listener.listen()
            if item:
                todo_add(item)
        return True
    if "todo list" in q or "to do list" in q or "my tasks" in q or "show tasks" in q:
        todo_list(); return True
    if q.startswith("complete task") or q.startswith("done task") or q.startswith("finish task"):
        idx = _extract_number(q, 1)
        todo_done(idx); return True
    if q.startswith("remind me"):
        try:
            body = q.split("to", 1)[1] if "to" in q else q
            if " in " in body:
                text, after = body.rsplit(" in ", 1)  # rsplit avoids "drink"/"in" clash
                minutes = _extract_number(after, 5)
            else:
                text, minutes = body, 5
            add_reminder(text.strip(), minutes)
        except Exception:
            speak("Try: remind me to call mom in 30 minutes.")
        return True
    if "set timer" in q or "start timer" in q:
        secs = _extract_number(q, 60)
        if "minute" in q:
            secs *= 60
        set_timer(secs); return True

    # ---- info lookups ----
    if "wikipedia" in q:
        wiki_search(q); return True
    if q.startswith("define ") or q.startswith("definition of ") or q.startswith("meaning of "):
        word = q.replace("definition of", "").replace("define", "").replace("meaning of", "").strip()
        define_word(word); return True
    if "weather" in q:
        city = q.replace("weather", "").replace("in", "").replace("the", "").strip()
        if not city:
            speak("Which city?")
            city = listener.listen()
        if city:
            get_weather(city)
        return True
    if "news" in q or "headlines" in q:
        get_news(); return True
    if "my ip" in q or "ip address" in q:
        public_ip(); return True

    # ---- currency ----
    if "convert" in q and ("to" in q):
        try:
            parts = q.replace("convert", "").strip().split()
            amount = float(parts[0])
            base = parts[1]
            target = parts[parts.index("to") + 1]
            convert_currency(amount, base, target)
            return True
        except Exception:
            speak("Try: convert 10 usd to eur.")
            return True

    # ---- math ----
    if q.startswith("calculate") or q.startswith("what's") or (
        any(op in q for op in ("plus", "minus", "times", "divided by", "multiplied")) and any(c.isdigit() for c in q)
    ):
        calculate(q.replace("calculate", "").replace("what's", "").strip() or q)
        return True

    # ---- media ----
    if q.startswith("play "):
        song = q[5:]
        if "on spotify" in song:
            play_on_spotify(song.replace("on spotify", "").strip())
        else:
            play_on_youtube(song.replace("on youtube", "").strip())
        return True

    # ---- daily briefing ----
    if q in ("briefing", "brief me", "good morning", "morning briefing", "daily briefing"):
        briefing(); return True

    # ---- alarm ----
    if "alarm" in q:
        when = q.split("for", 1)[1].strip() if "for" in q else ""
        set_alarm(when or "")
        return True

    # ---- email ----
    if q.startswith("send email") or q.startswith("send an email") or q.startswith("email "):
        speak("Who should I email?")
        to = listener.listen()
        speak("What's the subject?")
        subject = listener.listen()
        speak("What should the message say?")
        body = listener.listen()
        if to:
            send_email(to, subject or "(no subject)", body or "")
        else:
            speak("I need a recipient to send an email.")
        return True

    # ---- whatsapp ----
    if "whatsapp" in q:
        speak("What's the phone number, including country code?")
        number = listener.listen().replace(" ", "")
        speak("What should the message say?")
        body = listener.listen()
        if number and body:
            send_whatsapp(number, body)
        else:
            speak("I need a number and a message.")
        return True

    # ---- calendar ----
    if "calendar" in q or "upcoming event" in q or "my agenda" in q or "my schedule" in q:
        calendar_events(); return True

    # ---- translate ----
    if q.startswith("translate "):
        rest = q[len("translate "):]
        if " to " in rest:
            text, lang = rest.rsplit(" to ", 1)
            translate(text.strip(), lang.strip())
        else:
            speak("Try: translate good morning to French.")
        return True

    # ---- clipboard ----
    if "read clipboard" in q or "what's on my clipboard" in q or q == "paste":
        clipboard_read(); return True
    if q.startswith("copy ") and "clipboard" in q:
        text = q[5:].replace("to clipboard", "").replace("to the clipboard", "").strip()
        clipboard_copy(text); return True

    # ---- fun / utilities ----
    if "joke" in q:
        tell_joke(); return True
    if "flip a coin" in q or "flip coin" in q or "toss a coin" in q:
        flip_coin(); return True
    if "roll a dice" in q or "roll dice" in q or "roll a die" in q:
        roll_dice(); return True
    if "random number" in q:
        random_number(); return True
    if "generate" in q and "password" in q:
        length = _extract_number(q, 16)
        generate_password(length); return True
    if "screenshot" in q or "screen shot" in q:
        take_screenshot(); return True

    # ---- fall back to conversational AI ----
    answer = ask_ai(query)
    if answer:
        speak(answer)
    elif active_ai_provider() is None:
        speak("I'm not sure how to help with that, and my AI brain isn't configured. "
              "Add a Groq or Gemini key to your .env. Say 'help' to hear what I can do.")
    else:
        speak("I couldn't reach my AI brain just now — please check your connection. "
              "Say 'help' to hear what I can do offline.")
    return True


# ================= PROGRAMMATIC ENTRY (web / tests) =================
class _SilentListener:
    """A listener for one-shot commands (web/tests): follow-up prompts get ''."""
    voice = False

    def listen(self):
        return ""


def process_command(query):
    """Run a single command and return the spoken responses as a list of strings.

    Used by the web server and the test-suite. Does not use audio; instead it
    captures everything speak() would say into a thread-local buffer.
    """
    _capture.buffer = []
    try:
        cont = handle((query or "").strip().lower(), _SilentListener())
        return {"responses": list(_capture.buffer), "keep_open": cont}
    except Exception as e:
        return {"responses": [f"Something went wrong: {e}"], "keep_open": True}
    finally:
        _capture.buffer = None


# ================= MAIN LOOP =================
def run_jarvis(listener, gui=None):
    if gui:
        gui.set_status(f"{ASSISTANT_NAME} is running "
                       f"({'voice' if listener.voice else 'text'} mode)")
    wish_me()
    if not listener.voice:
        print("(Text mode: type your commands. Type 'help' for options, 'exit' to quit.)")
    while True:
        try:
            query = listener.listen()
        except KeyboardInterrupt:
            speak("Goodbye.")
            break
        if gui and query:
            gui.set_status(f"Heard: {query[:40]}")
        if not handle(query, listener):
            break
    if gui:
        gui.close()
    os._exit(0)


# ================= OPTIONAL GUI =================
def start_gui():
    try:
        from tkinter import Tk, Label
    except Exception:
        return None

    class JarvisGUI:
        def __init__(self):
            self.root = Tk()
            self.root.title(ASSISTANT_NAME)
            self.root.geometry("340x140")
            self.root.configure(bg="black")
            self.label = Label(self.root, text=f"{ASSISTANT_NAME} starting...",
                               fg="#00e676", bg="black", font=("Arial", 12), wraplength=320)
            self.label.pack(pady=40, padx=10)

        def set_status(self, text):
            try:
                self.label.config(text=text)
                self.root.update_idletasks()
            except Exception:
                pass

        def close(self):
            try:
                self.root.destroy()
            except Exception:
                pass

    try:
        return JarvisGUI()
    except Exception:
        return None


def print_capabilities():
    provider = active_ai_provider()
    brain = {"groq": f"AI brain: Groq ({GROQ_MODEL})",
             "gemini": f"AI brain: Gemini ({GEMINI_MODEL})"}.get(provider, "AI brain")
    caps = {
        "Speech recognition (mic input)": HAS_SR,
        "Text-to-speech (voice output)": HAS_TTS,
        brain: provider is not None,
        "Wikipedia": HAS_WIKI,
        "System stats (psutil)": HAS_PSUTIL,
        "Screenshots/volume (pyautogui)": HAS_PYAUTOGUI,
        "YouTube playback (pywhatkit)": HAS_PYWHATKIT,
        "Weather (API key)": bool(WEATHER_API_KEY),
    }
    print("=" * 44)
    print(f"  {ASSISTANT_NAME} capability check")
    print("=" * 44)
    for name, ok in caps.items():
        print(f"  [{'x' if ok else ' '}] {name}")
    print("=" * 44)


def run_cli(args):
    """The classic terminal/desktop assistant (voice or text)."""
    print_capabilities()
    mode = "auto"
    if args.text:
        mode = "text"
    elif args.voice:
        mode = "voice"
    listener = Listener(mode=mode)
    threading.Thread(target=_reminder_worker, daemon=True).start()
    gui = None if args.no_gui else start_gui()
    if gui is not None:
        threading.Thread(target=run_jarvis, args=(listener, gui), daemon=True).start()
        gui.root.mainloop()
    else:
        run_jarvis(listener, None)


# ================================================================
#  WEB HUD  —  the Iron Man style interface, embedded in this file
# ================================================================
HUD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>J.A.R.V.I.S.</title>
<style>
  :root{
    --cyan:#3fd8ff; --cyan-dim:#1a7fa8; --gold:#ffb23e; --bg:#00060e;
    --danger:#ff5b5b; --glow:0 0 12px var(--cyan);
  }
  *{box-sizing:border-box; margin:0; padding:0;}
  html,body{height:100%; overflow:hidden; background:var(--bg);
    font-family:"Segoe UI",Consolas,"Courier New",monospace; color:var(--cyan);
    user-select:none;}
  #bg{position:fixed; inset:0; z-index:0;}
  .frame{position:fixed; inset:0; z-index:2; pointer-events:none;}
  .bracket{position:absolute; width:70px; height:70px; border:2px solid var(--cyan);
    opacity:.5; filter:drop-shadow(var(--glow));}
  .bracket.tl{top:18px; left:18px; border-right:none; border-bottom:none;}
  .bracket.tr{top:18px; right:18px; border-left:none; border-bottom:none;}
  .bracket.bl{bottom:18px; left:18px; border-right:none; border-top:none;}
  .bracket.br{bottom:18px; right:18px; border-left:none; border-top:none;}
  .readout{position:absolute; font-size:12px; letter-spacing:1px; line-height:1.7;
    text-transform:uppercase; opacity:.85;}
  .readout .k{color:var(--cyan-dim);}
  .readout .v{color:var(--cyan); text-shadow:var(--glow);}
  #topLeft{top:34px; left:44px;}
  #topRight{top:34px; right:44px; text-align:right;}
  #botLeft{bottom:120px; left:44px;}
  #botRight{bottom:120px; right:44px; text-align:right; max-width:260px;}
  .title{font-size:15px; color:var(--gold); text-shadow:0 0 10px var(--gold);
    letter-spacing:4px; margin-bottom:6px;}
  .cap{display:inline-block; padding:2px 8px; margin:2px; border:1px solid var(--cyan-dim);
    border-radius:10px; font-size:10px;}
  .cap.on{color:var(--cyan); border-color:var(--cyan); box-shadow:0 0 6px rgba(63,216,255,.4);}
  .cap.off{color:#3a4a55; border-color:#243038;}
  #stage{position:fixed; inset:0; z-index:1; display:flex; flex-direction:column;
    align-items:center; justify-content:center;}
  .reactor{position:relative; width:420px; height:420px; display:flex;
    align-items:center; justify-content:center;}
  #viz{position:absolute; inset:0;}
  .ring{position:absolute; border-radius:50%; border:1px solid var(--cyan);
    box-shadow:var(--glow), inset 0 0 12px rgba(63,216,255,.25); opacity:.7;}
  .r1{width:300px; height:300px; border-style:solid; animation:spin 18s linear infinite;}
  .r2{width:238px; height:238px; border-style:dashed; border-color:var(--cyan-dim);
    animation:spin 12s linear infinite reverse;}
  .r3{width:360px; height:360px; border-top-color:transparent; border-left-color:transparent;
    animation:spin 26s linear infinite;}
  .r4{width:180px; height:180px; border-style:dotted; animation:spin 9s linear infinite;}
  .core{position:relative; width:120px; height:120px; border-radius:50%;
    background:radial-gradient(circle at 50% 45%, #eafcff 0%, var(--cyan) 35%, var(--cyan-dim) 70%, #003043 100%);
    box-shadow:0 0 40px var(--cyan), 0 0 90px rgba(63,216,255,.6), inset 0 0 30px #bff6ff;
    animation:pulse 3s ease-in-out infinite; z-index:2;}
  .core::after{content:""; position:absolute; inset:26px; border-radius:50%;
    background:radial-gradient(circle,#ffffff,#d5f7ff 60%,transparent 75%); opacity:.9;}
  .status{margin-top:38px; font-size:16px; letter-spacing:6px; text-transform:uppercase;
    color:var(--cyan); text-shadow:var(--glow); min-height:22px;}
  .listening .core{animation:pulse 1s ease-in-out infinite;
    background:radial-gradient(circle at 50% 45%,#eafffb 0%,#3fffd8 35%,#12907a 70%,#003a30 100%);
    box-shadow:0 0 50px #3fffd8,0 0 100px rgba(63,255,216,.6),inset 0 0 30px #bfffee;}
  .speaking .core{animation:pulse .55s ease-in-out infinite;
    background:radial-gradient(circle at 50% 45%,#fff6e6 0%,var(--gold) 35%,#a86a10 70%,#3a2600 100%);
    box-shadow:0 0 50px var(--gold),0 0 110px rgba(255,178,62,.6),inset 0 0 30px #ffe6b0;}
  @keyframes spin{to{transform:rotate(360deg);}}
  @keyframes pulse{0%,100%{transform:scale(1);}50%{transform:scale(1.06);}}
  #log{position:fixed; z-index:3; left:50%; transform:translateX(-50%);
    bottom:170px; width:min(720px,86vw); max-height:26vh; overflow-y:auto;
    padding:6px 10px; display:flex; flex-direction:column; gap:8px;
    pointer-events:auto; scrollbar-width:thin;}
  #log::-webkit-scrollbar{width:5px;} #log::-webkit-scrollbar-thumb{background:var(--cyan-dim);}
  .msg{max-width:80%; padding:8px 14px; font-size:14px; line-height:1.5;
    border-radius:12px; backdrop-filter:blur(3px); animation:fade .4s ease;}
  .msg.j{align-self:flex-start; background:rgba(10,40,60,.55); border:1px solid var(--cyan-dim);
    color:#dff6ff; border-left:3px solid var(--cyan);}
  .msg.u{align-self:flex-end; background:rgba(60,40,5,.5); border:1px solid #6b4d12;
    color:#ffe9c2; border-right:3px solid var(--gold);}
  @keyframes fade{from{opacity:0; transform:translateY(8px);}to{opacity:1;}}
  #bar{position:fixed; z-index:4; left:50%; transform:translateX(-50%); bottom:44px;
    display:flex; gap:12px; align-items:center; pointer-events:auto; width:min(720px,86vw);}
  #input{flex:1; background:rgba(4,18,28,.85); border:1px solid var(--cyan-dim);
    border-radius:26px; padding:14px 20px; color:var(--cyan); font-size:15px;
    outline:none; box-shadow:inset 0 0 14px rgba(63,216,255,.12);}
  #input::placeholder{color:#3a5a68;}
  #input:focus{border-color:var(--cyan); box-shadow:inset 0 0 14px rgba(63,216,255,.3),0 0 10px rgba(63,216,255,.3);}
  .btn{width:52px; height:52px; border-radius:50%; border:1px solid var(--cyan);
    background:rgba(6,28,40,.9); color:var(--cyan); font-size:20px; cursor:pointer;
    display:flex; align-items:center; justify-content:center; transition:.2s;
    box-shadow:0 0 10px rgba(63,216,255,.3);}
  .btn:hover{background:var(--cyan); color:#00131c; box-shadow:0 0 20px var(--cyan);}
  #mic.rec{background:var(--danger); border-color:var(--danger); color:#fff;
    box-shadow:0 0 22px var(--danger); animation:pulse 1s infinite;}
  .hint{position:fixed; z-index:4; bottom:16px; left:50%; transform:translateX(-50%);
    font-size:11px; color:#2f4a58; letter-spacing:1px;}
</style>
</head>
<body>
<canvas id="bg"></canvas>
<div class="frame">
  <div class="bracket tl"></div><div class="bracket tr"></div>
  <div class="bracket bl"></div><div class="bracket br"></div>
</div>
<div class="readout" id="topLeft">
  <div class="title">J.A.R.V.I.S.</div>
  <div><span class="k">System</span> &middot; <span class="v" id="sysState">ONLINE</span></div>
  <div><span class="k">Mode</span> &middot; <span class="v" id="modeState">STANDBY</span></div>
</div>
<div class="readout" id="topRight">
  <div class="v" id="clock" style="font-size:22px;">--:--:--</div>
  <div class="k" id="dateline">----</div>
</div>
<div class="readout" id="botLeft">
  <div><span class="k">Core</span> <span class="v" id="tCore">98.4%</span></div>
  <div><span class="k">Flux</span> <span class="v" id="tFlux">1.21 GW</span></div>
  <div><span class="k">Uplink</span> <span class="v" id="tLink">SECURE</span></div>
</div>
<div class="readout" id="botRight">
  <div class="k" style="margin-bottom:4px;">Subsystems</div>
  <div id="caps"></div>
</div>
<div id="stage">
  <div class="reactor" id="reactor">
    <canvas id="viz" width="420" height="420"></canvas>
    <div class="ring r3"></div><div class="ring r1"></div>
    <div class="ring r2"></div><div class="ring r4"></div>
    <div class="core"></div>
  </div>
  <div class="status" id="status">INITIALIZING</div>
</div>
<div id="log"></div>
<div id="bar">
  <input id="input" type="text" placeholder="Speak or type a command&hellip;  (try &quot;briefing&quot;, &quot;weather in Tokyo&quot;, &quot;help&quot;)" autocomplete="off">
  <button class="btn" id="send" title="Send">&#10148;</button>
  <button class="btn" id="mic" title="Voice">&#127908;</button>
</div>
<div class="hint">Click the mic and speak &middot; voice runs in your browser &middot; say &ldquo;help&rdquo; for commands</div>
<script>
const $ = s => document.querySelector(s);
const reactor = $('#reactor'), statusEl = $('#status'), logEl = $('#log');
let assistantName = "Jarvis";
const bg = $('#bg'), bx = bg.getContext('2d');
let parts = [];
function sizeBg(){ bg.width = innerWidth; bg.height = innerHeight; }
sizeBg(); addEventListener('resize', sizeBg);
for(let i=0;i<90;i++) parts.push({x:Math.random()*innerWidth, y:Math.random()*innerHeight,
  r:Math.random()*1.6+.3, s:Math.random()*.25+.05, a:Math.random()*.5+.15});
function drawBg(){
  bx.clearRect(0,0,bg.width,bg.height);
  bx.strokeStyle='rgba(63,216,255,.04)'; bx.lineWidth=1;
  for(let x=0;x<bg.width;x+=60){bx.beginPath();bx.moveTo(x,0);bx.lineTo(x,bg.height);bx.stroke();}
  for(let y=0;y<bg.height;y+=60){bx.beginPath();bx.moveTo(0,y);bx.lineTo(bg.width,y);bx.stroke();}
  for(const p of parts){ p.y-=p.s; if(p.y<0){p.y=bg.height; p.x=Math.random()*bg.width;}
    bx.beginPath(); bx.arc(p.x,p.y,p.r,0,7); bx.fillStyle='rgba(63,216,255,'+p.a+')'; bx.fill(); }
  requestAnimationFrame(drawBg);
}
drawBg();
const viz = $('#viz'), vx = viz.getContext('2d');
const BARS = 84; let amp = new Array(BARS).fill(0); let vState='idle';
function drawViz(){
  vx.clearRect(0,0,420,420);
  const cx=210, cy=210, base=136;
  for(let i=0;i<BARS;i++){
    let target = vState==='idle' ? 6+Math.sin(Date.now()/400+i)*3
      : vState==='listening' ? 8+Math.random()*22 : 10+Math.random()*46;
    amp[i] += (target-amp[i])*0.25;
    const ang = (i/BARS)*Math.PI*2;
    const x1=cx+Math.cos(ang)*base, y1=cy+Math.sin(ang)*base;
    const x2=cx+Math.cos(ang)*(base+amp[i]), y2=cy+Math.sin(ang)*(base+amp[i]);
    const col = vState==='speaking' ? '255,178,62' : vState==='listening' ? '63,255,216' : '63,216,255';
    vx.strokeStyle='rgba('+col+',.85)'; vx.lineWidth=2.4; vx.lineCap='round';
    vx.beginPath(); vx.moveTo(x1,y1); vx.lineTo(x2,y2); vx.stroke();
  }
  requestAnimationFrame(drawViz);
}
drawViz();
function setState(s, label){
  reactor.parentElement.classList.remove('listening','speaking'); vState = s;
  if(s==='listening'){ reactor.parentElement.classList.add('listening'); $('#modeState').textContent='LISTENING'; }
  else if(s==='speaking'){ reactor.parentElement.classList.add('speaking'); $('#modeState').textContent='SPEAKING'; }
  else { $('#modeState').textContent = s==='processing' ? 'PROCESSING' : 'STANDBY'; }
  statusEl.textContent = label || s.toUpperCase();
}
function addMsg(text, who){
  const d=document.createElement('div');
  d.className='msg '+(who==='user'?'u':'j');
  d.textContent=(who==='user'?'':assistantName+' · ')+text;
  logEl.appendChild(d); logEl.scrollTop=logEl.scrollHeight;
  while(logEl.children.length>30) logEl.removeChild(logEl.firstChild);
}
let voice=null;
function pickVoice(){
  const vs=speechSynthesis.getVoices();
  voice = vs.find(v=>/uk english male|daniel|arthur|george/i.test(v.name))
       || vs.find(v=>v.lang==='en-GB') || vs.find(v=>/male/i.test(v.name))
       || vs.find(v=>v.lang && v.lang.startsWith('en')) || vs[0];
}
speechSynthesis.onvoiceschanged=pickVoice; pickVoice();
function say(text){
  return new Promise(res=>{
    if(!('speechSynthesis' in window)) return res();
    const u=new SpeechSynthesisUtterance(text);
    if(voice) u.voice=voice; u.rate=1.02; u.pitch=.9;
    u.onend=res; u.onerror=res; speechSynthesis.speak(u);
  });
}
let busy=false;
async function send(query){
  query=(query||'').trim(); if(!query||busy) return;
  busy=true; addMsg(query,'user'); $('#input').value=''; setState('processing','PROCESSING');
  try{
    const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query})});
    const data=await r.json();
    for(const line of (data.responses||[])){
      addMsg(line,'jarvis');
      setState('speaking', line.length>60 ? 'SPEAKING' : line.toUpperCase().slice(0,26));
      await say(line);
    }
    if(data.keep_open===false){ setState('idle','OFFLINE'); $('#sysState').textContent='OFFLINE'; }
  }catch(e){ addMsg('Connection to core lost.','jarvis'); }
  setState('idle','STANDBY'); busy=false; $('#input').focus();
}
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec=null, recognizing=false;
if(SR){
  rec=new SR(); rec.lang='en-US'; rec.interimResults=false; rec.maxAlternatives=1;
  rec.onstart=()=>{recognizing=true; $('#mic').classList.add('rec'); setState('listening','LISTENING');};
  rec.onend=()=>{recognizing=false; $('#mic').classList.remove('rec'); if(!busy) setState('idle','STANDBY');};
  rec.onerror=()=>{recognizing=false; $('#mic').classList.remove('rec');};
  rec.onresult=e=>{ const t=e.results[0][0].transcript; send(t); };
}
$('#mic').onclick=()=>{
  if(!SR){ addMsg('Voice input needs Chrome or Edge. You can still type.','jarvis'); return; }
  if(recognizing){ rec.stop(); } else { try{ speechSynthesis.cancel(); rec.start(); }catch(_){} }
};
$('#send').onclick=()=>send($('#input').value);
$('#input').addEventListener('keydown',e=>{ if(e.key==='Enter') send($('#input').value); });
function tick(){
  const now=new Date();
  $('#clock').textContent=now.toLocaleTimeString();
  $('#dateline').textContent=now.toLocaleDateString(undefined,{weekday:'long',month:'short',day:'numeric'});
  $('#tCore').textContent=(96+Math.random()*3.5).toFixed(1)+'%';
}
setInterval(tick,1000); tick();
fetch('/api/capabilities').then(r=>r.json()).then(c=>{
  assistantName=c.assistant_name||'Jarvis';
  const labels={gemini:'AI Brain',weather:'Weather',wikipedia:'Wikipedia',
    speech_recognition:'Mic',text_to_speech:'Voice',spotify:'Spotify',
    calendar:'Calendar',psutil:'Telemetry'};
  const box=$('#caps'); box.innerHTML='';
  for(const k in labels){ const s=document.createElement('span');
    s.className='cap '+(c[k]?'on':'off'); s.textContent=labels[k]; box.appendChild(s); }
}).catch(()=>{});
setInterval(async()=>{
  try{ const r=await fetch('/api/events'); const d=await r.json();
    for(const ev of (d.events||[])){ addMsg(ev.text,'jarvis'); if(!busy) say(ev.text); }
  }catch(_){}
},4000);
setTimeout(()=>{ setState('idle','STANDBY'); $('#input').focus(); }, 1200);
</script>
</body>
</html>
"""


def _web_capabilities():
    return {
        "assistant_name": ASSISTANT_NAME,
        "user_title": USER_TITLE,
        "speech_recognition": HAS_SR,
        "text_to_speech": HAS_TTS,
        "gemini": active_ai_provider() is not None,
        "ai_provider": active_ai_provider() or "none",
        "wikipedia": HAS_WIKI,
        "psutil": HAS_PSUTIL,
        "weather": bool(WEATHER_API_KEY),
        "spotify": HAS_SPOTIFY and bool(SPOTIFY_CLIENT_ID),
        "calendar": HAS_GCAL,
    }


def _make_handler():
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, HUD_HTML, "text/html; charset=utf-8")
            elif self.path == "/api/capabilities":
                self._send(200, _web_capabilities())
            elif self.path == "/api/events":
                self._send(200, {"events": drain_events()})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/command":
                self._send(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length) or "{}")
            except Exception:
                self._send(400, {"error": "bad request"})
                return
            self._send(200, process_command(data.get("query", "")))

    return Handler


def run_web(args):
    from http.server import ThreadingHTTPServer

    print_capabilities()
    threading.Thread(target=_reminder_worker, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), _make_handler())
    url = f"http://{args.host}:{args.port}"
    print("=" * 50)
    print(f"  {ASSISTANT_NAME} HUD is online at  {url}")
    print("  Open that address in Chrome or Edge for voice.")
    print("  Press Ctrl+C (or stop in PyCharm) to shut down.")
    print("=" * 50)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\nShutting down {ASSISTANT_NAME}. Goodbye.")
        server.shutdown()


def main():
    parser = argparse.ArgumentParser(description=f"{ASSISTANT_NAME} assistant")
    parser.add_argument("--cli", action="store_true",
                        help="run the terminal assistant instead of the web HUD")
    parser.add_argument("--text", action="store_true", help="CLI: force text mode")
    parser.add_argument("--voice", action="store_true", help="CLI: force voice mode")
    parser.add_argument("--no-gui", action="store_true", help="CLI: no Tk status window")
    parser.add_argument("--host", default="127.0.0.1", help="web host")
    parser.add_argument("--port", type=int, default=5000, help="web port")
    parser.add_argument("--no-browser", action="store_true",
                        help="web: don't auto-open a browser tab")
    args = parser.parse_args()

    # Default (e.g. clicking Run in PyCharm) launches the Iron Man HUD.
    if args.cli or args.text or args.voice:
        run_cli(args)
    else:
        run_web(args)


if __name__ == "__main__":
    main()

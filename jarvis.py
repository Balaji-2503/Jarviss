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
WEATHER_API_KEY = env("OPENWEATHER_API_KEY")
WOLFRAM_APP_ID = env("WOLFRAM_APP_ID")
EMAIL_SENDER = env("EMAIL_SENDER")
EMAIL_PASSWORD = env("EMAIL_PASSWORD")

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
    """Say something out loud (if TTS is available) and always print it."""
    print(f"{ASSISTANT_NAME.upper()}: {text}")
    engine = _get_engine()
    if engine is not None:
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"(speech error: {e})")


# ================= GEMINI (lazy, with memory) =================
_gemini_model = None
_chat_session = None


def _get_chat():
    """Return a Gemini chat session that keeps conversation history, or None."""
    global _gemini_model, _chat_session
    if not HAS_GEMINI or not GEMINI_API_KEY:
        return None
    if _chat_session is None:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            system = (
                f"You are {ASSISTANT_NAME}, a concise, helpful voice assistant "
                f"inspired by Iron Man's AI. Address the user as '{USER_TITLE}'. "
                "Keep answers short and speakable unless asked for detail."
            )
            try:
                _gemini_model = genai.GenerativeModel(
                    GEMINI_MODEL, system_instruction=system
                )
            except TypeError:
                # Older SDKs don't support system_instruction.
                _gemini_model = genai.GenerativeModel(GEMINI_MODEL)
            _chat_session = _gemini_model.start_chat(history=[])
        except Exception as e:
            print(f"Gemini init failed: {e}")
            return None
    return _chat_session


def ask_ai(prompt):
    """Ask Gemini, remembering conversation context. Returns text or None."""
    chat = _get_chat()
    if chat is None:
        return None
    try:
        resp = chat.send_message(prompt)
        return (resp.text or "").strip()
    except Exception as e:
        print(f"Gemini error: {e}")
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
                        speak(f"Reminder, {USER_TITLE}: {r['text']}")
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
        speak(f"{USER_TITLE}, your {seconds} second timer is up.")
    threading.Thread(target=_timer, daemon=True).start()
    speak(f"Timer set for {seconds} seconds.")


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


# ================= HELP =================
def show_help():
    speak(f"Here are some things you can ask me, {USER_TITLE}.")
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
        "play <song>                      - play on YouTube",
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
            text = q.split("to", 1)[1].split("in")[0].strip()
            minutes = _extract_number(q.split("in", 1)[1], 5)
            add_reminder(text, minutes)
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
        play_on_youtube(q[5:].replace("on youtube", "").strip()); return True

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
    else:
        speak("I'm not sure how to help with that, and my AI brain isn't configured. "
              "Say 'help' to hear what I can do.")
    return True


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
    caps = {
        "Speech recognition (mic input)": HAS_SR,
        "Text-to-speech (voice output)": HAS_TTS,
        "Gemini AI brain": HAS_GEMINI and bool(GEMINI_API_KEY),
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


def main():
    parser = argparse.ArgumentParser(description=f"{ASSISTANT_NAME} assistant")
    parser.add_argument("--text", action="store_true", help="force text mode")
    parser.add_argument("--voice", action="store_true", help="force voice mode")
    parser.add_argument("--no-gui", action="store_true", help="don't open the GUI window")
    args = parser.parse_args()

    print_capabilities()

    mode = "auto"
    if args.text:
        mode = "text"
    elif args.voice:
        mode = "voice"
    listener = Listener(mode=mode)

    # Start the persistent-reminder background worker.
    threading.Thread(target=_reminder_worker, daemon=True).start()

    gui = None if args.no_gui else start_gui()
    if gui is not None:
        threading.Thread(target=run_jarvis, args=(listener, gui), daemon=True).start()
        gui.root.mainloop()
    else:
        run_jarvis(listener, None)


if __name__ == "__main__":
    main()

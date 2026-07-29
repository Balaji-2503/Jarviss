"""
JARVIS web server — serves the Iron Man style HUD and bridges the browser to
the assistant core in jarvis.py.

Runs on Python's standard library only (no Flask needed). Voice happens in the
browser (Web Speech API), so you don't need a microphone driver or PyAudio.

Run:
    python server.py               # then open http://localhost:5000
    python server.py --port 8080
    python server.py --no-browser  # don't auto-open a browser tab
"""

import os
import json
import argparse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jarvis

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _capabilities():
    return {
        "assistant_name": jarvis.ASSISTANT_NAME,
        "user_title": jarvis.USER_TITLE,
        "speech_recognition": jarvis.HAS_SR,
        "text_to_speech": jarvis.HAS_TTS,
        "gemini": jarvis.HAS_GEMINI and bool(jarvis.GEMINI_API_KEY),
        "wikipedia": jarvis.HAS_WIKI,
        "psutil": jarvis.HAS_PSUTIL,
        "weather": bool(jarvis.WEATHER_API_KEY),
        "spotify": jarvis.HAS_SPOTIFY and bool(jarvis.SPOTIFY_CLIENT_ID),
        "calendar": jarvis.HAS_GCAL,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the console clean

    def _send(self, code, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            path = os.path.join(WEB_DIR, "index.html")
            try:
                with open(path, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "HUD file not found", "text/plain")
        elif self.path == "/api/capabilities":
            self._send(200, _capabilities())
        elif self.path == "/api/events":
            self._send(200, {"events": jarvis.drain_events()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/command":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or "{}")
            query = data.get("query", "")
        except Exception:
            self._send(400, {"error": "bad request"})
            return
        result = jarvis.process_command(query)
        self._send(200, result)


def main():
    parser = argparse.ArgumentParser(description="JARVIS web server")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    # Start the persistent-reminder worker so reminders/alarms reach the HUD.
    import threading
    threading.Thread(target=jarvis._reminder_worker, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print("=" * 50)
    print(f"  JARVIS HUD running at {url}")
    print("  Press Ctrl+C to stop.")
    print("=" * 50)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down JARVIS HUD.")
        server.shutdown()


if __name__ == "__main__":
    main()

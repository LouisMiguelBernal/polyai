"""
PolyAI — Windows System Tray Application
Runs the FastAPI backend in the background with a tray icon.
Double-click to open dashboard, right-click for menu.
"""
import os, sys, subprocess, threading, webbrowser, signal, time
from pathlib import Path

# Ensure we're in the right directory
APP_DIR = Path(__file__).parent.resolve()
os.chdir(APP_DIR)

# Load .env manually for port
PORT = "8000"
env_file = APP_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("BACKEND_PORT="):
            PORT = line.split("=", 1)[1].strip()

DASHBOARD_URL = f"http://localhost:{PORT}"
ICON_PATH = APP_DIR / "polyai.ico"

# Brave browser path
BRAVE_PATHS = [
    Path(os.environ.get("PROGRAMFILES", "")) / "BraveSoftware/Brave-Browser/Application/brave.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "BraveSoftware/Brave-Browser/Application/brave.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware/Brave-Browser/Application/brave.exe",
]
BRAVE_EXE = next((p for p in BRAVE_PATHS if p.exists()), None)

def open_in_brave(url):
    """Open URL in Brave browser, fallback to default browser."""
    if BRAVE_EXE:
        subprocess.Popen([str(BRAVE_EXE), url])
    else:
        webbrowser.open(url)

# ── Backend Process ──────────────────────────────────────────
server_process = None

def start_server():
    global server_process
    if server_process and server_process.poll() is None:
        return  # already running
    server_process = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

def stop_server():
    global server_process
    if server_process and server_process.poll() is None:
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
    server_process = None

def is_server_running():
    return server_process is not None and server_process.poll() is None

# ── Health monitor — auto-restart if crashed ─────────────────
def health_monitor():
    while True:
        time.sleep(30)
        if server_process and server_process.poll() is not None:
            start_server()

# ── Tray Icon ────────────────────────────────────────────────
def create_tray():
    import pystray
    from PIL import Image

    if ICON_PATH.exists():
        icon_image = Image.open(str(ICON_PATH))
    else:
        # Fallback: green square
        icon_image = Image.new("RGB", (64, 64), (0, 229, 160))

    def on_open(icon, item):
        open_in_brave(DASHBOARD_URL)

    def on_restart(icon, item):
        stop_server()
        time.sleep(1)
        start_server()

    def on_quit(icon, item):
        stop_server()
        icon.stop()

    def on_status(icon, item):
        return

    menu = pystray.Menu(
        pystray.MenuItem("PolyAI — Smart Money Tracker", on_status, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Dashboard", on_open, default=True),
        pystray.MenuItem("Restart Server", on_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("PolyAI", icon_image, "PolyAI", menu)
    return icon

# ── Main ─────────────────────────────────────────────────────
if __name__ == "__main__":
    # Start the backend
    start_server()

    # Start health monitor in background
    t = threading.Thread(target=health_monitor, daemon=True)
    t.start()

    # Open dashboard after a short delay
    def delayed_open():
        time.sleep(4)
        open_in_brave(DASHBOARD_URL)
    threading.Thread(target=delayed_open, daemon=True).start()

    # Run tray icon (blocks until quit)
    tray = create_tray()
    tray.run()

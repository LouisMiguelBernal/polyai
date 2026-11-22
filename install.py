"""
PolyAI Installer — Adds to Windows Startup + creates pinnable shortcuts.
Run: python install.py
"""
import os, sys, subprocess, winreg
from pathlib import Path

APP_DIR = Path(__file__).parent.resolve()
PYW_PATH = APP_DIR / "polyai_app.pyw"
ICON_PATH = APP_DIR / "polyai.ico"
PYTHONW = Path(sys.executable).parent / "pythonw.exe"

if not PYTHONW.exists():
    PYTHONW = Path(sys.executable)

APP_NAME = "PolyAI"
COMMAND = f'"{PYTHONW}" "{PYW_PATH}"'


def get_desktop_path():
    try:
        result = subprocess.run(
            ["powershell", "-Command", '[Environment]::GetFolderPath("Desktop")'],
            capture_output=True, text=True, timeout=10
        )
        p = result.stdout.strip()
        if p and Path(p).exists():
            return Path(p)
    except Exception:
        pass
    for candidate in [
        Path(os.environ.get("USERPROFILE", "")) / "OneDrive" / "Desktop",
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
    ]:
        if candidate.exists():
            return candidate
    return Path(os.environ.get("USERPROFILE", "")) / "Desktop"


def add_to_startup():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, COMMAND)
        winreg.CloseKey(key)
        print(f"[OK] Added to Windows startup")
        return True
    except Exception as e:
        print(f"[FAIL] Could not add to startup: {e}")
        return False


def remove_from_startup():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        print(f"[OK] Removed from Windows startup")
    except FileNotFoundError:
        print("[OK] Was not in startup")
    except Exception as e:
        print(f"[FAIL] {e}")


def create_shortcut(folder, name="PolyAI"):
    """Create a proper .lnk shortcut (pinnable to taskbar)."""
    try:
        import win32com.client
        shortcut_path = folder / f"{name}.lnk"
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(shortcut_path))
        shortcut.TargetPath = str(PYTHONW)
        shortcut.Arguments = f'"{PYW_PATH}"'
        shortcut.WorkingDirectory = str(APP_DIR)
        shortcut.IconLocation = f"{ICON_PATH},0" if ICON_PATH.exists() else ""
        shortcut.Description = "PolyAI — Smart Money Tracker"
        shortcut.Save()
        print(f"[OK] Shortcut created: {shortcut_path}")
        return shortcut_path
    except Exception as e:
        print(f"[FAIL] Shortcut error: {e}")
        return None


def main():
    print()
    print("  ====================================")
    print("       PolyAI Installer")
    print("       Smart Money Tracker v2.0")
    print("  ====================================")
    print()
    print(f"  App:    {PYW_PATH}")
    print(f"  Python: {PYTHONW}")
    print(f"  Icon:   {ICON_PATH}")
    print()

    # Generate icon if missing
    if not ICON_PATH.exists():
        print("[...] Generating icon...")
        os.system(f'"{sys.executable}" "{APP_DIR / "generate_icon.py"}"')

    # 1. Add to startup
    print("[1/3] Adding to Windows startup...")
    add_to_startup()

    # 2. Desktop shortcut (.lnk — pinnable)
    print("[2/3] Creating desktop shortcut...")
    desktop = get_desktop_path()
    desktop.mkdir(parents=True, exist_ok=True)
    lnk_path = create_shortcut(desktop)

    # 3. Start Menu shortcut (makes it searchable + pinnable)
    print("[3/3] Creating Start Menu entry...")
    start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if start_menu.exists():
        create_shortcut(start_menu)
    else:
        print("[SKIP] Start Menu folder not found")

    # Remove old .bat if it exists
    old_bat = desktop / "PolyAI.bat"
    if old_bat.exists():
        old_bat.unlink()
        print("[OK] Removed old .bat launcher")

    print()
    print("  ====================================")
    print("            ALL DONE!")
    print("  ====================================")
    print()
    print("  PolyAI will now:")
    print("  - Auto-start on Windows boot")
    print("  - Run silently in the system tray")
    print("  - Open dashboard in Brave browser")
    print()
    print("  TO PIN TO TASKBAR:")
    print("  1. Right-click 'PolyAI' on Desktop")
    print("  2. Select 'Show more options'")
    print("  3. Click 'Pin to taskbar'")
    print()
    print("  Or search 'PolyAI' in Start Menu")
    print("  and pin from there.")
    print()
    print("  To uninstall: python install.py --remove")
    print()


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove_from_startup()
    else:
        main()

"""The Badge Arcade Manager's tabs, one module each; manager.py puts them in one window.

Each *_tab module is a mixin: its build_*_tab method lays the tab out, and the methods
next to it do the tab's work. manager.Manager inherits them all, so the tabs reach each
other and what the window shares (the settings, the SpotPass key, the server and proxy,
the status bar) through self.

Importing this package puts the SpotPass tools, the server, the proxy and the helper on
sys.path, so the tab modules can import serve, hotspot, badge_arcade and gui.
"""

from pathlib import Path
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
LETTER_DIR = ROOT / "spotpass-letter"
HELPER_DIR = ROOT / "helper"
LOG_DIR = SERVER_DIR / "logs"
PID_FILE = LOG_DIR / "manager_pids.json"
SETTINGS_FILE = ROOT / "manager_settings.json"
sys.path[:0] = [str(LETTER_DIR), str(SERVER_DIR), str(SERVER_DIR / "mitm"), str(HELPER_DIR)]

TITLE = "Badge Arcade Manager"  # the window's and every dialog's title
# Hotspot mode needs Windows (its mobile hotspot, hosts file and firewall); elsewhere the 3DS uses the proxy
WINDOWS = sys.platform == "win32"


def load_settings() -> dict:
	settings = {"connection": "hotspot" if WINDOWS else "proxy"}
	try:
		settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
	except (FileNotFoundError, ValueError):
		pass
	if not WINDOWS:
		settings["connection"] = "proxy"
	return settings


def save_settings(settings: dict) -> None:
	SETTINGS_FILE.write_text(json.dumps(settings, indent="\t") + "\n", encoding="utf-8")


def open_path(path: Path | str) -> None:
	"""Opens a folder in the system's file browser. OSError if there's nothing to open it with."""
	if WINDOWS:
		os.startfile(path)
	else:
		subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
			stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

"""Installs and sets up the Badge Arcade server. Setup.bat runs this.

  python install.py            install the packages, create server/config.json, set up the proxy
  python install.py --check    only list what's done and what's still missing
  python install.py --packages only (re)install the Python packages (update.py runs this)

Safe to run again: it keeps an existing config.json and skips finished steps.
The manager window's Setup tab uses checklist() and the steps below.
"""

from dataclasses import dataclass
from pathlib import Path
import argparse
import importlib.util
import json
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SERVER_DIR = ROOT / "server"
MITM_DIR = SERVER_DIR / "mitm"
LETTER_DIR = ROOT / "spotpass-letter"
OTHER_DIR = ROOT / "other"
CONFIG = SERVER_DIR / "config.json"
CONFIG_EXAMPLE = SERVER_DIR / "config.example.json"
NEX_KEYS = SERVER_DIR / "nex-keys.txt"
SD_CARD = MITM_DIR / "sd-card"
SSL_PATCH = SD_CARD / "luma" / "sysmodules" / "0004013000002F02.ips"
CLIENT_CERT = MITM_DIR / "mitmproxy-nintendo" / "client-certificates" / "CTR-common.pem"

# The archived SpotPass files the tools start from (see README.md)
SPOTPASS_FILES = ["data_v131-2022-12-29-09-40-NA.enc", "playinfo_v131-2022-12-29-09-40-NA.enc", "allbadge_v131.dat.boss"]
# Modules the server and the SpotPass tools import
MODULES = ["Crypto", "nintendo", "anynet", "anyio", "OpenSSL", "multidict"]


@dataclass
class Check:
	name: str
	ok: bool
	hint: str  # what to do about it (or what it's for, once done)
	fix: str | None = None  # "packages", "config" or "proxy": install.py can do it
	folder: Path | None = None  # where the user puts the file themselves


# ----- steps -----

def packages_installed() -> bool:
	return all(importlib.util.find_spec(module) for module in MODULES)


def install_packages() -> None:
	pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
	if sys.platform == "win32":
		# netifaces (an anynet dependency) needs a C compiler on Windows and the
		# server doesn't use it, so install around it
		subprocess.check_call(pip + ["pycryptodome>=3.20,<4", "anyio~=4.0", "pyopenssl>=24.0", "multidict>=6.0", "netifaces-plus"])
		subprocess.check_call(pip + ["--no-deps", "anynet~=1.2", "nintendoclients==5.0.0"])
	else:
		subprocess.check_call(pip + ["-r", str(SERVER_DIR / "requirements.txt")])


def create_config() -> bool:
	"""Creates server/config.json with a random kerberos_password. False if it exists."""
	if CONFIG.exists():
		return False
	config = json.loads(CONFIG_EXAMPLE.read_text(encoding="utf-8"))
	text = CONFIG_EXAMPLE.read_text(encoding="utf-8").replace(
		json.dumps(config["kerberos_password"]), json.dumps(secrets.token_urlsafe(24)))
	CONFIG.write_text(text, encoding="utf-8")
	return True


def proxy_ready() -> bool:
	venv_python = MITM_DIR / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
	return venv_python.exists() and CLIENT_CERT.exists() and SSL_PATCH.exists()


def setup_proxy() -> None:
	subprocess.check_call([sys.executable, str(MITM_DIR / "setup_mitm.py")])


def nex_password_known() -> bool:
	if NEX_KEYS.exists():
		return True
	try:
		return bool(json.loads(CONFIG.read_text(encoding="utf-8")).get("accounts"))
	except (OSError, ValueError):
		return False


# ----- checklist -----

def checklist() -> list[Check]:
	missing_spotpass = [name for name in SPOTPASS_FILES if not (OTHER_DIR / name).exists()]
	return [
		Check("Python packages", packages_installed(),
			"Installed." if packages_installed() else "Not installed yet.", "packages"),
		Check("Server settings (server/config.json)", CONFIG.exists(),
			"Created." if CONFIG.exists() else "Not created yet.", "config"),
		Check("Proxy (mitmproxy and Pretendo's 3DS files)", proxy_ready(),
			"Set up." if proxy_ready() else "Not set up yet (downloads about 100 MB).", "proxy"),
		Check("NoSSL patch on the 3DS's SD card", SSL_PATCH.exists(),
			"Copy the luma folder from server/mitm/sd-card to the root of the SD card (once), and turn on "
			"\"Enable game patching\" in Luma's settings (hold SELECT while powering on)."
			if SSL_PATCH.exists() else "Appears once the proxy is set up.", folder=SD_CARD),
		# Optional: when Badge Arcade logs in through the Nintendo Network ID account
		# server, the server picks the NEX password itself
		Check("NEX password (server/nex-keys.txt, optional)", True,
			"Found." if nex_password_known() else "Usually not needed. Only if the server's log says \"No NEX password "
			"known\": dump it with get_3ds_pid_password (see server/README.md).", folder=SERVER_DIR),
		Check("boot9.bin (spotpass-letter/)", (LETTER_DIR / "boot9.bin").exists(),
			"Found." if (LETTER_DIR / "boot9.bin").exists() else "Needed to switch machines and give free plays. Dump it with GodMode9.",
			folder=LETTER_DIR),
		Check("Game key (spotpass-letter/badge_arcade_hmac.key)", (LETTER_DIR / "badge_arcade_hmac.key").exists(),
			"Found." if (LETTER_DIR / "badge_arcade_hmac.key").exists() else "Needed for free plays. See README.md.",
			folder=LETTER_DIR),
		Check("SpotPass files (other/)", not missing_spotpass,
			"Found." if not missing_spotpass else f"Missing: {', '.join(missing_spotpass)}. See README.md.", folder=OTHER_DIR),
	]


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--check", action="store_true", help="only show what's missing")
	parser.add_argument("--packages", action="store_true", help="only (re)install the Python packages")
	args = parser.parse_args()

	if sys.version_info < (3, 12):
		print("Python 3.12 or newer is required: https://www.python.org/downloads/")
		return 1

	if not args.check:
		if args.packages:
			print("Installing the server's Python packages...")
			install_packages()
			return 0
		if not packages_installed():
			print("Installing the server's Python packages...")
			install_packages()
		if create_config():
			print(f"Created {CONFIG.relative_to(ROOT)} with a random kerberos_password.")
		if not proxy_ready():
			print("Setting up the proxy...")
			setup_proxy()

	print()
	for check in checklist():
		print(f"  [{'x' if check.ok else ' '}] {check.name}: {check.hint}")
	print("\nOpen \"Badge Arcade Manager.bat\" to start the server. README.md explains the rest.")
	return 0


if __name__ == "__main__":
	sys.exit(main())

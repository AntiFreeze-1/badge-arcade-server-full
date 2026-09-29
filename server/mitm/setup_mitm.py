"""One-time setup of Pretendo's mitmproxy-nintendo for the Badge Arcade server.

  python mitm/setup_mitm.py

  1. Creates mitm/.venv and installs mitmproxy (requirements-mitm.txt).
  2. Downloads Pretendo's mitmproxy-nintendo into mitm/mitmproxy-nintendo
     (3DS client certificate, NoSSL patch, reference configs).
  3. Puts the 3DS NoSSL patch in mitm/sd-card/, ready to copy to the SD card.

Safe to run again; it refreshes everything.
"""

from pathlib import Path
import io
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
PRETENDO_DIR = HERE / "mitmproxy-nintendo"
PRETENDO_ZIP = "https://codeload.github.com/PretendoNetwork/mitmproxy-nintendo/zip/refs/heads/master"
SSL_PATCH = "0004013000002F02.ips"  # Patches the 3DS SSL module to accept any certificate
SD_CARD = HERE / "sd-card"


def venv_executable(name: str) -> Path:
	if sys.platform == "win32":
		return VENV / "Scripts" / f"{name}.exe"
	return VENV / "bin" / name


def install_mitmproxy() -> None:
	if not venv_executable("python").exists():
		print(f"Creating virtual environment in {VENV}")
		venv.EnvBuilder(with_pip=True).create(VENV)

	print("Installing mitmproxy (this can take a minute)")
	subprocess.check_call([
		str(venv_executable("python")), "-m", "pip", "install",
		"--disable-pip-version-check", "--quiet", "-r", str(HERE / "requirements-mitm.txt"),
	])
	subprocess.check_call([str(venv_executable("mitmdump")), "--version"])


def download_pretendo_files() -> None:
	print(f"Downloading Pretendo's mitmproxy-nintendo from {PRETENDO_ZIP}")
	with urllib.request.urlopen(PRETENDO_ZIP, timeout=60) as response:
		archive = zipfile.ZipFile(io.BytesIO(response.read()))

	if PRETENDO_DIR.exists():
		shutil.rmtree(PRETENDO_DIR)

	for member in archive.infolist():
		# Strip the "mitmproxy-nintendo-master/" prefix
		relative = member.filename.split("/", 1)[-1]
		if member.is_dir() or not relative:
			continue
		target = (PRETENDO_DIR / relative).resolve()
		if not target.is_relative_to(PRETENDO_DIR):
			raise ValueError(f"Unexpected path in archive: {member.filename}")
		target.parent.mkdir(parents=True, exist_ok=True)
		target.write_bytes(archive.read(member))

	for required in ("client-certificates/CTR-common.pem", f"ssl-patches/{SSL_PATCH}"):
		if not (PRETENDO_DIR / required).is_file():
			raise FileNotFoundError(f"{required} is missing from Pretendo's repository")


def prepare_sd_card() -> Path:
	destination = SD_CARD / "luma" / "sysmodules" / SSL_PATCH
	destination.parent.mkdir(parents=True, exist_ok=True)
	shutil.copyfile(PRETENDO_DIR / "ssl-patches" / SSL_PATCH, destination)
	return destination


def main() -> int:
	# Keep our messages in order with the output of pip and mitmdump
	sys.stdout.reconfigure(line_buffering=True)

	if sys.version_info < (3, 12):
		print("Python 3.12 or newer is required")
		return 1

	install_mitmproxy()
	download_pretendo_files()
	patch = prepare_sd_card()

	print(f"""
Setup complete.

Next steps:
  1. Copy the "luma" folder from {SD_CARD}
     to the root of the 3DS SD card (it contains {patch.name}, the NoSSL patch).
     In Luma3DS's configuration menu (hold SELECT while powering on),
     make sure "Enable game patching" is turned on.
  2. Open "Badge Arcade Manager.bat" in the project folder, and connect the 3DS
     from its Setup tab (through the PC's hotspot, or through the proxy).
     By hand: python -m badge_arcade config.json, and python mitm/start_mitm.py
""")
	return 0


if __name__ == "__main__":
	sys.exit(main())

"""Checks for and installs new versions of the Badge Arcade server.

  python update.py              check, then ask before installing
  python update.py --check      only check (exit code 0: up to date, 10: update available, 1: error)
  python update.py --apply      install the newest version (asks first; --yes doesn't)

The newest version is whatever is on the project's main branch on GitHub: when
its version.txt holds a higher number than this folder's version.txt, there's
an update. A git checkout is updated with "git pull --ff-only"; otherwise the
main branch's files are downloaded and copied over this folder. Your own files are never touched: server/config.json, saves
(server/data/), SpotPass files (other/), keys and dumps, the manager's
settings, and anything else .gitignore lists. The files an update replaces are
zipped into backups/ first. Stop the server and proxy before updating.

Older installs update with the update.py they have, so the project keeps what those
need: version.txt (from 1.0.1 on), and a VERSION file with a GitHub release of the same
number (1.0.0, which read releases). Setup.bat and "Badge Arcade Manager.bat" never
change: Windows reads a running .bat from where it left off, so a new one would run
half-lines of it. install.py --packages keeps working and doesn't fail for the helper.

Uses only Python's standard library, so it works before the packages are
installed. BADGE_ARCADE_VERSION_URL and BADGE_ARCADE_ZIP_URL override where
the version number and the files are downloaded from.
"""

from pathlib import Path, PurePosixPath
import argparse
import datetime
import fnmatch
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION_NAME = "version.txt"
VERSION_FILE = ROOT / VERSION_NAME
REPOSITORY = "AntiFreeze-1/badge-arcade-server-full"
BRANCH = "main"
VERSION_URL = os.environ.get("BADGE_ARCADE_VERSION_URL",
	f"https://raw.githubusercontent.com/{REPOSITORY}/refs/heads/{BRANCH}/{VERSION_NAME}")
ZIP_URL = os.environ.get("BADGE_ARCADE_ZIP_URL", f"https://github.com/{REPOSITORY}/archive/refs/heads/{BRANCH}.zip")
CHANGES_URL = f"https://github.com/{REPOSITORY}/commits/{BRANCH}"
BACKUP_DIR = ROOT / "backups"
# Files installed by the last zip update, so the next one can remove files a new version dropped
MANIFEST = ROOT / ".update-manifest.json"

UP_TO_DATE, UPDATE_AVAILABLE, ERROR = 0, 10, 1

# Never written or removed by an update, whatever the download contains
# (.gitignore's patterns are added to these)
PROTECTED = [
	".git/", ".update-manifest.json", "/backups/", "/other/", "/manager_settings.json",
	"/server/config.json", "/server/nex-keys.txt", "/server/data/", "/server/backups/", "/server/logs/",
	"/server/mitm/.venv/", "*.key", "*.code", "boot9*.bin", "*.boss", "*.enc",
	# Your own badges and machines (the Badges, Machine editor and Custom weeks tabs)
	"/helper/workspace/", "/helper/helper_settings.json", "/helper/helper_state.json", "/helper/extra/", "/helper/other/",
]


class UpdateError(Exception):
	pass


# ----- versions -----

def current_version() -> str:
	try:
		return VERSION_FILE.read_text(encoding="utf-8").strip()
	except OSError:
		return "0"


def parse_version(text: str) -> tuple[int, ...]:
	"""'v1.10.0' -> (1, 10, 0). Raises ValueError for anything else."""
	parts = text.strip().removeprefix("v").split(".")
	if not all(part.isdigit() for part in parts):
		raise ValueError(f"not a version number: {text!r}")
	numbers = [int(part) for part in parts]
	while len(numbers) > 1 and numbers[-1] == 0:
		numbers.pop()  # 1.2 == 1.2.0
	return tuple(numbers)


def is_newer(latest: str, current: str) -> bool:
	try:
		current_parts = parse_version(current)
	except ValueError:
		return True  # unknown local version: anything is an update
	return parse_version(latest) > current_parts


def latest_version(timeout: float = 10) -> dict | None:
	"""{"version", "zip_url", "page_url"} for the main branch's version.txt, or None if
	GitHub has no version.txt there (or the repository isn't public)."""
	request = urllib.request.Request(VERSION_URL, headers={"User-Agent": "badge-arcade-updater", "Cache-Control": "no-cache"})
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			text = response.read(100).decode("utf-8", "replace").strip()
	except urllib.error.HTTPError as e:
		if e.code == 404:
			return None
		if e.code in (403, 429):
			raise UpdateError("GitHub is refusing requests for now; try again in an hour") from e
		raise UpdateError(f"GitHub answered {e.code} {e.reason}") from e
	except (urllib.error.URLError, OSError) as e:
		raise UpdateError(f"couldn't reach GitHub: {getattr(e, 'reason', e)}") from e

	try:
		parse_version(text)
	except ValueError as e:
		raise UpdateError(f"the version.txt on GitHub holds {text[:20]!r}, not a version number") from e
	return {"version": text.removeprefix("v"), "zip_url": ZIP_URL, "page_url": CHANGES_URL}


def check(timeout: float = 10) -> dict | None:
	"""The newest version if it's newer than this install, else None."""
	release = latest_version(timeout)
	return release if release and is_newer(release["version"], current_version()) else None


# ----- which files an update may touch -----

def ignore_patterns(root: Path = ROOT) -> list[str]:
	patterns = list(PROTECTED)
	try:
		for line in (root / ".gitignore").read_text(encoding="utf-8").splitlines():
			line = line.strip()
			if line and not line.startswith(("#", "!")):
				patterns.append(line)
	except OSError:
		pass
	return patterns


def is_protected(path: str, patterns: list[str]) -> bool:
	"""Whether a '/'-separated path relative to the project matches a gitignore-style pattern
	(or lies in a directory that does)."""
	parts = PurePosixPath(path).parts
	for pattern in patterns:
		directory_only = pattern.endswith("/")
		pattern = pattern.strip("/") if pattern.startswith("/") else pattern.rstrip("/")
		anchored = "/" in pattern
		for depth in range(1, len(parts) + 1):
			if directory_only and depth == len(parts):
				break  # a file can't match a directory pattern
			if anchored:
				if fnmatch.fnmatchcase("/".join(parts[:depth]), pattern):
					return True
			elif fnmatch.fnmatchcase(parts[depth - 1], pattern):
				return True
	return False


def zip_files(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
	"""The files in a GitHub download zip by project path (without the zip's top folder).
	Raises UpdateError for paths that would land outside the project."""
	files = {}
	for info in archive.infolist():
		if info.is_dir():
			continue
		name = info.filename.replace("\\", "/")
		parts = PurePosixPath(name).parts
		if name.startswith("/") or ":" in parts[0] or ".." in parts or len(parts) < 2:
			raise UpdateError(f"the download contains an unsafe path: {info.filename}")
		files["/".join(parts[1:])] = info
	if VERSION_NAME not in files:
		raise UpdateError(f"the download doesn't look like this project (no {VERSION_NAME})")
	return files


# ----- installing -----

def running_services() -> list[str]:
	"""Names of the parts that are listening on this PC (the server's HTTP port, the proxy)."""
	http_port = 8080
	try:
		http_port = int(json.loads((ROOT / "server" / "config.json").read_text(encoding="utf-8")).get("http_port", 8080))
	except (OSError, ValueError, TypeError):
		pass
	running = []
	for name, port in (("server", http_port), ("proxy", 8083)):
		with socket.socket() as s:
			s.settimeout(0.5)
			if s.connect_ex(("127.0.0.1", port)) == 0:
				running.append(name)
	return running


def is_git_checkout() -> bool:
	return (ROOT / ".git").exists() and shutil.which("git") is not None


def update_git() -> None:
	def git(*args: str) -> str:
		result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
		if result.returncode:
			raise UpdateError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
		return result.stdout

	if git("status", "--porcelain", "--untracked-files=no").strip():
		raise UpdateError("this git checkout has local changes; commit or stash them, then update")
	git("pull", "--ff-only")


def download(url: str, timeout: float = 60) -> bytes:
	request = urllib.request.Request(url, headers={"User-Agent": "badge-arcade-updater"})
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			return response.read()
	except (urllib.error.URLError, OSError) as e:
		raise UpdateError(f"couldn't download the new version: {getattr(e, 'reason', e)}") from e


def install_zip(data: bytes, root: Path = ROOT, backup_dir: Path | None = None, newer_than: str | None = None) -> Path | None:
	"""Copies a downloaded zip's files over root. Returns the backup of the replaced files (None if none).
	With newer_than, refuses a download whose version.txt isn't newer than that version."""
	backup_dir = backup_dir or root / "backups"
	try:
		archive = zipfile.ZipFile(io.BytesIO(data))
	except zipfile.BadZipFile as e:
		raise UpdateError("the download isn't a valid zip") from e

	patterns = ignore_patterns(root)
	with archive:
		all_files = zip_files(archive)
		if newer_than is not None:
			downloaded = archive.read(all_files[VERSION_NAME]).decode("utf-8", "replace").strip()
			if not is_newer(downloaded, newer_than):
				# raw.githubusercontent.com can show a new version.txt a few minutes before the zip has it
				raise UpdateError(f"the download is version {downloaded[:20]}, not newer than {newer_than}; try again in a few minutes")
		files = {path: info for path, info in all_files.items() if not is_protected(path, patterns)}
		try:
			previous = set(json.loads((root / MANIFEST.name).read_text(encoding="utf-8")))
		except (OSError, ValueError):
			previous = set()
		removed = sorted(path for path in previous - set(files) if not is_protected(path, patterns))

		# Back up everything that is about to change
		replaced = [path for path in [*files, *removed] if (root / path).is_file()]
		backup = None
		if replaced:
			backup_dir.mkdir(parents=True, exist_ok=True)
			backup = backup_dir / f"before-update-{datetime.datetime.now():%Y%m%d-%H%M%S}.zip"
			with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as out:
				for path in replaced:
					out.write(root / path, path)

		for path, info in files.items():
			target = root / path
			target.parent.mkdir(parents=True, exist_ok=True)
			tmp = target.with_name(target.name + ".update-tmp")
			with archive.open(info) as source, open(tmp, "wb") as dest:
				shutil.copyfileobj(source, dest)
			tmp.replace(target)
		for path in removed:
			(root / path).unlink(missing_ok=True)

	(root / MANIFEST.name).write_text(json.dumps(sorted(files), indent="\t") + "\n", encoding="utf-8")
	return backup


def install_packages() -> None:
	"""Runs the (possibly new) install.py so new or changed packages are installed."""
	subprocess.run([sys.executable, str(ROOT / "install.py"), "--packages"], check=True)


def apply(release: dict) -> str:
	"""Installs the newest version (from latest_version/check). Returns a summary."""
	running = running_services()
	if running:
		raise UpdateError(f"stop the {' and '.join(running)} first (they're running on this PC)")

	if is_git_checkout():
		update_git()
		summary = "Updated the git checkout."
	else:
		if not release.get("zip_url"):
			raise UpdateError("there's nothing to download")
		backup = install_zip(download(release["zip_url"]), newer_than=current_version())
		summary = f"Installed version {release['version']}."
		if backup:
			summary += f" The replaced files are backed up in {backup.relative_to(ROOT)}."

	try:
		install_packages()
	except (OSError, subprocess.CalledProcessError) as e:
		raise UpdateError(f"{summary} Installing the Python packages failed ({e}); run Setup.bat again") from e
	return summary


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	mode = parser.add_mutually_exclusive_group()
	mode.add_argument("--check", action="store_true", help="only check for an update")
	mode.add_argument("--apply", action="store_true", help="install the newest version")
	parser.add_argument("--yes", action="store_true", help="don't ask before installing")
	args = parser.parse_args(argv)

	print(f"Installed version: {current_version()}")
	try:
		release = latest_version()
	except UpdateError as e:
		print(f"Couldn't check for updates: {e}.")
		return ERROR
	if release is None:
		print(f"There's no version.txt at {VERSION_URL}.")
		return UP_TO_DATE
	if not is_newer(release["version"], current_version()):
		print(f"This is the newest version (GitHub has {release['version']}).")
		return UP_TO_DATE

	print(f"Version {release['version']} is available. What changed: {release['page_url']}")
	if args.check:
		return UPDATE_AVAILABLE
	if not args.yes:
		answer = input("Install it now? [y/N] ")
		if answer.strip().lower() not in ("y", "yes"):
			print("Not updated.")
			return UPDATE_AVAILABLE

	try:
		print(apply(release))
	except UpdateError as e:
		print(f"Update failed: {e}.")
		return ERROR
	print("Done. Start the manager (or the server) again to use the new version.")
	return UP_TO_DATE


if __name__ == "__main__":
	sys.exit(main())

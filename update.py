"""Checks for and installs new versions of the Badge Arcade server.

  python update.py              check, then ask before installing
  python update.py --check      only check (exit code 0: up to date, 10: update available, 1: error)
  python update.py --apply      install the newest release (asks first; --yes doesn't)

New versions are the GitHub releases of this project. A git checkout is
updated with "git pull --ff-only"; otherwise the release's files are copied
over this folder. Your own files are never touched: server/config.json, saves
(server/data/), SpotPass files (other/), keys and dumps, the manager's
settings, and anything else .gitignore lists. The files an update replaces are
zipped into backups/ first. Stop the server and proxy before updating.

Uses only Python's standard library, so it works before the packages are
installed. BADGE_ARCADE_UPDATE_URL overrides the release API address.
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
VERSION_FILE = ROOT / "VERSION"
REPOSITORY = "AntiFreeze-1/badge-arcade-server-full"
RELEASE_API = os.environ.get("BADGE_ARCADE_UPDATE_URL", f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
BACKUP_DIR = ROOT / "backups"
# Files installed by the last zip update, so the next one can remove files a release dropped
MANIFEST = ROOT / ".update-manifest.json"

UP_TO_DATE, UPDATE_AVAILABLE, ERROR = 0, 10, 1

# Never written or removed by an update, whatever the release contains
# (.gitignore's patterns are added to these)
PROTECTED = [
	".git/", ".update-manifest.json", "/backups/", "/other/", "/manager_settings.json",
	"/server/config.json", "/server/nex-keys.txt", "/server/data/", "/server/backups/", "/server/logs/",
	"/server/mitm/.venv/", "*.key", "*.code", "boot9*.bin", "*.boss", "*.enc",
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
		return True  # unknown local version: any release is an update
	return parse_version(latest) > current_parts


def latest_release(timeout: float = 10) -> dict | None:
	"""{"version", "tag", "zip_url", "page_url"} of the newest release, or None if there are none."""
	request = urllib.request.Request(RELEASE_API, headers={
		"Accept": "application/vnd.github+json", "User-Agent": "badge-arcade-updater",
	})
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			release = json.load(response)
	except urllib.error.HTTPError as e:
		if e.code == 404:
			return None  # no releases yet (or the repository isn't public)
		if e.code in (403, 429):
			raise UpdateError("GitHub's rate limit was reached; try again in an hour") from e
		raise UpdateError(f"GitHub answered {e.code} {e.reason}") from e
	except (urllib.error.URLError, OSError, ValueError) as e:
		raise UpdateError(f"couldn't reach GitHub: {getattr(e, 'reason', e)}") from e

	tag = release.get("tag_name", "")
	try:
		parse_version(tag)
	except ValueError as e:
		raise UpdateError(f"the newest release has an unexpected tag {tag!r}") from e
	return {
		"version": tag.removeprefix("v"), "tag": tag,
		"zip_url": release.get("zipball_url"), "page_url": release.get("html_url"),
	}


def check(timeout: float = 10) -> dict | None:
	"""The newest release if it's newer than this install, else None."""
	release = latest_release(timeout)
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


def release_files(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
	"""The files in a GitHub release zip by project path (without the zip's top folder).
	Raises UpdateError for paths that would land outside the project."""
	files = {}
	for info in archive.infolist():
		if info.is_dir():
			continue
		name = info.filename.replace("\\", "/")
		parts = PurePosixPath(name).parts
		if name.startswith("/") or ":" in parts[0] or ".." in parts or len(parts) < 2:
			raise UpdateError(f"the release zip contains an unsafe path: {info.filename}")
		files["/".join(parts[1:])] = info
	if "VERSION" not in files:
		raise UpdateError("the release zip doesn't look like this project (no VERSION file)")
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
		raise UpdateError(f"couldn't download the release: {getattr(e, 'reason', e)}") from e


def install_zip(data: bytes, root: Path = ROOT, backup_dir: Path | None = None) -> Path | None:
	"""Copies a release zip's files over root. Returns the backup of the replaced files (None if none)."""
	backup_dir = backup_dir or root / "backups"
	try:
		archive = zipfile.ZipFile(io.BytesIO(data))
	except zipfile.BadZipFile as e:
		raise UpdateError("the downloaded release isn't a valid zip") from e

	patterns = ignore_patterns(root)
	with archive:
		files = {path: info for path, info in release_files(archive).items() if not is_protected(path, patterns)}
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
	"""Installs a release. Returns a summary."""
	running = running_services()
	if running:
		raise UpdateError(f"stop the {' and '.join(running)} first (they're running on this PC)")

	if is_git_checkout():
		update_git()
		summary = "Updated the git checkout."
	else:
		if not release.get("zip_url"):
			raise UpdateError("the release has no download")
		backup = install_zip(download(release["zip_url"]))
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
	mode.add_argument("--apply", action="store_true", help="install the newest release")
	parser.add_argument("--yes", action="store_true", help="don't ask before installing")
	args = parser.parse_args(argv)

	print(f"Installed version: {current_version()}")
	try:
		release = latest_release()
	except UpdateError as e:
		print(f"Couldn't check for updates: {e}.")
		return ERROR
	if release is None:
		print(f"No releases are published at {RELEASE_API}.")
		return UP_TO_DATE
	if not is_newer(release["version"], current_version()):
		print(f"This is the newest version ({release['version']} is the latest release).")
		return UP_TO_DATE

	print(f"Version {release['version']} is available: {release['page_url']}")
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

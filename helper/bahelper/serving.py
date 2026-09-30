"""Getting weeks to the 3DS, through the server this helper is part of.

The helper lives in the server's helper/ folder. A week is:
1. saved as one of the server's custom weeks: spotpass-letter/out/weeks/<slug>.boss + .json,
   the way the manager saves them, so its Serve a week tab lists it;
2. served with the server's own `serve.py week <slug>`, which moves it to the game date,
   gives it a new SpotPass ID, puts it live as other/data_v131.dat.boss, and keeps it on the
   current date as days pass.
Before that, allbadge_v131.dat.boss is updated with your content if it changed (allbadge.py),
under an ID from the server's counter (spotpass-letter/serve_state.json).

The 3DS only downloads a file whose SpotPass (NsData) ID is new to it, so every file that
goes live gets a new, higher ID.
"""

import datetime
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import allbadge, boss

LIVE_NAME = "data_v131.dat.boss"
FIRST_NS_DATA_ID = 0x600   # above Nintendo's last (0x59e) and the server tools' starting point
SERVER_FIRST_STATE = {"last_ns_data_id": 0x5C7, "free_play_round": 1}   # serve.py's default
EXPORT_NS_DATA_ID = 0x5C0  # like custom_week.py; serve.py gives it a real one


@dataclass
class Settings:
	spotpass_dir: str = ""       # Nintendo's archived files; "" = the server's other/
	boot9: str = ""              # boot9.bin (or a key file); "" = the server's spotpass-letter/boot9.bin
	extra_dir: str = ""          # more SpotPass files to take badges and machines from; "" = an "extra" folder

	@classmethod
	def load(cls, path: Path) -> "Settings":
		"""Settings from older helpers load too: their server_dir, server_state and game_date are dropped."""
		try:
			data = json.loads(Path(path).read_text(encoding="utf-8"))
		except (FileNotFoundError, ValueError):
			data = {}
		return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

	def save(self, path: Path) -> None:
		Path(path).write_text(json.dumps(asdict(self), indent="\t") + "\n", encoding="utf-8")


def find_boot9(install_dir: Path) -> Path | None:
	"""boot9.bin in the server's spotpass-letter folder, where the manager's checklist wants it."""
	folder = Path(install_dir) / "spotpass-letter"
	return next((p for p in (folder / "boot9.bin", folder / "boot9_prot.bin") if p.exists()), None)


def key_path(settings: Settings, install_dir: Path) -> Path | None:
	"""The boot9.bin (or key file) to use: the one chosen on the Setup tab, else the server's."""
	return Path(settings.boot9) if settings.boot9 else find_boot9(install_dir)


def week_slug(name: str) -> str:
	"""The file name serve.py's save_custom_week would use."""
	return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "custom"


def _python() -> str:
	"""A console Python for running the server's scripts (pythonw has no output)."""
	exe = Path(sys.executable)
	if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
		return str(exe.with_name("python.exe"))
	return str(exe)


def _write_json(path: Path, data) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	temp = path.with_suffix(path.suffix + ".tmp")
	temp.write_text(json.dumps(data, indent="\t") + "\n", encoding="utf-8")
	os.replace(temp, path)


class Server:
	def __init__(self, root: Path, settings: Settings, key: bytes, install_dir: Path | None = None):
		self.root = Path(root)  # the helper's folder (its state file)
		self.install_dir = Path(install_dir) if install_dir else self.root.parent  # the server
		self.settings = settings
		self.key = key
		self.state_file = self.root / "helper_state.json"

	# ----- where things are -----

	@property
	def archive_dir(self) -> Path:
		"""Where Nintendo's archived SpotPass files are read from."""
		if self.settings.spotpass_dir:
			path = Path(self.settings.spotpass_dir)
			return path if path.is_absolute() else self.install_dir / path
		return self.spotpass_dir

	@property
	def extra_dirs(self) -> list[Path]:
		"""Folders of more SpotPass files (other weeks, other regions) to read badges from."""
		if self.settings.extra_dir:
			path = Path(self.settings.extra_dir)
			return [path if path.is_absolute() else self.install_dir / path]
		return [p for p in (self.archive_dir / "extra", self.root / "extra") if p.is_dir()]

	@property
	def spotpass_dir(self) -> Path:
		"""Where files go live: the server's other/."""
		return self.install_dir / "other"

	@property
	def live_file(self) -> Path:
		return self.spotpass_dir / LIVE_NAME

	@property
	def weeks_dir(self) -> Path:
		return self.install_dir / "spotpass-letter" / "out" / "weeks"

	def game_date(self) -> datetime.date:
		"""The date the server sends the game: server/config.json's game_date, else today (UTC)."""
		try:
			value = json.loads((self.install_dir / "server" / "config.json").read_text(encoding="utf-8")).get("game_date")
			return datetime.date.fromisoformat(value) if value else datetime.datetime.now(datetime.UTC).date()
		except (OSError, ValueError, TypeError, AttributeError):
			return datetime.datetime.now(datetime.UTC).date()

	# ----- state and IDs -----

	def load_state(self) -> dict:
		try:
			return json.loads(self.state_file.read_text(encoding="utf-8"))
		except (FileNotFoundError, ValueError):
			return {"last_ns_data_id": FIRST_NS_DATA_ID - 1}

	def save_state(self, state: dict) -> None:
		_write_json(self.state_file, state)

	def server_state_file(self) -> Path:
		return self.install_dir / "spotpass-letter" / "serve_state.json"

	def server_state(self) -> dict | None:
		try:
			return json.loads(self.server_state_file().read_text(encoding="utf-8"))
		except FileNotFoundError:
			return dict(SERVER_FIRST_STATE)
		except (OSError, ValueError):
			return None

	def server_last_id(self) -> int | None:
		state = self.server_state()
		return int(state.get("last_ns_data_id", 0)) if state else None

	def live_id(self) -> int | None:
		try:
			return boss.ns_data_id(self.live_file.read_bytes(), self.key)
		except (OSError, ValueError):
			return None

	def next_id(self) -> int:
		candidates = [self.load_state()["last_ns_data_id"], self.live_id() or 0, self.server_last_id() or 0]
		return max(candidates) + 1

	def allocate_id(self) -> int:
		"""A new SpotPass ID, recorded in both counters."""
		ns_id = self.next_id()
		state = self.load_state()
		state["last_ns_data_id"] = ns_id
		self.save_state(state)
		server_state = self.server_state()
		if server_state is not None:
			server_state["last_ns_data_id"] = max(int(server_state.get("last_ns_data_id", 0)), ns_id)
			server_state.setdefault("free_play_round", 1)
			_write_json(self.server_state_file(), server_state)
		return ns_id

	# ----- weeks -----

	def export_week(self, name: str, machines: list[str], per_series: int, content: bytes) -> str:
		"""Saves a week as one of the server's custom weeks; returns its slug."""
		folder = self.weeks_dir
		folder.mkdir(parents=True, exist_ok=True)
		slug = week_slug(name)
		meta_path = folder / f"{slug}.json"
		if meta_path.exists():
			try:
				made_by_us = json.loads(meta_path.read_text(encoding="utf-8")).get("made_with") == "Badge Arcade Helper"
			except ValueError:
				made_by_us = False
			if not made_by_us:
				slug = f"{slug}-helper"  # don't overwrite a week made on the Build a week tab
				meta_path = folder / f"{slug}.json"
		boss_path = folder / f"{slug}.boss"
		temp = boss_path.with_suffix(".boss.tmp")
		temp.write_bytes(content)
		os.replace(temp, boss_path)
		_write_json(meta_path, {"name": name, "setups": machines, "per_series": per_series,
			"created": datetime.datetime.now().isoformat(timespec="seconds"), "made_with": "Badge Arcade Helper"})
		return slug

	def run_serve_py(self, *args: str, timeout: int = 600) -> str:
		result = subprocess.run([_python(), "serve.py", *args], cwd=self.install_dir / "spotpass-letter", capture_output=True,
			text=True, encoding="utf-8", errors="replace", timeout=timeout,
			creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
		output = (result.stdout + result.stderr).strip()
		if result.returncode != 0:
			raise ValueError(f"The server's serve.py said:\n{output or f'exit code {result.returncode}'}")
		return output

	def serve_week(self, slug: str) -> str:
		return self.run_serve_py("week", slug)

	def record_served(self, label: str, ns_data_id: int | None) -> None:
		state = self.load_state()
		state["live"] = {"label": label, "ns_data_id": ns_data_id,
			"served": datetime.datetime.now().isoformat(timespec="seconds")}
		self.save_state(state)

	# ----- allbadge -----

	def update_allbadge(self, builder, force: bool = False, progress=lambda message: None) -> str:
		"""Puts your content into allbadge if it changed since the last time."""
		progress("Collecting your badges, sets and machines for allbadge...")
		added = allbadge.custom_files(builder)
		sign = allbadge.signature(added)
		state = self.load_state()
		live = self.spotpass_dir / allbadge.LIVE_NAME
		if not force and state.get("allbadge_signature") == sign and live.exists() and self.allbadge_is_ours():
			return "allbadge already has all your content."
		progress("Adding your content to allbadge (43 MB)...")
		pristine = allbadge.pristine(self.spotpass_dir, self.key)
		ns_id = self.allocate_id()
		content = allbadge.build(pristine.read_bytes(), self.key, added, ns_id)
		allbadge.put_live(self.spotpass_dir, content)
		state = self.load_state()
		state["allbadge_signature"] = sign
		state["allbadge_id"] = ns_id
		self.save_state(state)
		return (f"allbadge now has your {sum(1 for n in added if '/Pr/' in n)} badges and "
			f"{sum(1 for n in added if n.startswith('pc/ci/'))} machines (SpotPass ID {ns_id:#x}; the 3DS downloads "
			"it again, about 43 MB).")

	def allbadge_is_ours(self) -> bool:
		live = self.spotpass_dir / allbadge.LIVE_NAME
		try:
			return boss.ns_data_id(live.read_bytes(), self.key) == self.load_state().get("allbadge_id")
		except (OSError, ValueError):
			return False

	def restore_allbadge(self) -> str:
		"""Nintendo's allbadge again, under a new ID so the console takes it."""
		kept = self.spotpass_dir / allbadge.PRISTINE_NAME
		if not kept.exists():
			return "allbadge is still Nintendo's: nothing to put back."
		ns_id = self.allocate_id()
		allbadge.put_live(self.spotpass_dir, allbadge.build(kept.read_bytes(), self.key, {}, ns_id))
		state = self.load_state()
		state.pop("allbadge_signature", None)
		state["allbadge_id"] = ns_id
		self.save_state(state)
		return f"Nintendo's allbadge is live again (SpotPass ID {ns_id:#x})."

	def status(self) -> str:
		state = self.load_state()
		live = state.get("live")
		live_id = self.live_id()
		if not self.live_file.exists():
			lines = ["Nothing is live yet."]
		elif live and live.get("ns_data_id") == live_id:
			lines = [f"Live: {live['label']} (SpotPass ID {live_id:#x}), served {live['served']}."]
		else:
			lines = [f"Live: a week served from the Serve a week tab (SpotPass ID {live_id:#x})." if live_id is not None
				else "Live: a file the helper can't read."]
		lines.append(f"allbadge: {'with your content' if self.allbadge_is_ours() else 'Nintendo' + chr(39) + 's'}. "
			f"Next SpotPass ID: {self.next_id():#x}.")
		return "\n".join(lines)

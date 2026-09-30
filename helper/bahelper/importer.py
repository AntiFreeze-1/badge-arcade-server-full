"""Bringing your work over from the standalone Badge Arcade Helper.

Before it was part of the manager, the helper was a folder of its own next to the server
(badge-arcade-helper). import_helper copies its workspace (your sets, badges, machines, weeks
and pictures), its SpotPass counter and its settings into helper/, with the settings' paths
made to work from here. The old folder isn't changed.
"""

import json
import shutil
from pathlib import Path

from .archive import ALLBADGE_NAME, BASE_WEEK_NAME, PRISTINE_ALLBADGE
from .serving import Settings, find_boot9

SETTINGS_NAME = "helper_settings.json"
STATE_NAME = "helper_state.json"


def is_old_helper(folder: Path) -> bool:
	folder = Path(folder)
	return (folder / "bahelper").is_dir() and (
		(folder / "workspace" / "workspace.json").exists() or (folder / SETTINGS_NAME).exists())


def find_old_helpers(install_dir: Path) -> list[Path]:
	"""Standalone helper folders next to the server, where the helper's README said to put them."""
	install_dir = Path(install_dir).resolve()
	return [p for p in sorted(install_dir.parent.glob("badge-arcade-helper*")) if p != install_dir and is_old_helper(p)]


def has_content(workspace: Path) -> bool:
	"""Whether a workspace holds anything of yours (its cache and empty folders don't count)."""
	workspace = Path(workspace)
	if not workspace.is_dir():
		return False
	return any(p.is_file() and p.relative_to(workspace).parts[0] != "cache" for p in workspace.rglob("*"))


def _archive_files(folder: Path) -> set[str]:
	return {p.name for pattern in ("data_v131*", "allbadge_v13*") for p in folder.glob(pattern)}


def _has_archive(folder: Path) -> bool:
	return (folder / BASE_WEEK_NAME).exists() and ((folder / ALLBADGE_NAME).exists() or (folder / PRISTINE_ALLBADGE).exists())


def _resolve(value: str, base: Path) -> Path | None:
	if not value:
		return None
	path = Path(value)
	return path if path.is_absolute() else base / path


def imported_settings(old: Path, install_dir: Path) -> Settings:
	"""The old helper's settings with paths that work from here: relative ones are resolved
	against the old folder, and ones the server's own files already cover are left empty."""
	old, install_dir = Path(old), Path(install_dir)
	try:
		data = json.loads((old / SETTINGS_NAME).read_text(encoding="utf-8"))
	except (OSError, ValueError):
		data = {}
	settings = Settings()

	boot9 = _resolve(data.get("boot9", ""), old)
	server_boot9 = find_boot9(install_dir)
	if boot9 and boot9.exists() and not (server_boot9 and boot9.resolve() == server_boot9.resolve()):
		settings.boot9 = str(boot9.resolve())

	# The old helper read Nintendo's files from its setting, else the server's other/ (or its own other/)
	archive = _resolve(data.get("spotpass_dir", ""), old) or old / "other"
	install_other = install_dir / "other"
	covered = _has_archive(install_other) and _archive_files(archive) <= _archive_files(install_other)
	if not covered and archive.is_dir() and _has_archive(archive):
		settings.spotpass_dir = str(archive.resolve())

	extra = _resolve(data.get("extra_dir", ""), old)
	if extra is None and (old / "extra").is_dir() and any((old / "extra").iterdir()):
		extra = old / "extra"
	if extra and extra.is_dir():
		settings.extra_dir = str(extra.resolve())
	return settings


def import_helper(old: Path, helper_dir: Path, install_dir: Path | None = None) -> str:
	"""Copies an old helper's workspace, counter and settings into helper_dir; returns a summary.
	Refuses when helper_dir's workspace already has your work in it. Settings already chosen
	here are kept."""
	old, helper_dir = Path(old).resolve(), Path(helper_dir)
	install_dir = Path(install_dir) if install_dir else helper_dir.parent
	if not is_old_helper(old):
		raise ValueError(f"{old} doesn't look like a Badge Arcade Helper folder (it needs bahelper and a workspace).")
	if old == helper_dir.resolve():
		raise ValueError("That's this helper's own folder.")
	if has_content(helper_dir / "workspace"):
		raise ValueError(f"{helper_dir / 'workspace'} already has badges or machines in it, so nothing was copied "
			"(importing would mix the two).")

	if (old / "workspace").is_dir():
		shutil.copytree(old / "workspace", helper_dir / "workspace", dirs_exist_ok=True)

	try:
		state = json.loads((old / STATE_NAME).read_text(encoding="utf-8"))
	except (OSError, ValueError):
		state = None
	if state is not None:
		try:
			current = json.loads((helper_dir / STATE_NAME).read_text(encoding="utf-8"))
		except (OSError, ValueError):
			current = {}
		state["last_ns_data_id"] = max(int(state.get("last_ns_data_id", 0)), int(current.get("last_ns_data_id", 0)))
		(helper_dir / STATE_NAME).write_text(json.dumps(state, indent="\t") + "\n", encoding="utf-8")

	settings = Settings.load(helper_dir / SETTINGS_NAME)
	for name, value in vars(imported_settings(old, install_dir)).items():
		if value and not getattr(settings, name):
			setattr(settings, name, value)
	settings.save(helper_dir / SETTINGS_NAME)

	workspace = helper_dir / "workspace"
	counts = [(len(list((workspace / sub).glob("*.json"))), label) for sub, label in
		(("sets", "sets"), ("badges", "badges"), ("machines", "machines"), ("weeks", "weeks"))]
	return (f"Copied {', '.join(f'{n} {label}' for n, label in counts)} and the settings from {old}. "
		"That folder wasn't changed: once everything's here, you can delete it.")

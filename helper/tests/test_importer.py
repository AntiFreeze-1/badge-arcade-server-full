"""Bringing a standalone helper's work into the merged one."""

import json

import pytest

from bahelper import importer
from bahelper.archive import ALLBADGE_NAME, BASE_WEEK_NAME
from bahelper.serving import Settings
from bahelper.workspace import Workspace


def old_helper(parent, settings: dict, archive: list[str] = ()):
	"""A standalone helper folder the way users had it: code, workspace, settings, counter, own other/."""
	old = parent / "badge-arcade-helper"
	(old / "bahelper").mkdir(parents=True)
	for sub, name in (("sets", "Portal2.json"), ("badges", "Pr_Test.json"), ("badges", "Pr_Test.png"),
			("machines", "CuPortal2_000.json"), ("weeks", "Full_Fire.json"), ("cache", "abc.bin")):
		(old / "workspace" / sub).mkdir(parents=True, exist_ok=True)
		(old / "workspace" / sub / name).write_text("{}")
	(old / "workspace" / "workspace.json").write_text(json.dumps({"next": {"badge": 90000062}}))
	(old / "helper_settings.json").write_text(json.dumps(settings))
	(old / "helper_state.json").write_text(json.dumps({"last_ns_data_id": 1551, "allbadge_id": 1551}))
	(old / "other").mkdir()
	for name in archive:
		(old / "other" / name).write_bytes(b"x")
	return old


def install(parent, archive: list[str] = ()):
	root = parent / "badge-arcade-server-main"
	(root / "spotpass-letter").mkdir(parents=True)
	(root / "other").mkdir()
	for name in archive:
		(root / "other" / name).write_bytes(b"x")
	Workspace(root / "helper" / "workspace")  # the merged helper makes its empty folders at start
	return root


def snapshot(folder):
	return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_finds_and_copies_an_old_helper(tmp_path):
	archive = [BASE_WEEK_NAME, ALLBADGE_NAME, "data_v131-2022-02-10-09-40-NA.enc"]
	(tmp_path / "boot9.bin").write_bytes(b"k")
	old = old_helper(tmp_path, {"boot9": str(tmp_path / "boot9.bin"), "spotpass_dir": "other", "server_dir": "",
		"server_state": "", "game_date": "2026-01-05"}, archive)
	root = install(tmp_path, archive)
	before = snapshot(old)
	assert not importer.has_content(root / "helper" / "workspace")
	assert importer.find_old_helpers(root) == [old.resolve()]

	summary = importer.import_helper(old, root / "helper")
	assert "1 sets, 1 badges, 1 machines, 1 weeks" in summary
	assert snapshot(old) == before  # the old folder isn't changed
	for name in ("sets/Portal2.json", "badges/Pr_Test.png", "machines/CuPortal2_000.json", "weeks/Full_Fire.json",
			"cache/abc.bin", "workspace.json"):
		assert (root / "helper" / "workspace" / name).exists(), name
	assert json.loads((root / "helper" / "helper_state.json").read_text())["last_ns_data_id"] == 1551
	settings = json.loads((root / "helper" / "helper_settings.json").read_text())
	# boot9 elsewhere is kept; the old other/ has nothing the server's lacks, so the server's is used
	assert settings == {"spotpass_dir": "", "boot9": str((tmp_path / "boot9.bin").resolve()), "extra_dir": ""}


def test_keeps_an_archive_the_server_doesnt_have(tmp_path):
	old = old_helper(tmp_path, {"spotpass_dir": "other", "boot9": str(tmp_path / "gone" / "boot9.bin")},
		[BASE_WEEK_NAME, ALLBADGE_NAME, "data_v131-2022-07-07-09-40-NA.enc"])
	root = install(tmp_path, [BASE_WEEK_NAME, ALLBADGE_NAME])
	(root / "spotpass-letter" / "boot9.bin").write_bytes(b"k")
	settings = importer.imported_settings(old, root)
	assert settings.spotpass_dir == str((old / "other").resolve())
	assert settings.boot9 == ""  # it no longer exists: the server's boot9.bin is used


def test_the_servers_own_boot9_isnt_copied_into_the_settings(tmp_path):
	root = install(tmp_path)
	(root / "spotpass-letter" / "boot9.bin").write_bytes(b"k")
	old = old_helper(tmp_path, {"boot9": str(root / "spotpass-letter" / "boot9.bin")})
	assert importer.imported_settings(old, root).boot9 == ""


def test_refuses_to_mix_workspaces(tmp_path):
	old = old_helper(tmp_path, {})
	root = install(tmp_path)
	(root / "helper" / "workspace" / "sets" / "Mine.json").write_text("{}")
	with pytest.raises(ValueError, match="already has"):
		importer.import_helper(old, root / "helper")
	assert not (root / "helper" / "workspace" / "sets" / "Portal2.json").exists()


def test_keeps_settings_chosen_here_and_the_higher_counter(tmp_path):
	extra = tmp_path / "extra-files"
	extra.mkdir()
	old = old_helper(tmp_path, {"extra_dir": str(extra)})
	root = install(tmp_path)
	Settings(boot9="C:/keys/mine.bin").save(root / "helper" / "helper_settings.json")
	(root / "helper" / "helper_state.json").write_text(json.dumps({"last_ns_data_id": 1600}))
	importer.import_helper(old, root / "helper")
	settings = Settings.load(root / "helper" / "helper_settings.json")
	assert settings.boot9 == "C:/keys/mine.bin" and settings.extra_dir == str(extra.resolve())
	state = json.loads((root / "helper" / "helper_state.json").read_text())
	assert state == {"last_ns_data_id": 1600, "allbadge_id": 1551}


def test_only_helper_folders(tmp_path):
	(tmp_path / "badge-arcade-helper-copy").mkdir()
	root = install(tmp_path)
	assert importer.find_old_helpers(root) == []
	with pytest.raises(ValueError, match="doesn't look like"):
		importer.import_helper(tmp_path / "badge-arcade-helper-copy", root / "helper")

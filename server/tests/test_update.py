"""update.py: version checks and installing a release zip, with no network."""

from pathlib import Path
import hashlib
import io
import json
import sys
import urllib.error
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import update  # noqa: E402


def test_versions():
	assert update.parse_version("v1.10.0") == (1, 10)
	assert update.is_newer("1.10.0", "1.9.3")
	assert update.is_newer("v2", "1.99")
	assert not update.is_newer("1.2.0", "1.2")
	assert not update.is_newer("1.0.0", "1.0.1")
	assert update.is_newer("1.0.0", "unknown")
	with pytest.raises(ValueError):
		update.parse_version("1.0-beta")


class FakeResponse(io.BytesIO):
	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


def test_this_version_is_valid():
	assert update.VERSION_FILE.name == "version.txt"
	update.parse_version(update.current_version())


def test_version_file_for_the_first_updater():
	"""Installs from 1.0.0 update from GitHub releases and need a VERSION file in the release."""
	assert (update.ROOT / "VERSION").read_text(encoding="utf-8").strip() == update.current_version()


@pytest.mark.parametrize("name, digest", [
	("Setup.bat", "72cb8b6b7acfb2eb212249eba828b924b3f91407345a38c88fa060744e013433"),
	("Badge Arcade Manager.bat", "a3e55a5cb850609c22cb6e9f88a2864a0400585ee7f211bed024831cf1d9dbe3"),
])
def test_batch_files_never_change(name: str, digest: str):
	"""Setup.bat runs update.py, and Windows goes on reading a running .bat from the same byte
	offset: if an update changed it, the rest would run from the middle of a line. Put anything
	new in install.py or manager.py instead."""
	content = (update.ROOT / name).read_bytes().replace(b"\r\n", b"\n")
	assert hashlib.sha256(content).hexdigest() == digest


def test_check(monkeypatch):
	requested = []

	def github(request, **kwargs):
		requested.append(request.full_url)
		return FakeResponse(b"9.0.0\n")
	monkeypatch.setattr(update.urllib.request, "urlopen", github)
	monkeypatch.setattr(update, "current_version", lambda: "1.0.0")
	latest = update.check()
	assert latest["version"] == "9.0.0" and latest["zip_url"] == update.ZIP_URL
	assert requested == [update.VERSION_URL] and update.VERSION_URL.endswith("/main/version.txt")
	monkeypatch.setattr(update, "current_version", lambda: "9.0.0")
	assert update.check() is None
	monkeypatch.setattr(update, "current_version", lambda: "10.0")
	assert update.check() is None  # never "updates" to an older version

	monkeypatch.setattr(update.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"<html>oops</html>"))
	with pytest.raises(update.UpdateError, match="not a version number"):
		update.check()

	def missing(*args, **kwargs):
		raise urllib.error.HTTPError(update.VERSION_URL, 404, "Not Found", {}, None)
	monkeypatch.setattr(update.urllib.request, "urlopen", missing)
	assert update.check() is None

	def offline(*args, **kwargs):
		raise urllib.error.URLError("no network")
	monkeypatch.setattr(update.urllib.request, "urlopen", offline)
	with pytest.raises(update.UpdateError):
		update.check()


def test_is_protected():
	patterns = update.PROTECTED + ["__pycache__/", "/spotpass-letter/out/"]
	for path in ("server/config.json", "server/data/badge_arcade.db", "other/x.enc", "spotpass-letter/boot9.bin",
			"spotpass-letter/badge_arcade_hmac.key", "server/mitm/.venv/bin/python", "manager_settings.json",
			".git/config", "server/badge_arcade/__pycache__/x.pyc", "spotpass-letter/out/week.boss",
			"helper/workspace/badges/Pr_Cu_Star.png", "helper/workspace/workspace.json", "helper/helper_settings.json",
			"helper/helper_state.json", "helper/extra/data.enc", "helper/other/allbadge.dat"):
		assert update.is_protected(path, patterns), path
	for path in ("manager.py", "server/config.example.json", "server/badge_arcade/server.py",
			"spotpass-letter/out.py", "other.py", "version.txt", "VERSION", "helper/bahelper/workspace.py",
			"helper/gui/app.py", "helper/requirements.txt"):
		assert not update.is_protected(path, patterns), path


def release_zip(files: dict[str, bytes], top: str = "AntiFreeze-1-badge-arcade-server-full-abc123") -> bytes:
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		for path, content in files.items():
			archive.writestr(f"{top}/{path}" if top else path, content)
	return buffer.getvalue()


def test_install_zip_keeps_user_files(tmp_path: Path):
	(tmp_path / ".gitignore").write_text("/server/config.json\n/server/data/\n")
	(tmp_path / "server" / "data").mkdir(parents=True)
	(tmp_path / "server" / "config.json").write_text("mine")
	(tmp_path / "server" / "data" / "badge_arcade.db").write_text("saves")
	(tmp_path / "manager.py").write_text("old")
	(tmp_path / "dropped.py").write_text("old")
	(tmp_path / ".update-manifest.json").write_text(json.dumps(["manager.py", "dropped.py", "server/config.json"]))

	backup = update.install_zip(release_zip({
		"version.txt": b"2.0.0\n", "manager.py": b"new", "server/new_module.py": b"new",
		"server/config.json": b"from the release", "server/data/badge_arcade.db": b"from the release",
	}), root=tmp_path)

	assert (tmp_path / "version.txt").read_text() == "2.0.0\n"
	assert (tmp_path / "manager.py").read_text() == "new"
	assert (tmp_path / "server" / "new_module.py").read_text() == "new"
	assert (tmp_path / "server" / "config.json").read_text() == "mine"
	assert (tmp_path / "server" / "data" / "badge_arcade.db").read_text() == "saves"
	assert not (tmp_path / "dropped.py").exists()  # removed from the release

	with zipfile.ZipFile(backup) as archive:
		assert sorted(archive.namelist()) == ["dropped.py", "manager.py"]
		assert archive.read("manager.py") == b"old"
	assert "server/config.json" not in json.loads((tmp_path / ".update-manifest.json").read_text())


@pytest.mark.parametrize("bad", ["top/../../outside.py", "/absolute.py", "C:/windows.py"])
def test_install_zip_rejects_unsafe_paths(tmp_path: Path, bad: str):
	buffer = io.BytesIO(release_zip({"version.txt": b"2.0.0"}))
	with zipfile.ZipFile(buffer, "a") as archive:
		archive.writestr(zipfile.ZipInfo(bad), b"x")  # ZipInfo keeps the name as given
	with pytest.raises(update.UpdateError, match="unsafe"):
		update.install_zip(buffer.getvalue(), root=tmp_path / "root")
	assert not (tmp_path / "outside.py").exists()


def test_install_zip_rejects_other_zips(tmp_path: Path):
	with pytest.raises(update.UpdateError):
		update.install_zip(release_zip({"something.txt": b"x"}), root=tmp_path)
	with pytest.raises(update.UpdateError):
		update.install_zip(b"not a zip", root=tmp_path)



def test_install_zip_refuses_a_download_that_isnt_newer(tmp_path: Path):
	(tmp_path / "manager.py").write_text("old")
	# GitHub's version.txt can be ahead of its zip for a few minutes
	with pytest.raises(update.UpdateError, match="not newer"):
		update.install_zip(release_zip({"version.txt": b"1.0.0", "manager.py": b"new"}), root=tmp_path, newer_than="1.0.0")
	assert (tmp_path / "manager.py").read_text() == "old"
	update.install_zip(release_zip({"version.txt": b"1.0.1", "manager.py": b"new"}), root=tmp_path, newer_than="1.0.0")
	assert (tmp_path / "manager.py").read_text() == "new"


def old_install(root: Path) -> dict[str, str]:
	"""A zip install of version 1.0.0 with two modules, one of which the next version drops."""
	files = {"version.txt": "1.0.0\n", "manager.py": "old manager", "server/a.py": "old a", "server/dropped.py": "old"}
	for path, content in files.items():
		(root / path).parent.mkdir(parents=True, exist_ok=True)
		(root / path).write_text(content)
	(root / ".update-manifest.json").write_text(json.dumps(sorted(files)))
	return files


NEW_VERSION = {"version.txt": b"2.0.0\n", "manager.py": b"new manager", "server/a.py": b"new a", "server/b.py": b"new b"}


def project_files(root: Path) -> dict[str, str]:
	return {path.relative_to(root).as_posix(): path.read_text() for path in root.rglob("*")
		if path.is_file() and "backups" not in path.parts}


@pytest.mark.parametrize("stuck, step", [
	("server/a.py", "aside"), ("server/a.py", "in"),  # a changed file
	("server/b.py", "in"),  # a new file
	("server/dropped.py", "aside"),  # a file the new version drops
	("version.txt", "aside"), ("version.txt", "in"),  # the last one
])
def test_install_zip_puts_the_old_version_back(tmp_path: Path, monkeypatch, stuck: str, step: str):
	"""Windows refuses to replace a file another program has open: the update then puts every
	file back as it was, rather than leaving half of the old version and half of the new one."""
	before = old_install(tmp_path)
	before[".update-manifest.json"] = (tmp_path / ".update-manifest.json").read_text()
	replace = Path.replace

	def locked(self: Path, target):
		# step "aside": moving the old file out of the way fails; "in": moving the new one in does
		moving = Path(target) if step == "in" else self
		if moving == tmp_path / stuck and (step == "aside" or self.name.endswith(update.STAGED)):
			raise PermissionError(13, "The process cannot access the file because it is being used by another process")
		return replace(self, target)
	monkeypatch.setattr(Path, "replace", locked)

	with pytest.raises(update.UpdateError, match="previous version was put back"):
		update.install_zip(release_zip(NEW_VERSION), root=tmp_path)
	assert project_files(tmp_path) == before  # no new files, no leftovers next to them
	assert len(list((tmp_path / "backups").iterdir())) == 1  # and the backup is still there


def test_install_zip_leaves_a_broken_download_alone(tmp_path: Path):
	before = old_install(tmp_path)
	data = release_zip({**NEW_VERSION, "server/b.py": b"CORRUPTED?"})
	data = data.replace(b"CORRUPTED?", b"corrupted!")  # the stored checksum no longer matches
	with pytest.raises(update.UpdateError, match="nothing was changed"):
		update.install_zip(data, root=tmp_path)
	assert project_files(tmp_path) == {**before, ".update-manifest.json": json.dumps(sorted(before))}


def test_install_zip_changes_version_txt_last(tmp_path: Path, monkeypatch):
	"""If an update stops partway (the PC loses power), the old version number stays, so the
	manager offers the update again and finishes it."""
	old_install(tmp_path)
	moved_in = []
	replace = Path.replace

	def record(self: Path, target):
		if self.name.endswith(update.STAGED):
			moved_in.append(Path(target).relative_to(tmp_path).as_posix())
		return replace(self, target)
	monkeypatch.setattr(Path, "replace", record)
	update.install_zip(release_zip({"VERSION": b"2.0.0", "version.txt": b"2.0.0\n", "manager.py": b"new", "zz.py": b"new"}),
		root=tmp_path)
	assert moved_in[-1] == "version.txt" and sorted(moved_in) == ["VERSION", "manager.py", "version.txt", "zz.py"]
	assert project_files(tmp_path)["version.txt"] == "2.0.0\n"
	assert not [path for path in project_files(tmp_path) if path.endswith((update.STAGED, update.OLD))]


def test_apply_refuses_while_running(monkeypatch):
	monkeypatch.setattr(update, "running_services", lambda: ["server"])
	with pytest.raises(update.UpdateError, match="stop the server"):
		update.apply({"version": "2.0.0", "zip_url": "https://example.invalid/zip"})


def test_apply_git_checkout(monkeypatch):
	calls = []
	monkeypatch.setattr(update, "running_services", lambda: [])
	monkeypatch.setattr(update, "is_git_checkout", lambda: True)
	monkeypatch.setattr(update, "update_git", lambda: calls.append("git"))
	monkeypatch.setattr(update, "install_zip", lambda *a: calls.append("zip"))
	monkeypatch.setattr(update, "install_packages", lambda: calls.append("packages"))
	update.apply({"version": "2.0.0", "zip_url": "https://example.invalid/zip"})
	assert calls == ["git", "packages"]

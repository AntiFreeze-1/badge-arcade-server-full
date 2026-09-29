"""update.py: version checks and installing a release zip, with no network."""

from pathlib import Path
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


def test_check(monkeypatch):
	release = {"tag_name": "v9.0.0", "zipball_url": "https://example.invalid/zip", "html_url": "https://example.invalid/"}
	monkeypatch.setattr(update.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps(release).encode()))
	monkeypatch.setattr(update, "current_version", lambda: "1.0.0")
	assert update.check()["version"] == "9.0.0"
	monkeypatch.setattr(update, "current_version", lambda: "9.0.0")
	assert update.check() is None

	def no_releases(*args, **kwargs):
		raise urllib.error.HTTPError(update.RELEASE_API, 404, "Not Found", {}, None)
	monkeypatch.setattr(update.urllib.request, "urlopen", no_releases)
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
			".git/config", "server/badge_arcade/__pycache__/x.pyc", "spotpass-letter/out/week.boss"):
		assert update.is_protected(path, patterns), path
	for path in ("manager.py", "server/config.example.json", "server/badge_arcade/server.py",
			"spotpass-letter/out.py", "other.py", "VERSION"):
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
		"VERSION": b"2.0.0\n", "manager.py": b"new", "server/new_module.py": b"new",
		"server/config.json": b"from the release", "server/data/badge_arcade.db": b"from the release",
	}), root=tmp_path)

	assert (tmp_path / "VERSION").read_text() == "2.0.0\n"
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
	buffer = io.BytesIO(release_zip({"VERSION": b"2.0.0"}))
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

"""Shared fixtures. Tests that need the SpotPass key and Nintendo's archived files are
skipped when they aren't there (they're never part of the repository)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent   # helper/
INSTALL = ROOT.parent                            # the server around it
sys.path.insert(0, str(ROOT))

from bahelper import boss  # noqa: E402
from bahelper.serving import Settings, key_path  # noqa: E402

OTHER = INSTALL / "other"


def nintendo_allbadge() -> Path:
	"""Nintendo's allbadge: the copy kept aside once yours went live, else the live one."""
	kept = OTHER / "allbadge_v131-nintendo.enc"
	return kept if kept.exists() else OTHER / "allbadge_v131.dat.boss"


def _key() -> bytes | None:
	path = key_path(Settings.load(ROOT / "helper_settings.json"), INSTALL)
	try:
		return boss.load_key(path) if path else None
	except (OSError, ValueError):
		return None


@pytest.fixture(scope="session")
def key() -> bytes:
	key = _key()
	if key is None or not (OTHER / "data_v131-2022-12-29-09-40-NA.enc").exists():
		pytest.skip("needs boot9.bin and the archived SpotPass files in other/")
	return key


@pytest.fixture(scope="session")
def archive(key, tmp_path_factory):
	from bahelper.archive import Archive
	return Archive(OTHER, key, tmp_path_factory.mktemp("cache"))


@pytest.fixture(scope="session")
def base_week(key):
	from bahelper import sarc
	top = sarc.sarc_read(boss.open_container((OTHER / "data_v131-2022-12-29-09-40-NA.enc").read_bytes(), key)[1])
	return sarc.sarc_read(top[next(n for n in top if n.startswith("sharc/"))])


@pytest.fixture(scope="session")
def base_top(key):
	"""The base week's top-level files (schedule, messages, hall pictures, posts, ...)."""
	from bahelper import sarc
	return sarc.sarc_read(boss.open_container((OTHER / "data_v131-2022-12-29-09-40-NA.enc").read_bytes(), key)[1])

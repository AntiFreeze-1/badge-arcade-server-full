"""Free plays for each region's Badge Arcade: the European game stops at "we're still doing some
setup work" with the USA playinfo and starts with its own (issue 12), so free plays for it are
made from a European playinfo in other/. Made-up playinfo containers with a throwaway key."""

from pathlib import Path
import datetime
import hmac
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import free_plays  # noqa: E402
import make_letter  # noqa: E402
import serve  # noqa: E402
import test_rotation  # noqa: E402

KEY = bytes(range(16))
HMAC_KEY = bytes(range(32, 48))
USA, EUR = make_letter.TITLE_IDS["USA"], make_letter.TITLE_IDS["EUR"]
DAY = datetime.date(2026, 10, 1)


def payload(campaigns: int = 7, extra: bytes = b"", count: int | None = None) -> bytes:
	"""A playinfo payload laid out like Nintendo's USA one (count and campaigns at 0x2C), signed."""
	body = bytearray(free_plays.DAILY_COUNT_OFFSET) + struct.pack("<I", campaigns if count is None else count)
	for i in range(campaigns):
		body += free_plays.DAILY_ENTRY.pack(1000 + i, 1, 1_700_000_000 + i * 86400, 1_700_086_399 + i * 86400, 2)
	body += extra
	return bytes(body) + hmac.digest(HMAC_KEY, bytes(body), "sha256")


def playinfo(path: Path, *entries: tuple[bytes, int]) -> Path:
	"""A playinfo container with these (payload, program ID) entries."""
	plain = make_letter.build_container_plain_multi([(content, program_id, 0x10001, 0x500 + i, 1)
		for i, (content, program_id) in enumerate(entries)], mark_arrived_always=True)
	path.write_bytes(make_letter.encrypt_container(KEY, plain, 1, iv12=bytes(12)))
	return path


@pytest.fixture
def other(tmp_path: Path, monkeypatch) -> Path:
	other = tmp_path / "other"
	other.mkdir()
	(tmp_path / "badge_arcade_hmac.key").write_text(HMAC_KEY.hex())
	monkeypatch.setattr(serve, "HERE", tmp_path)
	monkeypatch.setattr(serve, "OTHER", other)
	monkeypatch.setattr(serve, "STATE", tmp_path / "serve_state.json")
	monkeypatch.setattr(serve, "LIVE_WEEK", other / "data_v131.dat.boss")
	monkeypatch.setattr(serve, "LIVE_PLAYINFO", other / "playinfo_v131.dat.boss")
	monkeypatch.setattr(serve, "PLAYINFO_BASE", other / "playinfo_v131-2022-12-29-09-40-NA.enc")
	monkeypatch.setattr(serve, "game_date", lambda: None)
	playinfo(serve.PLAYINFO_BASE, (payload(), USA))
	return other


def live() -> list:
	return make_letter.parse_container(serve.LIVE_PLAYINFO.read_bytes(), KEY)[1]


def test_usa_free_plays_are_made_from_the_usa_playinfo(other):
	assert "free plays are ready" in serve.give_free_plays(5, KEY, DAY, "USA")
	(game,) = live()
	assert game.program_id == USA and {c[4] for c in free_plays.daily_campaigns(game.content)} == {5}
	assert serve.container_regions(serve.LIVE_PLAYINFO, KEY) == ("USA",)


def test_eur_free_plays_are_made_from_the_eur_playinfo(other):
	# Nintendo's European one is bigger, and can carry a letter for the Notifications applet too
	eur = payload(extra=b"european play settings" * 100)
	playinfo(other / "playinfo_v131-2022-11-18-EU.boss", (eur, EUR), (b"letter", make_letter.NEWS_PROGRAM_ID))
	message = serve.give_free_plays(5, KEY, DAY, "EUR")
	assert "free plays are ready" in message and "from the EUR playinfo" in message
	game, letter = live()
	assert game.program_id == EUR and len(game.content) == len(eur) and game.content[-32 - 2200:-32] == eur[-32 - 2200:-32]
	assert {c[4] for c in free_plays.daily_campaigns(game.content)} == {5}
	assert hmac.digest(HMAC_KEY, game.content[:-32], "sha256") == game.content[-32:]  # signed again
	assert letter.content == b"letter"  # kept
	assert game.ns_data_id == serve.load_state()["last_ns_data_id"]  # a new, higher ID from the counter
	assert serve.container_regions(serve.LIVE_PLAYINFO, KEY) == ("EUR",)


def test_the_region_defaults_to_the_live_weeks(other):
	playinfo(other / "playinfo_v131-2022-11-18-EU.boss", (payload(), EUR))
	assert serve.free_plays_region(KEY) == "USA"  # no live week
	# (test_rotation's made-up weeks use the same throwaway key)
	test_rotation.week_file(serve.LIVE_WEEK, datetime.date(2022, 11, 18), ["E_1"], program_id=EUR, text_for="EUR")
	assert serve.free_plays_region(KEY) == "EUR"
	serve.give_free_plays(3, KEY, DAY)
	assert live()[0].program_id == EUR


def test_a_playinfo_whose_free_plays_cant_be_changed_still_goes_live(other):
	"""A layout this doesn't know (or another HMAC key): the European game still needs its own
	playinfo to start, so it goes live as it is, under a new SpotPass ID."""
	odd = payload(count=900)  # no campaigns where the USA playinfo has them
	playinfo(other / "playinfo_v131-2022-11-18-EU.boss", (odd, EUR))
	message = serve.give_free_plays(5, KEY, DAY, "EUR")
	assert message.startswith("The EUR playinfo (playinfo_v131-2022-11-18-EU.boss) is live as it is, under SpotPass ID")
	assert "free plays couldn't be changed" in message
	(game,) = live()
	assert game.content == odd and game.program_id == EUR and game.ns_data_id == serve.load_state()["last_ns_data_id"]
	assert serve.playinfo_campaigns(KEY) == []  # the Free plays tab and status show none instead of failing

	# The USA one is Nintendo's and known: a problem with it is still an error
	playinfo(serve.PLAYINFO_BASE, (payload(count=900), USA))
	with pytest.raises(ValueError, match="aren't where they're expected"):
		serve.give_free_plays(5, KEY, DAY, "USA")


def test_no_eur_playinfo_says_where_to_get_it(other):
	playinfo(other / "playinfo_v131-elsewhere.boss", (payload(), USA))  # another USA one doesn't count
	with pytest.raises(FileNotFoundError, match="FGONLYT"):
		serve.give_free_plays(5, KEY, DAY, "EUR")
	assert not serve.LIVE_PLAYINFO.exists()

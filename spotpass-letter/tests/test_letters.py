"""Letters: picture conversion, the history, and putting a letter live, with a
throwaway key and made-up data."""

from pathlib import Path
import io
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import letters  # noqa: E402
import make_letter  # noqa: E402
import serve  # noqa: E402

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

KEY = bytes(range(16))


@pytest.fixture(autouse=True)
def private_dirs(tmp_path: Path, monkeypatch):
	monkeypatch.setattr(letters, "LETTERS_DIR", tmp_path / "letters")
	monkeypatch.setattr(serve, "LIVE_NEWS", tmp_path / "other" / "news.dat.boss")
	# Not the install's real other/playinfo_v131.dat.boss (none here: letters use the news task)
	monkeypatch.setattr(serve, "LIVE_PLAYINFO", tmp_path / "other" / "playinfo_v131.dat.boss")
	monkeypatch.setattr(serve, "STATE", tmp_path / "serve_state.json")
	(tmp_path / "other").mkdir()


def picture(size: tuple[int, int], mode: str = "RGB", fmt: str = "PNG") -> bytes:
	image = Image.effect_noise(size, 60).convert(mode)  # noise: hard to compress
	out = io.BytesIO()
	image.save(out, fmt)
	return out.getvalue()


@pytest.mark.parametrize("size,mode", [((1600, 1200), "RGB"), ((300, 900), "RGBA"), ((64, 64), "P")])
def test_prepare_image(size, mode):
	jpeg = letters.prepare_image(picture(size, mode))
	assert make_letter.jpeg_info(jpeg) == (400, 240, False)
	assert len(jpeg) <= make_letter.IMAGE_RECOMMENDED_MAX
	assert Image.open(io.BytesIO(letters.preview_png(jpeg))).size == (200, 120)


def test_prepare_image_rejects_non_pictures(tmp_path: Path):
	with pytest.raises(ValueError, match="isn't a picture"):
		letters.prepare_image(b"not a picture")
	with pytest.raises(ValueError):
		letters.prepare_image(tmp_path / "missing.png")


def test_history_round_trip():
	image = letters.prepare_image(picture((400, 240)))
	first = letters.save_letter(letters.SavedLetter("", "First", "Hello", image=image))
	second = letters.save_letter(letters.SavedLetter("", "Second", "Hi", url="https://example.invalid/", region="EUR"))
	assert first.id != second.id

	loaded = letters.load_letter(first.id)
	assert (loaded.title, loaded.message, loaded.image) == ("First", "Hello", image)
	assert {letter.id for letter in letters.list_letters()} == {first.id, second.id}

	copy = letters.copy_of(loaded)
	assert copy.id == "" and copy.sent is None and copy.image == image

	letters.delete_letter(first.id)
	assert [letter.id for letter in letters.list_letters()] == [second.id]
	with pytest.raises(ValueError):
		letters.delete_letter(first.id)
	with pytest.raises(ValueError):
		letters.delete_letter("../../somewhere")
	with pytest.raises(ValueError):
		letters.save_letter(letters.SavedLetter("", "x", "y", region="JPN"))


def test_validate_letter():
	ok = make_letter.Letter("Title", "Message")
	assert make_letter.validate_letter(ok) == ([], [])
	errors, _ = make_letter.validate_letter(make_letter.Letter("x" * 32, ""))
	assert len(errors) == 2
	_, warnings = make_letter.validate_letter(make_letter.Letter("Title", "Message", url="example.com"))
	assert warnings
	errors, _ = make_letter.validate_letter(make_letter.Letter("Title", "Message", image=b"nope"))
	assert errors


def test_serve_and_remove_letter():
	image = letters.prepare_image(picture((800, 480)))
	saved = letters.SavedLetter("", "From the server", "Line 1\nLine 2", url="https://example.invalid/", image=image)
	message = serve.serve_letter(saved, KEY)
	assert "is live" in message

	# Under both names the console might ask for
	assert [p.name for p in serve.news_files()] == ["news.dat.boss", "news_v131.dat.boss"]
	assert serve.news_files()[0].read_bytes() == serve.news_files()[1].read_bytes()
	_, payloads = make_letter.parse_container(serve.LIVE_NEWS.read_bytes(), KEY)
	payload = payloads[0]
	assert payload.program_id == make_letter.NEWS_PROGRAM_ID and payload.ns_data_id == saved.ns_data_id
	assert payload.content[make_letter.NEWS_BODY_SIZE:] == image
	description = make_letter.describe_news_payload(payload.content)
	assert "'From the server'" in description and "Line 2" in description

	live = serve.live_letter()
	assert live.id == saved.id and live.sent and live.downloaded is None
	assert serve.mark_letter_downloaded().downloaded
	assert serve.mark_letter_downloaded() is None  # only once

	assert serve.remove_letter() == "The letter was taken down."
	assert not any(p.exists() for p in serve.news_files()) and serve.live_letter() is None
	assert letters.load_letter(saved.id).sent  # still in the history


def test_serve_letter_rejects_bad_letters():
	with pytest.raises(ValueError, match="title"):
		serve.serve_letter(letters.SavedLetter("", "", "Message"), KEY)
	assert not serve.LIVE_NEWS.exists() and letters.list_letters() == []


def test_fast_and_pure_aes_agree():
	iv = bytes(range(16, 32))
	data = bytes(range(256)) * 3
	assert make_letter.aes128_ctr(KEY, iv, data) == make_letter.aes128_ctr_pure(KEY, iv, data)
	# The counter carries across all 16 bytes (not only the low 32 bits)
	iv = b"\x00" * 11 + b"\xff" * 5
	assert make_letter.aes128_ctr(KEY, iv, data) == make_letter.aes128_ctr_pure(KEY, iv, data)


GAME = 0x0004000000153500


def playinfo_file(tmp_path: Path, monkeypatch, content: bytes = b"playinfo payload") -> Path:
	path = tmp_path / "other" / "playinfo_v131.dat.boss"
	plain = make_letter.build_container_plain(content, GAME, 0x10001, 0x100, 1, mark_arrived_always=True)
	path.write_bytes(make_letter.encrypt_container(KEY, plain, 1))
	monkeypatch.setattr(serve, "LIVE_PLAYINFO", path)
	return path


def test_letter_goes_out_inside_playinfo(tmp_path: Path, monkeypatch):
	path = playinfo_file(tmp_path, monkeypatch)
	saved = letters.SavedLetter("", "Through playinfo", "Opens with the game")
	assert "Open Badge Arcade" in serve.serve_letter(saved, KEY)

	info, payloads = make_letter.parse_container(path.read_bytes(), KEY)
	assert [p.program_id for p in payloads] == [GAME, make_letter.NEWS_PROGRAM_ID]
	game, news = payloads
	assert game.content == b"playinfo payload" and game.datatype == 0x10001 and game.ns_data_id != 0x100  # a new ID
	assert info["flags0"] == 0x80  # playinfo keeps its own content flags
	assert news.ns_data_id == saved.ns_data_id and "'Through playinfo'" in make_letter.describe_news_payload(news.content)
	assert serve.letter_in_playinfo()

	# Taking it down puts back the game's payload alone, under yet another new ID
	serve.remove_letter(KEY)
	_, payloads = make_letter.parse_container(path.read_bytes(), KEY)
	assert [p.content for p in payloads] == [b"playinfo payload"] and payloads[0].ns_data_id > game.ns_data_id
	assert not serve.letter_in_playinfo()


def test_letter_without_playinfo_uses_the_news_task(tmp_path: Path, monkeypatch):
	monkeypatch.setattr(serve, "LIVE_PLAYINFO", tmp_path / "other" / "playinfo_v131.dat.boss")
	message = serve.serve_letter(letters.SavedLetter("", "News only", "Text"), KEY)
	assert "Give free plays once" in message and not serve.letter_in_playinfo()
	assert serve.news_files()[0].exists()


def test_free_plays_keep_an_undelivered_letter(tmp_path: Path, monkeypatch):
	import datetime
	import hmac
	import struct
	import free_plays
	hmac_key = bytes(range(32, 48))
	(tmp_path / "badge_arcade_hmac.key").write_text(hmac_key.hex())
	monkeypatch.setattr(serve, "HERE", tmp_path)
	monkeypatch.setattr(serve, "game_date", lambda: None)
	body = bytearray(free_plays.DAILY_COUNT_OFFSET) + struct.pack("<I", 1) + free_plays.DAILY_ENTRY.pack(1, 1, 0, 0, 1)
	base = playinfo_file(tmp_path, monkeypatch, bytes(body) + hmac.digest(hmac_key, bytes(body), "sha256"))
	monkeypatch.setattr(serve, "PLAYINFO_BASE", base.with_name("playinfo_base.enc"))
	serve.PLAYINFO_BASE.write_bytes(base.read_bytes())

	serve.serve_letter(letters.SavedLetter("", "Still coming", "Text"), KEY)
	serve.give_free_plays(5, KEY, datetime.date(2026, 9, 29))
	_, payloads = make_letter.parse_container(base.read_bytes(), KEY)
	assert [p.program_id for p in payloads] == [GAME, make_letter.NEWS_PROGRAM_ID]
	assert free_plays.daily_campaigns(payloads[0].content)[0][4] == 5

	# Once the 3DS has it, new free plays go out without it
	serve.mark_letter_downloaded()
	serve.give_free_plays(3, KEY, datetime.date(2026, 9, 29))
	_, payloads = make_letter.parse_container(base.read_bytes(), KEY)
	assert [p.program_id for p in payloads] == [GAME] and not serve.letter_in_playinfo()

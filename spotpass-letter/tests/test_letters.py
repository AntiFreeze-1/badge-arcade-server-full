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

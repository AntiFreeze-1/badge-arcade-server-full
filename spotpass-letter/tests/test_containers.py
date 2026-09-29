"""Round trips of the SpotPass container and payload code, with throwaway keys
and synthetic data only (no Nintendo files or console keys needed).

Run with:  python -m pytest tests   (from spotpass-letter/)
"""

from pathlib import Path
import datetime
import hmac
import os
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import custom_week  # noqa: E402
import free_plays  # noqa: E402
import make_letter  # noqa: E402
import repack  # noqa: E402

KEY = bytes(range(16))  # throwaway key: tests the container logic, not the real key


def container(payload: bytes, ns_data_id: int = 0x100, serial: int = 1) -> bytes:
	plain = make_letter.build_container_plain(payload, make_letter.NEWS_PROGRAM_ID, 0x20001, ns_data_id, 1)
	return make_letter.encrypt_container(KEY, plain, serial, iv12=bytes(12))


def test_aes_vectors():
	make_letter.aes_self_test()
	# The pure-Python AES and pycryptodome's must agree, since repack.py mixes them
	data = os.urandom(100)
	iv12 = os.urandom(12)
	assert make_letter.aes128_ctr(KEY, iv12 + b"\x00\x00\x00\x01", data) == repack.ctr(KEY, iv12, data)


def test_letter_round_trip():
	letter = make_letter.Letter("Hello", "Line one\r\nLine two", url="https://example.invalid/", ns_data_id=0x1234)
	payload = make_letter.build_news_payload(letter)
	assert len(payload) == make_letter.NEWS_BODY_SIZE

	info, payloads = make_letter.parse_container(container(payload, 0x1234, 42), KEY)
	assert info["serial"] == 42 and info["payload_count"] == 1
	assert payloads[0].ns_data_id == 0x1234 and payloads[0].content == payload

	description = make_letter.describe_news_payload(payload)
	assert "'Hello'" in description and "Line two" in description and "https://example.invalid/" in description


def test_letter_limits():
	with pytest.raises(ValueError):
		make_letter.build_news_payload(make_letter.Letter("x" * 32, "message"))
	with pytest.raises(ValueError):
		make_letter.build_news_payload(make_letter.Letter("title", "x" * 3000))


def test_parse_rejects_wrong_key_and_bad_data():
	data = container(b"payload")
	with pytest.raises(ValueError, match="wrong key"):
		make_letter.parse_container(data, bytes(16))
	with pytest.raises(ValueError, match="magic"):
		make_letter.parse_container(b"xxxx" + data[4:], KEY)
	with pytest.raises(ValueError, match="size"):
		make_letter.parse_container(data + b"\x00", KEY)


def test_repack_changes_ns_data_id():
	payload = os.urandom(300)
	out, old_id = repack.repack(container(payload, 0x100), KEY, 0x5A0, serial=7)
	assert old_id == 0x100
	assert repack.verify(out, KEY) == (0x5A0, len(payload))
	info, payloads = make_letter.parse_container(out, KEY)
	assert info["serial"] == 7 and payloads[0].ns_data_id == 0x5A0 and payloads[0].content == payload


def playinfo(hmac_key: bytes, campaigns: int) -> bytes:
	body = bytearray(free_plays.DAILY_COUNT_OFFSET)
	body += struct.pack("<I", campaigns)
	for i in range(campaigns):
		body += free_plays.DAILY_ENTRY.pack(1000 + i, 1, 1_700_000_000 + i * 86400, 1_700_086_399 + i * 86400, 1)
	return bytes(body) + hmac.digest(hmac_key, bytes(body), "sha256")


def test_free_plays():
	hmac_key = os.urandom(16)
	payload = playinfo(hmac_key, 3)
	start = datetime.datetime(2026, 1, 5, tzinfo=datetime.UTC)
	out = free_plays.set_free_plays(payload, hmac_key, 5, start)

	assert hmac.digest(hmac_key, out[:-32], "sha256") == out[-32:]
	campaigns = free_plays.daily_campaigns(out)
	assert [c[4] for c in campaigns] == [5, 5, 5]
	assert [c[2] for c in campaigns] == [int(start.timestamp()) + i * 86400 for i in range(3)]
	assert len({c[0] for c in campaigns}) == 3  # new, distinct campaign IDs

	with pytest.raises(ValueError, match="HMAC"):
		free_plays.set_free_plays(payload, bytes(16), 5, None)


def test_free_plays_pack_round_trip():
	hmac_key = os.urandom(16)
	data = container(playinfo(hmac_key, 2))
	body, payload = free_plays.unpack(data, KEY)
	new_payload = free_plays.set_free_plays(payload, hmac_key, 9, None)
	out = free_plays.pack(data, KEY, body, new_payload, 0x777, serial=3)
	_, payloads = make_letter.parse_container(out, KEY)
	assert payloads[0].ns_data_id == 0x777 and payloads[0].content == new_payload


def test_sarc_round_trip():
	files = {"a/one.bin": b"1" * 5, "b/two.xml": b"<x/>", "three": os.urandom(33)}
	assert custom_week.sarc_read(custom_week.sarc_write(files, 0x80)) == files


def test_daily_lineup_rotates_within_series():
	setups = ["Mro_1", "Mro_2", "Mro_3", "Zel_1"]
	lineup = custom_week.daily_lineup(setups, days=3, per_series=1)
	assert lineup == [["Mro_1", "Zel_1"], ["Mro_2", "Zel_1"], ["Mro_3", "Zel_1"]]
	assert custom_week.daily_lineup(setups, days=1, per_series=0) == [setups]


def test_picture_flag_is_set_for_jpeg_and_mpo():
	# All 155 real letters with a picture have byte 2 set, 3D (MPO) pictures included
	frame = b"\xff\xc0\x00\x11\x08\x00\xf0\x01\x90" + bytes(20)  # 400x240
	jpeg = b"\xff\xd8" + frame
	mpo = b"\xff\xd8\xff\xe2\x00\x06MPF\x00" + frame  # APP2 "MPF" segment: a 3D picture
	assert make_letter.jpeg_info(mpo) == (400, 240, True)
	for image, flag in ((None, 0), (jpeg, 1), (mpo, 1)):
		assert make_letter.build_news_payload(make_letter.Letter("T", "M", image=image))[2] == flag


def test_compare_with_a_real_letter():
	# A stand-in for Nintendo's container: same letter, but another datatype and
	# a different flag byte, which compare must point out
	letter = make_letter.Letter("A Message from Arcade Bunny!", "Hello!\nNew badges.", ns_data_id=0x9639)
	news = bytearray(make_letter.build_news_payload(letter))
	news[6] = 0
	plain = make_letter.build_container_plain(bytes(news), make_letter.NEWS_PROGRAM_ID, 0x10001, 0x9639, 1)
	rows = {name: (real, ours) for name, real, ours in make_letter.comparison(make_letter.encrypt_container(KEY, plain, 5), KEY)}
	assert rows["payload: datatype"] == ("0x10001", "0x20001")
	assert rows["payload: program ID"][0] == rows["payload: program ID"][1]
	assert rows["letter: flags"][0] == rows["letter: flags"][1]  # rebuilt with the real letter's own flag values
	assert rows["letter: header, title, message"][0] == "same bytes"


def test_compare_with_two_payloads(capsys, tmp_path, monkeypatch):
	# Nintendo's Badge Arcade letter carries a payload for the game next to the letter
	news = make_letter.build_news_payload(make_letter.Letter("Hi", "There", ns_data_id=7))
	plain = make_letter.build_container_plain_multi([
		(b"game data", 0x0004000000134600, 0x10001, 7, 1),
		(news, make_letter.NEWS_PROGRAM_ID, 0x20001, 7, 1),
	])
	container = make_letter.encrypt_container(KEY, plain, 5)
	_, payloads = make_letter.parse_container(container, KEY)
	assert [p.content for p in payloads] == [b"game data", news]

	rows = {name: (real, ours) for name, real, ours in make_letter.comparison(container, KEY)}
	assert rows["container: payloads"] == ("0004000000134600, 0004013000003502", "0004013000003502")
	assert rows["payload: datatype"] == ("0x20001", "0x20001")

	path = tmp_path / "real.boss"
	path.write_bytes(container)
	monkeypatch.setattr(make_letter, "load_key", lambda args: KEY)
	assert make_letter.main(["compare", str(path), "--extract", str(tmp_path / "x")]) == 0
	out = capsys.readouterr().out
	assert "also has a payload for 0004000000134600" in out and "game data" in out
	assert (tmp_path / "x" / "real_0004000000134600_0.bin").read_bytes() == b"game data"


def test_container_matches_nintendos_letter():
	# Badge Arcade's real letter: content flags 0, datatype 0x20001, payload version 1,
	# and the letter header's own version field 0
	letter = make_letter.Letter("Hi", "There", ns_data_id=0x291E, version=0)
	news = make_letter.build_news_payload(letter)
	real = make_letter.encrypt_container(KEY, make_letter.build_container_plain(news, make_letter.NEWS_PROGRAM_ID, 0x20001, 0x291E, 1), 1)
	info, _ = make_letter.parse_container(real, KEY)
	assert info["flags0"] == 0x00
	# compare rebuilds ours with the real payload's version, so nothing shows as different
	rows = make_letter.comparison(real, KEY)
	assert [name for name, theirs, ours in rows if ours and theirs != ours] == []

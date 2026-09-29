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
	with pytest.raises(SystemExit):
		make_letter.build_news_payload(make_letter.Letter("x" * 32, "message"))
	with pytest.raises(SystemExit):
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

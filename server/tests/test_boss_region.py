"""Converting SpotPass files made for the USA Badge Arcade for an EUR console, and back."""

from pathlib import Path
import hashlib
import http.client
import os
import socket
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from badge_arcade import boss_region, http_server  # noqa: E402
from badge_arcade.boss_region import (BOSS_HEADER_SIZE, CONTENT_HEADER_SIZE, PAYLOAD_HEADER_SIZE,  # noqa: E402
                                      TITLE_IDS, ctr, retarget)
from badge_arcade.config import Config  # noqa: E402
from badge_arcade.storage import Storage  # noqa: E402

KEY = bytes(range(16))  # any key works for the container format; only load_key checks for the real one
IV = bytes(range(0x40, 0x4C))
NEWS = 0x0004013000003502


def container(payloads: list[tuple[int, bytes]], key: bytes = KEY) -> bytes:
	"""A container like Nintendo's: (program ID, payload) pairs, each with a valid hash and a signature."""
	ch = bytearray(CONTENT_HEADER_SIZE)
	struct.pack_into(">H", ch, 0x10, len(payloads))
	ch[0x12:0x32] = hashlib.sha256(bytes(ch[:0x12]) + b"\x00\x00").digest()
	ch[0x32:] = b"\x5a" * 0x100
	body = bytes(ch)
	for ns, (pid, payload) in enumerate(payloads, 0x5C0):
		ph = bytearray(PAYLOAD_HEADER_SIZE)
		struct.pack_into(">QIIIII", ph, 0, pid, 0, 0x10001, len(payload), ns, 1)
		ph[0x1C:0x3C] = hashlib.sha256(bytes(ph[:0x1C]) + b"\x00\x00" + payload).digest()
		ph[0x3C:] = b"\xa5" * 0x100
		body += bytes(ph) + payload
	header = bytearray(BOSS_HEADER_SIZE)
	header[0:4] = b"boss"
	struct.pack_into(">IIQHHHH", header, 4, 0x10001, BOSS_HEADER_SIZE + len(body), 7, 1, 0, 2, 2)
	header[0x1C:0x28] = IV
	return bytes(header) + ctr(key, IV, body)


def payloads(data: bytes, key: bytes = KEY) -> list[tuple[int, bytes, bool]]:
	"""(program ID, payload, hash valid) of each payload."""
	body = ctr(key, data[0x1C:0x28], data[BOSS_HEADER_SIZE:])
	out, off = [], CONTENT_HEADER_SIZE
	for _ in range(struct.unpack_from(">H", body, 0x10)[0]):
		pid, length = struct.unpack_from(">Q", body, off)[0], struct.unpack_from(">I", body, off + 0x10)[0]
		payload = body[off + PAYLOAD_HEADER_SIZE:off + PAYLOAD_HEADER_SIZE + length]
		valid = hashlib.sha256(body[off:off + 0x1C] + b"\x00\x00" + payload).digest() == body[off + 0x1C:off + 0x3C]
		out.append((pid, payload, valid))
		off += PAYLOAD_HEADER_SIZE + length
	return out


def test_retarget_to_eur_and_back():
	usa = container([(TITLE_IDS["USA"], b"machines" * 50), (NEWS, b"letter"), (TITLE_IDS["USA"], b"badges")])
	eur = retarget(usa, KEY, TITLE_IDS["EUR"])
	assert eur is not None and len(eur) == len(usa) and eur[:BOSS_HEADER_SIZE] == usa[:BOSS_HEADER_SIZE]
	assert payloads(eur) == [(TITLE_IDS["EUR"], b"machines" * 50, True), (NEWS, b"letter", True), (TITLE_IDS["EUR"], b"badges", True)]
	assert retarget(usa, KEY, TITLE_IDS["USA"]) is None  # already the console's own
	assert retarget(eur, KEY, TITLE_IDS["EUR"]) is None
	back = retarget(eur, KEY, TITLE_IDS["USA"])  # EUR files for a USA console
	assert [pid for pid, _, valid in payloads(back) if valid] == [TITLE_IDS["USA"], NEWS, TITLE_IDS["USA"]]


def test_retarget_rejects_bad_input():
	with pytest.raises(ValueError):
		retarget(b"not a container" * 40, KEY, TITLE_IDS["EUR"])
	with pytest.raises(ValueError):  # wrong key
		retarget(container([(TITLE_IDS["USA"], b"x")]), bytes(16), TITLE_IDS["EUR"])


def test_load_key(tmp_path: Path):
	(tmp_path / "boot9.bin").write_bytes(bytes(0x10000))
	with pytest.raises(ValueError):  # not a retail boot9.bin
		boss_region.load_key(tmp_path / "boot9.bin")
	(tmp_path / "key.txt").write_text(KEY.hex())
	with pytest.raises(ValueError):  # not the SpotPass key
		boss_region.load_key(tmp_path / "key.txt")


def get(port: int, path: str, headers: dict | None = None) -> tuple[int, bytes, str | None]:
	connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
	try:
		connection.request("GET", path, headers=headers or {})
		response = connection.getresponse()
		return response.status, response.read(), response.getheader("ETag")
	finally:
		connection.close()


@pytest.fixture
def boss_server(tmp_path: Path):
	with socket.socket() as s:
		s.bind(("127.0.0.1", 0))
		port = s.getsockname()[1]
	(tmp_path / "boss").mkdir()
	config = Config(public_host="127.0.0.1", bind_host="127.0.0.1", http_port=port, boss_dir="boss",
		boss_key_file="boot9.bin", base_dir=tmp_path)
	storage = Storage(config.data_path)
	server = http_server.start_http_server(config, storage)
	yield port, tmp_path / "boss", server.RequestHandlerClass.boss.regions
	server.shutdown()
	server.server_close()
	storage.close()


def test_server_converts_for_the_console_region(boss_server, monkeypatch):
	port, boss_dir, regions = boss_server
	usa = container([(TITLE_IDS["USA"], b"week" * 100)])
	(boss_dir / "data_v131.dat.boss").write_bytes(usa)
	eur_url, usa_url = "/boss/p01/nsa/J6la9Kj8iqTvAPOq/data/data_v131.dat", "/boss/p01/nsa/OvbmGLZ9senvgV3K/data/data_v131.dat"

	# Without boot9.bin the file goes out unchanged (and the log says why an EUR console can't save it)
	status, data, _ = get(port, eur_url)
	assert status == 200 and data == usa

	monkeypatch.setattr(regions, "key", lambda: KEY)
	status, data, etag = get(port, eur_url)
	assert status == 200 and payloads(data) == [(TITLE_IDS["EUR"], b"week" * 100, True)]
	assert get(port, eur_url, {"If-None-Match": etag})[0] == 304
	status, data, usa_etag = get(port, usa_url)
	assert status == 200 and data == usa and usa_etag != etag

	# A newly served week is converted again
	newer = container([(TITLE_IDS["USA"], b"next week")])
	(boss_dir / "data_v131.dat.boss").write_bytes(newer)
	os.utime(boss_dir / "data_v131.dat.boss", (1, 2_000_000_000))
	assert payloads(get(port, eur_url)[1]) == [(TITLE_IDS["EUR"], b"next week", True)]

	# Files that aren't containers still go out as they are
	(boss_dir / "plain.dat").write_bytes(b"hello")
	assert get(port, "/boss/p01/nsa/J6la9Kj8iqTvAPOq/data/plain.dat")[1] == b"hello"


def week(region: str) -> bytes:
	"""A payload with a week's text for one region, the way it sits in Nintendo's weeks."""
	return b"SARC....Schedule.xml\0sharc/221229-230105.sarc\0" + f"message/boss_{region}/{region[:2]}en/boss/slotA00/StartUp.msbf".encode() + b"\0" * 9


def test_made_for():
	usa_week = container([(TITLE_IDS["USA"], week("USA"))])
	assert boss_region.made_for(usa_week, KEY) == {"USA"}
	# Converting a week for another console doesn't change what it was made for: its text stays USA
	assert boss_region.made_for(retarget(usa_week, KEY, TITLE_IDS["EUR"]), KEY) == {"USA"}
	assert boss_region.made_for(container([(TITLE_IDS["EUR"], week("EUR"))]), KEY) == {"EUR"}
	assert boss_region.made_for(container([(TITLE_IDS["USA"], week("USA") + week("EUR"))]), KEY) == {"USA", "EUR"}
	# Anything else (allbadge, playinfo): the program it's made out to
	assert boss_region.made_for(container([(TITLE_IDS["EUR"], b"badges")]), KEY) == {"EUR"}
	assert boss_region.made_for(container([(NEWS, b"letter")]), KEY) == set()
	with pytest.raises(ValueError):
		boss_region.made_for(usa_week, bytes(16))


def test_server_warns_when_a_3ds_gets_another_regions_files(boss_server, monkeypatch, caplog):
	port, boss_dir, regions = boss_server
	monkeypatch.setattr(regions, "key", lambda: KEY)
	eur, usa = "/boss/p01/nsa/J6la9Kj8iqTvAPOq/data/", "/boss/p01/nsa/OvbmGLZ9senvgV3K/data/"

	def warnings_for(path: str) -> list[str]:
		caplog.clear()
		assert get(port, path)[0] == 200
		return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]

	(boss_dir / "data_v131.dat.boss").write_bytes(container([(TITLE_IDS["USA"], week("USA"))]))
	(warning,) = warnings_for(eur + "data_v131.dat")
	assert "is a week made for the USA Badge Arcade, but this 3DS runs the EUR one" in warning
	assert "we're still doing some setup work" in warning and "Serve a week made for EUR" in warning
	assert warnings_for(eur + "data_v131.dat") == []  # once
	assert warnings_for(usa + "data_v131.dat") == []

	# An EUR week: right for an EUR 3DS, wrong for a USA one
	(boss_dir / "data_v131.dat.boss").write_bytes(container([(TITLE_IDS["EUR"], week("EUR"))]))
	os.utime(boss_dir / "data_v131.dat.boss", (1, 2_000_000_000))
	assert warnings_for(eur + "data_v131.dat") == []
	assert "made for the EUR Badge Arcade, but this 3DS runs the USA one" in warnings_for(usa + "data_v131.dat")[0]

	# The badges too; the free plays aren't checked (they're always made from the USA playinfo)
	(boss_dir / "allbadge_v131.dat.boss").write_bytes(container([(TITLE_IDS["USA"], b"badges")]))
	(warning,) = warnings_for(eur + "allbadge_v131.dat")
	assert "holds the USA Badge Arcade's badges, but this 3DS runs the EUR one" in warning
	(boss_dir / "playinfo_v131.dat.boss").write_bytes(container([(TITLE_IDS["USA"], b"plays")]))
	assert warnings_for(eur + "playinfo_v131.dat") == []

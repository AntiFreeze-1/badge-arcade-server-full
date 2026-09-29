"""Unit tests for pieces that don't need the NEX servers: the save tools'
FreePlayData helpers, stale upload cleanup and HTTP request limits."""

from pathlib import Path
import http.client
import socket
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from badge_arcade import admin, http_server  # noqa: E402
from badge_arcade.config import Config  # noqa: E402
from badge_arcade.storage import Storage  # noqa: E402


def test_meta_fields_and_edit():
	meta = bytes(range(8)) + bytes(32)  # two fields, then the signature
	assert admin.meta_fields(meta) == [(0, 0x03020100), (4, 0x07060504)]

	edited = admin.edit_meta(meta, {4: 1})
	assert admin.meta_fields(edited) == [(0, 0x03020100), (4, 1)]
	assert edited[-32:] == meta[-32:]

	for offset in (2, 8, 40):  # unaligned, inside the signature, past the end
		with pytest.raises(ValueError):
			admin.edit_meta(meta, {offset: 1})


def test_prune_uploads(tmp_path: Path):
	storage = Storage(tmp_path)
	try:
		data_id = storage.create_object(1234, data_type=100)
		storage.add_upload("done", data_id, 1, 3)
		storage.store_upload("done", b"abc")
		assert storage.finish_upload(data_id, 1)

		# An update the game started but never completed, and one still in progress
		storage.add_upload("stale", data_id, 2, 3)
		storage.store_upload("stale", b"new")
		storage.add_upload("fresh", data_id, 3, 3)
		old = int(time.time()) - 2 * 86400
		storage._db.execute("UPDATE uploads SET created=? WHERE key='stale'", (old,))

		assert storage.prune_uploads() == 1
		assert storage.get_upload("stale") is None and storage.get_upload("fresh") is not None
		assert not storage.object_file(data_id, 2).exists()
		assert storage.object_file(data_id, 1).read_bytes() == b"abc"  # the current save is kept
	finally:
		storage.close()


@pytest.fixture
def http_port(tmp_path: Path):
	with socket.socket() as s:
		s.bind(("127.0.0.1", 0))
		port = s.getsockname()[1]
	config = Config(public_host="127.0.0.1", bind_host="127.0.0.1", http_port=port, boss_dir=None, base_dir=tmp_path)
	storage = Storage(config.data_path)
	server = http_server.start_http_server(config, storage)
	yield port
	server.shutdown()
	server.server_close()
	storage.close()


def post(port: int, content_length: str, body: bytes = b"") -> int:
	connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
	try:
		connection.putrequest("POST", "/ac")
		connection.putheader("Content-Length", content_length)
		connection.endheaders(body)
		return connection.getresponse().status
	finally:
		connection.close()


def test_http_body_limits(http_port: int):
	assert post(http_port, str(http_server.MAX_BODY + 1)) == 413
	assert post(http_port, "not-a-number") == 400
	assert post(http_port, "-5") == 400
	assert post(http_port, "14", b"action=TE9HSU4") == 200  # still answers normal requests


def test_address_in_use_message():
	import errno
	from badge_arcade.__main__ import address_in_use
	in_use = OSError(errno.EADDRINUSE, "Address already in use")
	assert address_in_use(in_use)
	# The NEX servers raise it wrapped in (nested) exception groups, like in a real server.log
	assert address_in_use(BaseExceptionGroup("x", [ExceptionGroup("y", [in_use])]))
	assert not address_in_use(OSError(errno.EACCES, "Permission denied"))
	assert not address_in_use(ExceptionGroup("x", [ValueError("no")]))


def test_address_for(monkeypatch):
	from badge_arcade import config as config_module
	auto = Config(public_host="auto", base_dir=Path("."))
	auto.public_host = "10.99.99.99"  # what --public-host sets: only a fallback now
	assert auto.address_for("127.0.0.1") == "127.0.0.1"
	assert auto.address_for(None) == "10.99.99.99"
	assert auto.address_for("not an ip") == "10.99.99.99"
	assert auto.address_for("::1") == "10.99.99.99"
	monkeypatch.setattr(config_module, "local_address_toward", lambda ip: "192.168.137.1" if ip.startswith("192.168.137.") else None)
	assert auto.address_for("192.168.137.122") == "192.168.137.1"  # a 3DS on the hotspot
	assert auto.address_for("10.0.0.5") == "10.99.99.99"  # no route found

	fixed = Config(public_host="10.1.2.3", base_dir=Path("."))
	assert fixed.public_host_fixed and fixed.address_for("192.168.137.122") == "10.1.2.3"


@pytest.fixture
def auto_server(tmp_path: Path):
	with socket.socket() as s:
		s.bind(("127.0.0.1", 0))
		port = s.getsockname()[1]
	config = Config(public_host="auto", bind_host="127.0.0.1", http_port=port, boss_dir=None, base_dir=tmp_path)
	config.public_host = "10.99.99.99"  # a stale address, like a server started before the hotspot
	storage = Storage(config.data_path)
	server = http_server.start_http_server(config, storage)
	yield config, port
	server.shutdown()
	server.server_close()
	storage.close()


def post_form(port: int, path: str, body: bytes) -> bytes:
	connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
	try:
		connection.request("POST", path, body, {"Content-Type": "application/x-www-form-urlencoded"})
		return connection.getresponse().read()
	finally:
		connection.close()


def test_logins_get_the_address_the_3ds_can_reach(auto_server):
	config, port = auto_server
	# NASC login (behind the proxy the console's address comes in X-Forwarded-For; here it's 127.0.0.1)
	form = "&".join(f"{k}={http_server.nasc_encode(v)}" for k, v in {"action": "LOGIN", "gameid": "00134600"}.items())
	reply = dict(pair.split("=", 1) for pair in post_form(port, "/ac", form.encode()).decode().split("&"))
	assert http_server.nasc_decode(reply["locator"]).decode() == f"127.0.0.1:{config.auth_port}"

	# NEX token (the proxy passes the console's address as ip)
	body = post_form(port, "/nex_token", b"game_server_id=00134600&pid=1750000001&ip=127.0.0.1").decode()
	assert "<host>127.0.0.1</host>" in body

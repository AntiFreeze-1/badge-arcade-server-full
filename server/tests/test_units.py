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

"""Hotspot mode helpers (no network or admin rights needed) and --public-host."""

from pathlib import Path
import json
import socket
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mitm"))

import hotspot  # noqa: E402
from nintendo_hosts import HANDLED_HOSTS  # noqa: E402
from badge_arcade import __main__ as server_main  # noqa: E402

HOSTS = "# Copyright (c) Microsoft Corp.\r\n#\r\n127.0.0.1\tlocalhost\r\n10.0.0.2\tnas.local"


def test_hosts_block_round_trip():
	text = hotspot.with_block(HOSTS, "192.168.137.1")
	assert text.startswith(HOSTS + "\r\n")
	for host in HANDLED_HOSTS:
		assert f"192.168.137.1\t{host}\r\n" in text
	assert hotspot.block_ip(text) == "192.168.137.1"
	assert hotspot.block_ip(HOSTS) is None

	# Replacing keeps a single block, and removing restores the file
	moved = hotspot.with_block(text, "192.168.50.1")
	assert moved.count(hotspot.BEGIN) == 1 and hotspot.block_ip(moved) == "192.168.50.1"
	assert hotspot.strip_block(moved) == HOSTS + "\r\n"
	assert hotspot.strip_block(HOSTS) == HOSTS


def test_hosts_block_keeps_lines_after_it():
	text = hotspot.with_block(HOSTS, "192.168.137.1") + "10.0.0.3\tprinter.local\r\n"
	assert hotspot.strip_block(text) == HOSTS + "\r\n10.0.0.3\tprinter.local\r\n"


def encode_name(name: str) -> bytes:
	return b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\0"


def test_dns_query_and_reply():
	query = hotspot.dns_query("nasc.nintendowifi.net", 0x1234)
	assert query[:12] == struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
	assert query[12:] == encode_name("nasc.nintendowifi.net") + struct.pack(">HH", 1, 1)

	# A reply with a CNAME (pointing back at the question's name) and an A record
	cname = encode_name("nasc.example.net")
	reply = (
		struct.pack(">HHHHHH", 0x1234, 0x8180, 1, 2, 0, 0) + query[12:]
		+ b"\xc0\x0c" + struct.pack(">HHIH", 5, 1, 60, len(cname)) + cname
		+ b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton("192.168.137.1")
	)
	assert hotspot.a_records(reply, 0x1234) == ["192.168.137.1"]
	assert hotspot.a_records(reply, 0x9999) == []  # someone else's reply


def test_public_host_flag(tmp_path, monkeypatch):
	config = tmp_path / "config.json"
	config.write_text(json.dumps({"public_host": "10.0.0.5", "kerberos_password": "test"}))
	started = []
	monkeypatch.setattr(server_main.anyio, "run", lambda serve, config: started.append(config))

	monkeypatch.setattr(sys, "argv", ["badge_arcade", str(config), "--public-host", "192.168.137.1"])
	server_main.main()
	monkeypatch.setattr(sys, "argv", ["badge_arcade", str(config)])
	server_main.main()
	assert [c.public_host for c in started] == ["192.168.137.1", "10.0.0.5"]

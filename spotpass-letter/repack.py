"""EXPERIMENT: re-pack a real SpotPass container with a different NsData ID.

The 3DS keeps SpotPass downloads by NsData ID and Badge Arcade appears to use
the highest one it has, which is still Nintendo's final March 2023 data
(0x59d / 0x59e). Raising the ID of an older week lets it take priority.

Changing the ID invalidates Nintendo's RSA signature on the payload header
(the SHA-256 hash is recomputed; the signature can't be), so this only works
if the console skips that check. Stock Luma doesn't patch the SpotPass (BOSS)
module; whether the console keeps such files is unconfirmed (it does
download them).

  python repack.py IN OUT --ns-data-id 0x5a0 --boot9 boot9.bin
"""

import argparse
import hashlib
import struct
import sys
import time
from pathlib import Path

from Crypto.Cipher import AES

from make_letter import (BOSS_HEADER_SIZE, CONTENT_HEADER_SIZE, PAYLOAD_HEADER_SIZE, build_boss_header,
                         key_from_boot9)


def ctr(key: bytes, iv12: bytes, data: bytes) -> bytes:
	# Same as make_letter.aes128_ctr (counter starts at 1), but fast
	return AES.new(key, AES.MODE_CTR, nonce=iv12, initial_value=1).encrypt(data)


def repack(data: bytes, key: bytes, ns_data_id: int, serial: int) -> tuple[bytes, int]:
	iv12 = data[0x1C:0x28]
	body = bytearray(ctr(key, iv12, data[BOSS_HEADER_SIZE:]))
	ch = body[:CONTENT_HEADER_SIZE]
	if hashlib.sha256(bytes(ch[:0x12]) + b"\x00\x00").digest() != bytes(ch[0x12:0x32]):
		raise ValueError("content header hash mismatch (wrong key?)")
	if struct.unpack_from(">H", ch, 0x10)[0] != 1:
		raise ValueError("expected exactly one payload")

	off = CONTENT_HEADER_SIZE
	pid, _unk, datatype, length, old_id, version = struct.unpack_from(">QIIIII", body, off)
	payload = bytes(body[off + PAYLOAD_HEADER_SIZE:off + PAYLOAD_HEADER_SIZE + length])
	struct.pack_into(">I", body, off + 0x14, ns_data_id)
	header = bytes(body[off:off + 0x1C])
	body[off + 0x1C:off + 0x3C] = hashlib.sha256(header + b"\x00\x00" + payload).digest()
	body[off + 0x3C:off + 0x13C] = bytes(0x100)  # signature no longer valid; zero it like our letters

	out = build_boss_header(len(data), serial, iv12) + ctr(key, iv12, bytes(body))
	return out, old_id


def verify(data: bytes, key: bytes) -> tuple[int, int]:
	body = ctr(key, data[0x1C:0x28], data[BOSS_HEADER_SIZE:])
	off = CONTENT_HEADER_SIZE
	_pid, _unk, _dt, length, ns, _ver = struct.unpack_from(">QIIIII", body, off)
	payload = body[off + PAYLOAD_HEADER_SIZE:off + PAYLOAD_HEADER_SIZE + length]
	assert hashlib.sha256(body[off:off + 0x1C] + b"\x00\x00" + payload).digest() == body[off + 0x1C:off + 0x3C]
	return ns, length


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("input", type=Path)
	parser.add_argument("output", type=Path)
	parser.add_argument("--ns-data-id", type=lambda v: int(v, 0), required=True)
	parser.add_argument("--serial", type=lambda v: int(v, 0), default=None, help="default: current Unix time")
	parser.add_argument("--boot9", type=Path, default=Path("boot9.bin"))
	args = parser.parse_args()

	if args.output.resolve().parent == (Path(__file__).resolve().parent.parent / "other"):
		print("Write to out/ and copy to other/ yourself.")
		return 1

	key = key_from_boot9(args.boot9)
	out, old_id = repack(args.input.read_bytes(), key, args.ns_data_id, args.serial or int(time.time()))
	ns, length = verify(out, key)
	args.output.parent.mkdir(parents=True, exist_ok=True)
	args.output.write_bytes(out)
	print(f"{args.input.name}: NsData ID {old_id:#x} -> {ns:#x}, {length} byte payload, hashes OK -> {args.output}")
	return 0


if __name__ == "__main__":
	sys.exit(main())

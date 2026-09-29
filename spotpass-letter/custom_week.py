"""EXPERIMENT: build a custom Badge Arcade week (data_v131) from archived machines.

Takes a real week as the base (dates, messages, posts) and replaces its
machines with every buildable machine setup of the chosen series, gathered
from all weekly files and allbadge files in ../other. A setup is buildable
when every part it references (cabinet, badges, icon, fixed objects,
attachments) exists in one of those files and the game's master list
(PrizeCollection.xml) knows it.

  python custom_week.py --series MH,Rockman,PokeExt --out out/data_v131-custom.boss
  python custom_week.py --list      # buildable setups per series

The container can't carry a valid Nintendo signature (it's zeroed, like
repack.py), so the console may discard it.
"""

import argparse
import datetime
import hashlib
import re
import struct
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from Crypto.Cipher import AES

from make_letter import BOSS_HEADER_SIZE, CONTENT_HEADER_SIZE, PAYLOAD_HEADER_SIZE, build_boss_header, key_from_boot9

HERE = Path(__file__).resolve().parent
OTHER = HERE.parent / "other"
BASE_WEEK = OTHER / "data_v131-2022-12-29-09-40-NA.enc"
SOURCES = sorted(OTHER.glob("data_v131*")) + [OTHER / "allbadge_v131.dat.boss"]
SOURCES_2016 = [OTHER / "allbadge_v130.dat.boss"]  # made for game version 1.3.0
REF_PREFIXES = ("CrSt_", "CraneIcon_", "Pr_", "FxOb_", "At_")
SARC_HASH_KEY = 0x65
BOM = bytes.fromhex("efbbbf")


# ----- containers -----

def open_container(data: bytes, key: bytes) -> tuple[bytes, bytes]:
	"""(decrypted body, payload)"""
	body = AES.new(key, AES.MODE_CTR, nonce=data[0x1C:0x28], initial_value=1).decrypt(data[BOSS_HEADER_SIZE:])
	length = struct.unpack_from(">I", body, CONTENT_HEADER_SIZE + 0x10)[0]
	start = CONTENT_HEADER_SIZE + PAYLOAD_HEADER_SIZE
	return body, body[start:start + length]


def build_container(base: bytes, base_body: bytes, key: bytes, payload: bytes, ns_data_id: int, serial: int) -> bytes:
	# The content header (and its valid signature) is kept; the payload header gets a new
	# size, ID and hash, and a zeroed signature
	content_header = base_body[:CONTENT_HEADER_SIZE]
	ph = bytearray(base_body[CONTENT_HEADER_SIZE:CONTENT_HEADER_SIZE + PAYLOAD_HEADER_SIZE])
	struct.pack_into(">II", ph, 0x10, len(payload), ns_data_id)
	ph[0x1C:0x3C] = hashlib.sha256(bytes(ph[:0x1C]) + b"\x00\x00" + payload).digest()
	ph[0x3C:0x13C] = bytes(0x100)
	body = content_header + bytes(ph) + payload
	iv12 = base[0x1C:0x28]
	encrypted = AES.new(key, AES.MODE_CTR, nonce=iv12, initial_value=1).encrypt(body)
	return build_boss_header(BOSS_HEADER_SIZE + len(body), serial, iv12) + encrypted


# ----- SARC -----

def sarc_read(blob: bytes) -> dict[str, bytes]:
	assert blob[:4] == b"SARC" and blob[6:8] == b"\xff\xfe"
	data_off = struct.unpack_from("<I", blob, 0x0C)[0]
	count = struct.unpack_from("<H", blob, 0x1A)[0]
	names_off = 0x20 + 16 * count + 8
	files = {}
	for i in range(count):
		_h, attr, start, end = struct.unpack_from("<IIII", blob, 0x20 + 16 * i)
		p = names_off + (attr & 0xFFFFFF) * 4
		files[blob[p:blob.index(b"\0", p)].decode()] = blob[data_off + start:data_off + end]
	return files


def sarc_hash(name: str) -> int:
	h = 0
	for c in name.encode():
		h = (h * SARC_HASH_KEY + c) & 0xFFFFFFFF
	return h


def sarc_write(files: dict[str, bytes], align: int) -> bytes:
	entries = sorted(files.items(), key=lambda item: sarc_hash(item[0]))
	hashes = [sarc_hash(name) for name, _ in entries]
	if len(set(hashes)) != len(hashes):
		raise ValueError("SARC name hash collision")

	names = bytearray()
	name_offsets = []
	for name, _ in entries:
		name_offsets.append(len(names))
		names += name.encode() + b"\0"
		names += bytes(-len(names) % 4)

	header_size = 0x14 + 0x0C + 16 * len(entries) + 8 + len(names)
	data_off = header_size + (-header_size % align)
	nodes = bytearray()
	data = bytearray()
	for (name, content), h, name_off in zip(entries, hashes, name_offsets):
		data += bytes(-len(data) % align)
		nodes += struct.pack("<IIII", h, 0x01000000 | name_off // 4, len(data), len(data) + len(content))
		data += content

	out = bytearray()
	out += struct.pack("<4sHHIIHH", b"SARC", 0x14, 0xFEFF, data_off + len(data), data_off, 0x100, 0)
	out += struct.pack("<4sHHI", b"SFAT", 0x0C, len(entries), SARC_HASH_KEY)
	out += nodes
	out += struct.pack("<4sHH", b"SFNT", 8, 0)
	out += names
	out += bytes(data_off - len(out))
	out += data
	return bytes(out)


def yaz0(src: bytes) -> bytes:
	size = struct.unpack_from(">I", src, 4)[0]
	dst = bytearray()
	s = 16
	while len(dst) < size:
		code = src[s]
		s += 1
		for bit in range(8):
			if len(dst) >= size:
				break
			if code & (0x80 >> bit):
				dst.append(src[s])
				s += 1
			else:
				b1, b2 = src[s], src[s + 1]
				s += 2
				dist = ((b1 & 0xF) << 8 | b2) + 1
				n = b1 >> 4
				if n == 0:
					n = src[s] + 0x12
					s += 1
				else:
					n += 2
				for _ in range(n):
					dst.append(dst[-dist])
	return bytes(dst)


# ----- machines -----

def stem(path: str) -> str:
	return path.rsplit("/", 1)[-1].split(".")[0]


def series(setup: str) -> str:
	return setup.rsplit("_", 1)[0]


def registry(xml: str) -> dict[str, set[str]]:
	return {tag: set(re.findall(rf'<{tag} name="([^"]+)"', xml))
		for tag in ("Crane", "CraneInstance", "CraneIcon", "Prize", "FixedObject", "Attachment")}


def replace_list(xml: str, block: str, tag: str, names: list[str]) -> str:
	match = re.search(rf'( *)<{block} count="\d+">\r?\n.*?</{block}>', xml, re.S)
	indent = match.group(1)
	newline = "\r\n" if "\r\n" in match.group(0) else "\n"
	lines = [f'{indent}<{block} count="{len(names)}">']
	lines += [f'{indent}  <{tag} name="{name}" />' for name in names]
	lines.append(f"{indent}</{block}>")
	return xml[:match.start()] + newline.join(lines) + xml[match.end():]


STAGE_ITEM = """    <FileItem>
      <DateStartText>{start}</DateStartText>
      <DateExpireText>{end}</DateExpireText>
      <ColorText>{color}</ColorText>
      <Comment />
      <RegexSetName>{kind}</RegexSetName>
      <Expression>boss/preview/(?&lt;Dir&gt;.+)/(?&lt;StageName&gt;.+).jpg</Expression>
      <KeyVariable>{key_variable}</KeyVariable>
      <ValueVariable>$(StageName)</ValueVariable>
      <OutputVariable />
      <PreviewVariable>boss/preview/$(Dir)/$(StageName).jpg</PreviewVariable>
      <CommandVariable />
      <ArgumentsVariable />
      <Key>{key}</Key>
      <Value>{setup}</Value>
      <Output />
      <Preview>boss/preview/{week}/{setup}.jpg</Preview>
      <SrcPath>boss/preview/{week}/{setup}.jpg</SrcPath>
      <Command />
      <Arguments />
    </FileItem>
"""


def schedule_days(xml: str) -> list[datetime.date]:
	"""The days of the week, from the PrizeCollection entry's dates."""
	item = re.search(r"<FileItem>(?:(?!</FileItem>).)*<RegexSetName>PrizeCollection</RegexSetName>.*?</FileItem>", xml, re.S)
	start = datetime.datetime.strptime(re.search(r"<DateStartText>(\d{8})<", item.group(0)).group(1), "%Y%m%d").date()
	end = datetime.datetime.strptime(re.search(r"<DateExpireText>(\d{8})<", item.group(0)).group(1), "%Y%m%d").date()
	return [start + datetime.timedelta(days=i) for i in range((end - start).days)]


def daily_lineup(setups: list[str], days: int, per_series: int) -> list[list[str]]:
	"""Up to per_series setups of every series each day, rotating through each series.
	per_series 0 puts every setup on the floor every day."""
	by_series = defaultdict(list)
	for setup in setups:
		by_series[series(setup)].append(setup)
	lineup = []
	for day in range(days):
		today = []
		for members in by_series.values():
			count = len(members) if per_series <= 0 else min(per_series, len(members))
			today += [members[(day * count + i) % len(members)] for i in range(count)]
		lineup.append(today)
	return lineup


def write_schedule(xml: str, week: str, lineup: list[list[str]], days: list[datetime.date]) -> str:
	"""Replaces the daily machine line-up (DefaultStage) and bonus machine (BonusStage)."""
	items = re.findall(r"[ \t]*<FileItem>.*?</FileItem>\r?\n", xml, re.S)
	kept = [item for item in items if not re.search(r"<RegexSetName>(DefaultStage|BonusStage)</RegexSetName>", item)]
	new = []
	slot = 0
	for today, day in zip(lineup, days):
		start, end = day.strftime("%Y%m%d"), (day + datetime.timedelta(days=1)).strftime("%Y%m%d")
		new.append(STAGE_ITEM.format(start=start, end=end, color="#ff77aa", kind="BonusStage",
			key_variable="BonusStageName", key="BonusStageName", setup=today[0], week=week))
		for setup in today:
			new.append(STAGE_ITEM.format(start=start, end=end, color="#ffccaa", kind="DefaultStage",
				key_variable="DefaultStageName$(Slot3)", key=f"DefaultStageName{slot:03d}", setup=setup, week=week))
			slot += 1
	newline = "\r\n" if "\r\n" in xml else "\n"
	new = [item.replace("\n", newline) for item in new]
	head = xml[:xml.index(items[0])]
	tail = xml[xml.index(items[-1]) + len(items[-1]):]
	tail = re.sub(r"<ItemsCount>\d+</ItemsCount>", f"<ItemsCount>{len(kept) + len(new)}</ItemsCount>", tail)
	return head + "".join(kept + new) + tail


class Pool:
	def __init__(self):
		self.resources: dict[str, bytes] = {}  # pc/rt/... -> file
		self.setups: dict[str, bytes] = {}     # setup name -> .cib.szs
		self.by_stem: dict[str, str] = {}

	@classmethod
	def from_sources(cls, key: bytes, include_2016: bool = False) -> "Pool":
		pool = cls()
		for path in SOURCES + (SOURCES_2016 if include_2016 else []):
			top = sarc_read(open_container(path.read_bytes(), key)[1])
			weeks = [name for name in top if name.startswith("sharc/")]
			pool.add(sarc_read(top[weeks[0]]) if weeks else top)
		return pool

	@classmethod
	def from_files(cls, files: dict[str, bytes]) -> "Pool":
		pool = cls()
		pool.add(files)
		return pool

	def add(self, files: dict[str, bytes]) -> None:
		for name, content in files.items():
			if name.startswith("pc/rt/"):
				self.resources.setdefault(name, content)
				self.by_stem.setdefault(stem(name), name)
			elif name.startswith("pc/ci/"):
				self.setups.setdefault(stem(name), content)

	def parts(self, setup: str) -> set[str]:
		text = yaz0(self.setups[setup])
		return {s.decode() for s in re.findall(rb"[A-Za-z0-9_\-]{4,}", text) if s.startswith(tuple(p.encode() for p in REF_PREFIXES))}

	def buildable(self, setup: str, known: dict[str, set[str]]) -> bool:
		if setup not in known["CraneInstance"]:
			return False
		for part in self.parts(setup):
			if part not in self.by_stem:
				return False
			for prefix, tag in (("Pr_", "Prize"), ("CrSt_", "Crane"), ("CraneIcon_", "CraneIcon")):
				if part.startswith(prefix) and part not in known[tag]:
					return False
		return True


class Builder:
	"""Builds custom weeks on top of the base week. Reading the archive takes a few seconds."""

	def __init__(self, key: bytes, include_2016: bool = False):
		self.key = key
		self.base_raw = BASE_WEEK.read_bytes()
		self.base_body, base_payload = open_container(self.base_raw, key)
		self.top = sarc_read(base_payload)
		self.week_name = next(name for name in self.top if name.startswith("sharc/"))
		self.week = sarc_read(self.top[self.week_name])
		self.xml = self.week["pc/PrizeCollection.xml"].decode("utf-8-sig")
		known = registry(self.xml)
		self.pool = Pool.from_sources(key, include_2016)
		self.buildable = sorted(s for s in self.pool.setups if self.pool.buildable(s, known))

	def badges(self, setup: str) -> list[str]:
		return sorted(part for part in self.pool.parts(setup) if part.startswith("Pr_"))

	def build(self, setups: list[str], per_series: int = 3, ns_data_id: int = 0x5C0, serial: int | None = None) -> bytes:
		"""A data_v131 container with these setups, scheduled per_series per series each day."""
		unknown = [s for s in setups if s not in self.buildable]
		if unknown:
			raise ValueError(f"Not buildable: {', '.join(unknown)}")
		week = self.week
		# The base week's files that no machine references (all 119 categories, the invalid
		# icon) are always needed; then each chosen setup and everything it references
		base_setups = Pool.from_files(week)
		base_referenced = {base_setups.by_stem[part] for setup in base_setups.setups for part in base_setups.parts(setup)}
		files = {name: content for name, content in week.items()
			if name.startswith("pc/rt/") and name not in base_referenced}
		if sum(1 for name in files if name.startswith("pc/rt/Ca/")) != sum(1 for name in week if name.startswith("pc/rt/Ca/")):
			raise ValueError("category files missing from the base week's shared files")
		for setup in setups:
			files[f"pc/ci/{setup}.cib.szs"] = self.pool.setups[setup]
			for part in self.pool.parts(setup):
				path = self.pool.by_stem[part]
				files[path] = self.pool.resources[path]

		xml = self.xml
		fixed = sorted({stem(n) for n in files if n.startswith("pc/rt/FO/")})
		attachments = sorted({stem(n) for n in files if n.startswith("pc/rt/At/")})
		xml = replace_list(xml, "FixedObjects", "FixedObject", fixed)
		xml = replace_list(xml, "Attachments", "Attachment", attachments)
		files["pc/PrizeCollection.xml"] = BOM + xml.encode("utf-8")

		# The schedule decides which machines are on the floor each day
		top = dict(self.top)
		schedule = top["Schedule.xml"].decode("utf-8")
		days = schedule_days(schedule)
		lineup = daily_lineup(setups, len(days), per_series)
		top["Schedule.xml"] = write_schedule(schedule, stem(self.week_name), lineup, days).encode("utf-8")

		top[self.week_name] = sarc_write(files, 0x80)
		payload = sarc_write(top, 0x80)
		out = build_container(self.base_raw, self.base_body, self.key, payload, ns_data_id, serial or int(time.time()))

		# Read it back the way the console will
		_, check = open_container(out, self.key)
		assert check == payload
		check_week = sarc_read(sarc_read(check)[self.week_name])
		assert all(check_week[name] == content for name, content in files.items())
		return out


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--series", help="comma-separated series (e.g. MH,Rockman); see --list")
	parser.add_argument("--list", action="store_true", help="show buildable setups per series and exit")
	parser.add_argument("--include-2016", action="store_true",
		help="also use machines only found in the 2016 allbadge file (untested on the console)")
	parser.add_argument("--per-series", type=int, default=3, help="machines per series each day; 0 = every machine every day (default 3, like Nintendo's weeks)")
	parser.add_argument("--out", type=Path, default=HERE / "out/data_v131-custom.boss")
	parser.add_argument("--ns-data-id", type=lambda v: int(v, 0), default=0x5C0)
	parser.add_argument("--serial", type=lambda v: int(v, 0), default=None, help="default: current Unix time")
	parser.add_argument("--boot9", type=Path, default=HERE / "boot9.bin")
	args = parser.parse_args()

	print("Reading archived machines...")
	builder = Builder(key_from_boot9(args.boot9), args.include_2016)
	if args.list or not args.series:
		counts = Counter(series(s) for s in builder.buildable)
		print(f"{len(builder.buildable)} buildable setups of {len(builder.pool.setups)}:")
		for name, count in counts.most_common():
			print(f"  {name:12} {count}")
		return 0

	wanted = [s.strip() for s in args.series.split(",") if s.strip()]
	chosen = [s for w in wanted for s in builder.buildable if series(s) == w]
	missing = [s for s in wanted if not any(series(c) == s for c in chosen)]
	if missing:
		print(f"No buildable setups for: {', '.join(missing)}")
		return 1

	out = builder.build(chosen, args.per_series, args.ns_data_id, args.serial)
	args.out.parent.mkdir(parents=True, exist_ok=True)
	args.out.write_bytes(out)
	per_series = Counter(series(s) for s in chosen)
	print(f"{args.out}: {len(chosen)} machine setups ({', '.join(f'{k} {v}' for k, v in per_series.items())}), "
		f"{len(out) / 1e6:.1f} MB, week {builder.week_name}, NsData ID {args.ns_data_id:#x}")
	return 0


if __name__ == "__main__":
	try:
		sys.exit(main())
	except ValueError as e:  # a bad boot9.bin, key or input: no traceback
		sys.exit(f"error: {e}")

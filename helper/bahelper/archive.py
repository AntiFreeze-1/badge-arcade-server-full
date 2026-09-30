"""Every Nintendo asset in the archived SpotPass files, indexed.

Sources are the weekly machine sets (other/data_v131*) and the all-badges file
(other/allbadge_v131.dat.boss). When a file appears in several, the first weekly copy
wins, so badges come with their machine textures when any week has them; the all-badges
file only has the Home Menu picture of retired badges (the helper can rebuild the rest).

Adapted from badge-arcade-server's custom_week.Pool / Builder.
"""

import hashlib
import pickle
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import boss, formats as f, sarc, yaz0

BASE_WEEK_NAME = "data_v131-2022-12-29-09-40-NA.enc"
LIVE_NAMES = {"data_v131.dat.boss"}      # what's being served: may hold custom content
ALLBADGE_NAME = "allbadge_v131.dat.boss"
ALLBADGE_2016 = "allbadge_v130.dat.boss"   # game version 1.3.0's: some badges, backgrounds, machines only here
PRISTINE_ALLBADGE = "allbadge_v131-nintendo.enc"
EXTRA_SUFFIXES = {".boss", ".enc", ".dat", ".bin", ""}   # Nintendo's, kept when the helper adds yours
CACHE_VERSION = 4

FOLDERS = {"prb": "pc/rt/Pr", "cib": "pc/ci", "cab": "pc/rt/Ca", "icb": "pc/rt/CI", "crb": "pc/rt/Cr",
	"fob": "pc/rt/FO", "atb": "pc/rt/At"}


def stem(path: str) -> str:
	return path.rsplit("/", 1)[-1].split(".")[0]


def series(machine: str) -> str:
	"""Animal_055 -> Animal."""
	return machine.rsplit("_", 1)[0]


# Internal series codes -> names (from badge-arcade-server's manager.py)
SERIES_NAMES = {
	"Amiibo": "amiibo", "Animal": "Animal Crossing", "AnimalItem": "Animal Crossing (items)",
	"KirbyKey": "Kirby (key chains)", "MroGn2D": "Mario (2D)", "MroMvsD": "Mario vs. Donkey Kong", "ZelMuju": "Zelda: Majora's Mask",
	"HappyHome": "Animal Crossing: Happy Home Designer", "Hakoboy": "BoxBoy!", "Pushmo": "Pushmo", "AnimalFes": "Animal Crossing: amiibo Festival",
	"BTCenter": "BTCenter", "CatMario": "Cat Mario", "DotHard": "Retro consoles (pixel art)",
	"Emblem": "Fire Emblem", "FcRemix": "NES Remix", "Kirby": "Kirby", "MH": "Monster Hunter",
	"MdWario": "WarioWare", "MroKrt8": "Mario Kart 8", "MroMkr": "Super Mario Maker",
	"MroPrt": "Mario Party", "MroRPGMix": "Mario RPGs", "MroSMB": "Super Mario Bros.",
	"MroTnsU": "Mario Tennis: Ultra Smash", "Nikki": "Swapnote (Nikki)", "Pkm": "Pikmin",
	"PokeDot": "Pokémon (pixel art)", "PokeExt": "Pokémon (extra)", "Pokemon": "Pokémon",
	"Rhythm": "Rhythm Heaven", "Rockman": "Mega Man", "SFZero": "Star Fox Zero", "Spltn": "Splatoon",
	"Tank": "Tank Troopers", "Tomodachi": "Tomodachi Life", "YshWW": "Yoshi's Woolly World",
	"Yokai": "Yo-kai Watch", "ZelBoW": "Zelda: Breath of the Wild", "ZelDisk": "Zelda (Famicom Disk)",
	"ZelHero": "Zelda: heroes", "ZelTPri": "Zelda: Twilight Princess", "ZelTri": "Zelda: Tri Force Heroes",
	"ZelWndHD": "Zelda: Wind Waker HD", "Zelda30th": "Zelda 30th Anniversary",
}


def series_name(code: str) -> str:
	return SERIES_NAMES.get(code, code)


def badge_label(name: str) -> str:
	"""Pr_MH_Chara_Rathalos00 -> "Chara Rathalos00"."""
	parts = name.split("_")[2:] or name.split("_")[1:]
	return " ".join(p for p in parts if p != "Sep")


@dataclass
class BadgeInfo:
	name: str
	id: int
	category: str
	title: str
	width: int
	height: int
	playable: bool      # has machine textures (else they get rebuilt from the Home Menu picture)


def schedule_floor_sizes(schedule: str) -> list[int]:
	"""How many machines each day of a week's Schedule.xml puts on the floor."""
	days = Counter(re.findall(r"<DateStartText>(\d{8})</DateStartText>(?:(?!</FileItem>).)*"
		r"<RegexSetName>DefaultStage</RegexSetName>", schedule, re.S))
	return list(days.values())


class Archive:
	"""Reading everything takes a few seconds the first time; the index is cached."""

	def __init__(self, other_dir: Path, key: bytes, cache_dir: Path | None = None, progress=None,
			extra_dirs: list[Path] = ()):
		self.other_dir = Path(other_dir)
		self.key = key
		progress = progress or (lambda message: None)
		base = self.other_dir / BASE_WEEK_NAME
		if not base.exists():
			raise FileNotFoundError(f"{base} is missing: the helper builds weeks on top of Nintendo's Dec 29, 2022 week")
		self.sources = [p for p in sorted(self.other_dir.glob("data_v131*"))
			if p.name not in LIVE_NAMES and not p.name.endswith(".tmp")]
		allbadge = self.other_dir / PRISTINE_ALLBADGE
		if not allbadge.exists():
			allbadge = self.other_dir / ALLBADGE_NAME
		if allbadge.exists():
			self.sources.append(allbadge)
		if (self.other_dir / ALLBADGE_2016).exists():
			self.sources.append(self.other_dir / ALLBADGE_2016)  # only what the others don't have
		# any other SpotPass files (other weeks, other regions): whatever they add
		for folder in extra_dirs:
			self.sources += sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() in EXTRA_SUFFIXES)

		self.files: dict[str, bytes] = {}    # SARC path -> Yaz0 data
		self.by_stem: dict[str, str] = {}
		self.floor_sizes: list[int] = []
		readable = []
		for i, path in enumerate(self.sources):
			progress(f"Reading {path.name} ({i + 1}/{len(self.sources)})...")
			try:
				top = sarc.sarc_read(boss.open_container(path.read_bytes(), key)[1])
			except (ValueError, IndexError, struct.error, OSError):
				continue  # not a SpotPass file with badges in it (a letter, playinfo, ...)
			readable.append(path)
			if "Schedule.xml" in top:
				self.floor_sizes += schedule_floor_sizes(top["Schedule.xml"].decode("utf-8", "replace"))
			weeks = [name for name in top if name.startswith("sharc/")]
			for name, content in (sarc.sarc_read(top[weeks[0]]) if weeks else top).items():
				if name.startswith(("pc/rt/", "pc/ci/")) and name not in self.files:
					self.files[name] = content
					self.by_stem.setdefault(stem(name), name)

		self.sources = readable
		self._cache: dict[str, bytes] = {}
		cache_key = hashlib.sha1(repr([(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in self.sources]
			+ [CACHE_VERSION]).encode()).hexdigest()
		cache_file = cache_dir / f"archive-{cache_key[:16]}.pickle" if cache_dir else None
		index = None
		if cache_file and cache_file.exists():
			try:
				index = pickle.loads(cache_file.read_bytes())
			except Exception:
				index = None
		if index is None:
			index = self._build_index(progress)
			if cache_file:
				cache_dir.mkdir(parents=True, exist_ok=True)
				for old in cache_dir.glob("archive-*.pickle"):
					old.unlink(missing_ok=True)
				cache_file.write_bytes(pickle.dumps(index))
		self.badges: dict[str, BadgeInfo] = index["badges"]
		self.machines: dict[str, f.Machine] = index["machines"]
		self.categories: dict[str, f.Category] = index["categories"]
		self.ids = index["ids"]
		progress(f"{len(self.badges)} badges, {len(self.machines)} machines, {len(self.cabinets())} cabinets.")

	def _build_index(self, progress) -> dict:
		badges, machines, categories = {}, {}, {}
		ids = {"badge": set(), "machine": set(), "category": set()}
		names = [n for n in self.files if n.endswith(".prb.szs")]
		for i, name in enumerate(names):
			if i % 250 == 0:
				progress(f"Indexing badges ({i}/{len(names)})...")
			head = yaz0.decompress(self.files[name], 0x1100)
			offs = struct.unpack_from("<14I", head, 4)
			width, height = struct.unpack_from("<II", head, 0xB8)
			info = BadgeInfo(f.read_name(head, 0x44), struct.unpack_from("<I", head, 0x3C)[0], f.read_name(head, 0x74),
				f.read_titles(head, 0xE0)[1] or f.read_titles(head, 0xE0)[0], width, height, offs[11] > offs[10])
			badges[info.name] = info
			ids["badge"].add(info.id)
		progress("Indexing machines...")
		for name in (n for n in self.files if n.endswith(".cib.szs")):
			m = f.Machine.parse(yaz0.decompress(self.files[name]))
			machines[m.name] = m
			ids["machine"].add(m.id)
		for name in (n for n in self.files if n.endswith(".cab.szs")):
			c = f.Category.parse(yaz0.decompress(self.files[name]))
			categories[c.name] = c
			ids["category"].add(c.id)
			ids["category"].add(struct.unpack_from("<I", c.numbers, 8)[0])
		return {"badges": badges, "machines": machines, "categories": categories, "ids": ids}

	# ----- raw files -----

	def has(self, name: str) -> bool:
		return name in self.by_stem

	def path(self, name: str) -> str:
		return self.by_stem[name]

	def compressed(self, name: str) -> bytes:
		return self.files[self.by_stem[name]]

	def raw(self, name: str) -> bytes:
		if name not in self._cache:
			if len(self._cache) > 400:
				self._cache.clear()
			self._cache[name] = yaz0.decompress(self.compressed(name))
		return self._cache[name]

	def names(self, kind: str) -> list[str]:
		folder = FOLDERS[kind] + "/"
		return sorted(stem(n) for n in self.files if n.startswith(folder))

	def cabinets(self) -> list[str]:
		return [n for n in self.names("crb") if n != "CrSt_Invalid_Invalid"]

	def icons(self) -> list[str]:
		return self.names("icb")

	# ----- parsed -----

	def badge(self, name: str) -> f.Badge:
		return f.Badge.parse(self.raw(name))

	def badge_image(self, name: str) -> np.ndarray:
		"""The Home Menu picture (whole badge), RGBA."""
		info = self.badges[name]
		if (info.width, info.height) == (1, 1):
			return f.decode_badge_image(yaz0.decompress(self.compressed(name), 0x4300)[0x1100:0x4300])
		return self.badge(name).full_rgba()

	def badge_thumbnail(self, name: str) -> np.ndarray:
		"""64x64 RGBA."""
		return f.decode_badge_image(yaz0.decompress(self.compressed(name), 0x4300)[0x1100:0x4300])

	def machine(self, name: str) -> f.Machine:
		return f.Machine.parse(self.raw(name))  # a fresh copy to edit

	def cabinet(self, name: str) -> f.Cabinet:
		return f.Cabinet.parse(self.raw(name))

	def icon(self, name: str) -> f.Icon:
		return f.Icon.parse(self.raw(name))

	def object_texture(self, name: str) -> np.ndarray:
		return f.object_texture(self.raw(name))

	def object_shapes(self, name: str):
		"""((texture w, h), collision polygons) of a fixed object or attachment."""
		return f.object_shapes(self.raw(name))

	def obstacles(self) -> dict[str, list[str]]:
		"""Fixed objects and attachments that some machine uses, by kind."""
		self._obstacle_templates()
		return {"fixed": sorted(self._templates["fixed"]), "attachment": sorted(self._templates["attachment"])}

	def obstacle_template(self, kind: str, name: str) -> f.Placement:
		"""How Nintendo placed this object somewhere (its size and physics values)."""
		return self._obstacle_templates()[kind][name].copy()

	def _obstacle_templates(self) -> dict:
		if getattr(self, "_templates", None) is None:
			templates = {"fixed": {}, "attachment": {}}
			for m in self.machines.values():
				for kind, names, places in (("fixed", m.fixed, m.fixed_placements),
						("attachment", m.attachments, m.attachment_placements)):
					for p in places:
						if p.index < len(names) and self.has(names[p.index]):
							templates[kind].setdefault(names[p.index], p)
			self._templates = templates
		return self._templates

	def machine_parts(self, machine: f.Machine) -> list[str]:
		return [machine.crane, machine.icon] + machine.prizes + machine.attachments + machine.fixed

	def missing_parts(self, machine: f.Machine) -> list[str]:
		return [p for p in self.machine_parts(machine) if not self.has(p)]

	def template_machine(self, name: str) -> tuple[f.Machine, list[str]] | None:
		"""A machine to start a custom one from, with any parts the archive doesn't have left
		out (a stand-in background and icon if those are missing). None if none of its badges
		are left."""
		m = self.machine(name)
		missing = set(self.missing_parts(m))
		notes = []
		for i in reversed(range(len(m.prize_placements))):
			if m.prizes[m.prize_placements[i].index] in missing:
				m.remove_prize_placement(i)
		if not m.prize_placements:
			return None
		for i in reversed(range(len(m.attachment_placements))):
			if m.attachments[m.attachment_placements[i].index] in missing:
				m.remove_attachment_placement(i)
		m.fixed_placements = [p for p in m.fixed_placements if m.fixed[p.index] not in missing]
		m.prune_names()
		m.prune_obstacles()
		if m.crane in missing:
			notes.append(f"its background ({m.crane}) isn't in the archive, so it has a stand-in")
			m.crane = self.cabinets()[0]
		if m.icon in missing:
			m.icon = self.icons()[0]
		lost = sorted(p for p in missing if p.startswith(("Pr_", "FxOb_", "At_")))
		if lost:
			notes.append(f"{len(lost)} of its parts aren't in the archive and were left out: " + ", ".join(lost[:6])
				+ (" ..." if len(lost) > 6 else ""))
		return m, notes

	def template_machines(self) -> dict[str, int]:
		"""Every machine that can start a custom one, with how many of its parts are missing."""
		out = {}
		for name, m in self.machines.items():
			if not self.has(name):
				continue
			missing = set(self.missing_parts(m))
			if any(p not in missing for p in m.prizes):
				out[name] = len(missing)
		return dict(sorted(out.items()))

	def buildable_machines(self) -> list[str]:
		"""Nintendo machines whose every part is in the archive."""
		return sorted(n for n, m in self.machines.items() if self.has(n) and not self.missing_parts(m))

	def machine_badges(self, name: str) -> list[str]:
		return sorted(set(self.machines[name].prizes))

	def missing_series(self) -> list[tuple[str, int, list[str]]]:
		"""Books (series) whose categories exist but none of whose badges are in the sources:
		(title, badges Nintendo counted, category names)."""
		have = {info.category for info in self.badges.values()}
		books: dict[int, list] = {}
		for c in self.categories.values():
			books.setdefault(struct.unpack_from("<I", c.numbers, 8)[0], []).append(c)
		out = []
		for book, cats in sorted(books.items()):
			if any(c.name in have for c in cats):
				continue
			title = next((c.titles[1] or c.titles[0] for c in cats if c.titles[0]), cats[0].name).replace("\n", " ")
			out.append((title, struct.unpack_from("<I", cats[0].numbers, 0)[0], sorted(c.name for c in cats)))
		return out

	def badges_by_series(self) -> dict[str, list[str]]:
		out: dict[str, list[str]] = {}
		for name, info in self.badges.items():
			code = re.sub(r"\d+$", "", info.category) or "Other"
			out.setdefault(code, []).append(name)
		return {k: sorted(v) for k, v in sorted(out.items(), key=lambda kv: series_name(kv[0]).lower())}

	def max_per_day(self, days: int, slot_limit: int) -> int:
		nintendo = max(self.floor_sizes, default=0)
		return min(nintendo or slot_limit, slot_limit // days)

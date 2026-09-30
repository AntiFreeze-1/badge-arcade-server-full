"""Your own content, kept in workspace/ as JSON and PNG files.

  workspace/workspace.json      ID counters
  workspace/sets/<code>.json    badge sets (a category and its book in the badge collection)
  workspace/badges/<slug>.json  custom badges, with <slug>.png (the picture)
  workspace/machines/<name>.json  custom machines
  workspace/weeks/<slug>.json   weeks (which machines to put on the floor)
  workspace/images/             pictures for machine icons and cabinet backgrounds
  workspace/cache/              built badges (rebuilt when their picture or settings change)

Every asset gets its IDs once, when it's created, so rebuilding a week never changes
what the 3DS has already saved.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import formats as f

# Machine IDs are one counter across all of Nintendo's machines, in the order they came out
# (133 to 4,498 in the archive); the game's collection seems to track sets by them, so ours
# carry on from just past Nintendo's. Badge and category IDs sit in Nintendo's ranges too.
FIRST_IDS = {"badge": 90_000_000, "machine": 4_600, "category": 7_000}
MACHINE_ID_LIMIT = 10_000   # older helper versions gave machines 60,000 and up: moved on start
NAME_MAX = f.NAME_BYTES - 1
# Nintendo's longest names are 11 characters for a category (AnimalItem2), 12 for a book
# (KirbyKeyBook) and 13 for a machine (AnimalFes_000). A machine called CuRetroConsole_000
# (18) wouldn't load while CuPortal2_000 (13) did, so codes are kept short enough for every
# name to fit in 15: Cu + 9 + Book, Cu + 9 + _000.
SET_CODE_MAX = 9
MACHINE_NAME_MAX = 15


def slugify(text: str, fallback: str = "item") -> str:
	slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
	return slug or fallback


def code_for(text: str) -> str:
	"""A set code from a title: "Portal 2!" -> "Portal2"."""
	words = re.findall(r"[A-Za-z0-9]+", text)
	code = "".join(w[:1].upper() + w[1:] for w in words)[:SET_CODE_MAX]
	return code or "Custom"


def _hexify(value):
	if isinstance(value, bytes):
		return {"hex": value.hex()}
	if isinstance(value, list):
		return [_hexify(v) for v in value]
	if isinstance(value, dict):
		return {k: _hexify(v) for k, v in value.items()}
	if isinstance(value, tuple):
		return [_hexify(v) for v in value]
	return value


def _unhex(value):
	if isinstance(value, dict) and set(value) == {"hex"}:
		return bytes.fromhex(value["hex"])
	if isinstance(value, list):
		return [_unhex(v) for v in value]
	if isinstance(value, dict):
		return {k: _unhex(v) for k, v in value.items()}
	return value


def machine_to_json(machine: f.Machine) -> dict:
	return _hexify(asdict(machine))


def machine_from_json(data: dict) -> f.Machine:
	d = _unhex(data)
	d["prize_placements"] = [f.Placement(**p) for p in d["prize_placements"]]
	d["catalog"] = [f.Placement(**p) for p in d["catalog"]]
	d["attachment_placements"] = [f.Placement(**p) for p in d["attachment_placements"]]
	d["fixed_placements"] = [f.Placement(**p) for p in d["fixed_placements"]]
	d["links"] = [f.Link(**l) for l in d["links"]]
	return f.Machine(**d)


@dataclass
class BadgeSet:
	"""A custom category: its own page in the badge collection (with its own book)."""
	code: str
	title: str
	category_id: int
	book_id: int
	icon_badge: str = ""       # badge whose picture is the set's icon (default: the first)

	@property
	def category(self) -> str:
		return f"Cu{self.code}"

	@property
	def book(self) -> str:
		return f"Cu{self.code}Book"


@dataclass
class CustomBadge:
	name: str                  # Pr_Cu<code>_<slug>
	set: str                   # BadgeSet.code
	title: str
	id: int
	image: str                 # file in workspace/badges
	pixel_art: bool | None = None   # None: guess
	simple_shape: bool = False
	tiles: list[int] | None = None  # [width, height] in 64-px tiles; None: from the picture
	launch_title: str = ""     # the program it opens on the HOME Menu (16 hex digits); "" = none


@dataclass
class CustomMachine:
	name: str                  # Cu<series>_NNN
	title: str
	template: str              # the Nintendo machine it started from
	machine: dict              # machine_to_json(...)
	cabinet_image: str = ""    # a custom background picture (in images/), else machine.crane is used
	cabinet_trim: str = ""     # the Nintendo cabinet whose trim a custom background gets
	icon_mode: str = "auto"    # "auto" (collage of its badges), "custom" (icon_image) or "nintendo" (machine.icon)
	icon_image: str = ""
	icon_background: list[int] = field(default_factory=lambda: [250, 220, 120])
	# the "auto" icon's badges, as {"badge", "x", "y", "size"} in icon pixels (see makers.collage);
	# empty: the machine's first four badges in a grid
	icon_layout: list[dict] = field(default_factory=list)

	def load(self) -> f.Machine:
		return machine_from_json(self.machine)

	def store(self, machine: f.Machine) -> None:
		self.machine = machine_to_json(machine)

	@property
	def cabinet_name(self) -> str:
		return f"CrSt_{self.name}" if self.cabinet_image else self.load().crane

	@property
	def icon_name(self) -> str:
		return f"CraneIcon_{self.name}" if self.icon_mode != "nintendo" else self.load().icon

	def collage(self, machine: f.Machine) -> tuple[list[str], list[tuple[float, float, float]] | None]:
		"""The badges of the icon made from the machine's badges, and where they go (None: the
		grid). Arranged badges the machine no longer has are left out."""
		chosen = [item for item in self.icon_layout if item.get("badge") in machine.prizes][:4]
		if chosen:
			return [item["badge"] for item in chosen], [(item["x"], item["y"], item["size"]) for item in chosen]
		return machine.prizes[:4], None


@dataclass
class WeekPlan:
	name: str
	nintendo: list[str] = field(default_factory=list)
	custom: list[str] = field(default_factory=list)
	per_series: int = 3        # machines per series each day; 0 = every machine every day
	extras: dict = field(default_factory=dict)  # hall pictures, Bunny's lines, gallery posts (extras.py)


class Workspace:
	def __init__(self, root: Path):
		self.root = Path(root)
		for sub in ("sets", "badges", "machines", "weeks", "images", "cache"):
			(self.root / sub).mkdir(parents=True, exist_ok=True)
		self.state_file = self.root / "workspace.json"
		self.state = self._read(self.state_file, {"next": dict(FIRST_IDS)})

	# ----- files -----

	@staticmethod
	def _read(path: Path, default=None):
		try:
			return json.loads(path.read_text(encoding="utf-8"))
		except FileNotFoundError:
			return default

	@staticmethod
	def _write(path: Path, data) -> None:
		tmp = path.with_suffix(path.suffix + ".tmp")
		tmp.write_text(json.dumps(data, indent="\t", ensure_ascii=False) + "\n", encoding="utf-8")
		tmp.replace(path)

	def _save_state(self) -> None:
		self._write(self.state_file, self.state)

	def allocate_id(self, kind: str, taken: set[int] = frozenset(), count: int = 1) -> int:
		"""A run of `count` unused IDs (not Nintendo's, not already given out)."""
		used = self.used_ids(kind) | set(taken)
		start = self.state["next"].get(kind, FIRST_IDS[kind])
		while any(start + i in used for i in range(count)):
			start += 1
		self.state["next"][kind] = start + count
		self._save_state()
		return start

	def renumber_machines(self, taken: set[int]) -> list[str]:
		"""Gives machines made by older versions (IDs of 60,000 and up) IDs past Nintendo's."""
		moved = []
		if self.state["next"].get("machine", 0) >= MACHINE_ID_LIMIT:
			self.state["next"]["machine"] = FIRST_IDS["machine"]
			self._save_state()
		for cm in self.machines():
			m = cm.load()
			if m.id >= MACHINE_ID_LIMIT or m.id in taken:
				m.id = self.allocate_id("machine", taken)
				cm.store(m)
				self.save_machine(cm)
				moved.append(cm.title or cm.name)
		return moved

	def set_of_machine(self, cm: CustomMachine) -> "BadgeSet | None":
		"""The set most of a machine's custom badges belong to (None if it has none of yours)."""
		counts: dict[str, int] = {}
		for name in cm.load().prizes:
			badge = self.get_badge(name)
			if badge:
				counts[badge.set] = counts.get(badge.set, 0) + 1
		return self.get_set(max(counts, key=counts.get)) if counts else None

	def rename_machine(self, old: str, new: str) -> None:
		"""Renames a machine, and changes it in the weeks that have it."""
		cm = self.get_machine(old)
		m = cm.load()
		m.name = new
		cm.name = new
		cm.store(m)
		self.save_machine(cm)
		(self.root / "machines" / f"{old}.json").unlink(missing_ok=True)
		for week in self.weeks():
			if old in week.custom:
				week.custom = [new if n == old else n for n in week.custom]
				self.save_week(week)

	def shorten_names(self) -> list[tuple[str, str]]:
		"""Gives sets with long codes (older versions allowed 16 characters) a short one, and
		their badges the names that go with it (Pr_<category>_..., like Nintendo's). Machines
		follow with align_machine_names. Returns (old, new) set categories."""
		changed = []
		for badge_set in self.sets():
			if len(badge_set.code) <= SET_CODE_MAX:
				continue
			old_category = badge_set.category
			base, code, n = badge_set.code[:SET_CODE_MAX], badge_set.code[:SET_CODE_MAX], 2
			while self.get_set(code):
				code = f"{base[:SET_CODE_MAX - len(str(n))]}{n}"
				n += 1
			(self.root / "sets" / f"{badge_set.code}.json").unlink(missing_ok=True)
			members = [b for b in self.badges() if b.set == badge_set.code]
			badge_set.code = code
			self.save_set(badge_set)
			for badge in members:
				badge.set = code
				self.save_badge(badge)
				self.align_badge_name(badge)
			changed.append((old_category, badge_set.category))
		return changed

	def align_badge_name(self, badge: CustomBadge) -> str:
		"""Renames a badge to Pr_<its set's category>_<label> if it isn't (after its set was
		renamed), in the machines that have it too. Returns its name."""
		badge_set = self.get_set(badge.set)
		if badge_set is None or badge.name.startswith(f"Pr_{badge_set.category}_"):
			return badge.name
		label = badge.name.split("_", 2)[-1] if badge.name.startswith("Pr_Cu") else badge.name
		prefix = f"Pr_{badge_set.category}_"
		slug = label[:NAME_MAX - len(prefix) - 3]
		new, n = prefix + slug, 2
		while self.get_badge(new):
			new = f"{prefix}{slug}{n}"
			n += 1
		old = badge.name
		picture = self.badge_picture(badge)
		image = f"{new}{picture.suffix}"
		if picture.exists():
			picture.replace(self.root / "badges" / image)
		self.badge_file(old).unlink(missing_ok=True)
		badge.name, badge.image = new, image
		self.save_badge(badge)
		for other in self.sets():
			if other.icon_badge == old:
				other.icon_badge = new
				self.save_set(other)
		for cm in self.machines():
			m = cm.load()
			if old in m.prizes or any(item.get("badge") == old for item in cm.icon_layout):
				m.prizes = [new if p == old else p for p in m.prizes]
				cm.store(m)
				for item in cm.icon_layout:
					if item.get("badge") == old:
						item["badge"] = new
				self.save_machine(cm)
		return new

	def align_machine_names(self) -> list[tuple[str, str]]:
		"""Names each machine after its set, the way Nintendo's are named after their book
		(Amiibo_000 ... in AmiiboBook, Pokemon_000 ... in PokemonBook): CuPortal_000 in CuPortalBook.
		Returns (old, new) names."""
		renamed = []
		for cm in self.machines():
			badge_set = self.set_of_machine(cm)
			if badge_set is None:
				if len(cm.name) <= MACHINE_NAME_MAX:
					continue
				new = self.new_machine_name(cm.name.rsplit("_", 1)[0].removeprefix("Cu"))
			elif cm.name.rsplit("_", 1)[0] == badge_set.category and len(cm.name) <= MACHINE_NAME_MAX:
				continue
			else:
				new = self.new_machine_name(badge_set.code)
			self.rename_machine(cm.name, new)
			renamed.append((cm.name, new))
		return renamed

	def machines_using(self, badge_names: set[str]) -> list[CustomMachine]:
		"""Your machines that have any of these badges on them."""
		return [cm for cm in self.machines() if badge_names & {p for p in cm.load().prizes}]

	def used_ids(self, kind: str) -> set[int]:
		if kind == "badge":
			return {b.id for b in self.badges()}
		if kind == "machine":
			return {m.load().id for m in self.machines()}
		return {s.category_id for s in self.sets()} | {s.book_id for s in self.sets()}

	# ----- badge sets -----

	def sets(self) -> list[BadgeSet]:
		return [BadgeSet(**self._read(p)) for p in sorted((self.root / "sets").glob("*.json"))]

	def get_set(self, code: str) -> BadgeSet | None:
		data = self._read(self.root / "sets" / f"{code}.json")
		return BadgeSet(**data) if data else None

	def save_set(self, badge_set: BadgeSet) -> None:
		self._write(self.root / "sets" / f"{badge_set.code}.json", asdict(badge_set))

	def create_set(self, title: str, taken_ids: set[int] = frozenset(), code: str | None = None) -> BadgeSet:
		code = (code or code_for(title))[:SET_CODE_MAX]
		base, n = code, 2
		while self.get_set(code):
			code = f"{base[:SET_CODE_MAX - len(str(n))]}{n}"
			n += 1
		book = self.allocate_id("category", taken_ids, count=2)
		badge_set = BadgeSet(code, title, book + 1, book)
		self.save_set(badge_set)
		return badge_set

	def delete_set(self, code: str) -> None:
		if any(b.set == code for b in self.badges()):
			raise ValueError("Delete or move the set's badges first")
		(self.root / "sets" / f"{code}.json").unlink(missing_ok=True)

	# ----- badges -----

	def badges(self) -> list[CustomBadge]:
		return [CustomBadge(**self._read(p)) for p in sorted((self.root / "badges").glob("*.json"))]

	def badge_file(self, name: str) -> Path:
		return self.root / "badges" / f"{name}.json"

	def get_badge(self, name: str) -> CustomBadge | None:
		data = self._read(self.badge_file(name))
		return CustomBadge(**data) if data else None

	def save_badge(self, badge: CustomBadge) -> None:
		self._write(self.badge_file(badge.name), asdict(badge))

	def badge_picture(self, badge: CustomBadge) -> Path:
		return self.root / "badges" / badge.image

	def add_badge(self, picture: bytes, title: str, badge_set: BadgeSet, label: str,
			taken_ids: set[int] = frozenset()) -> CustomBadge:
		prefix = f"Pr_{badge_set.category}_"
		slug = slugify(label, "badge")[:NAME_MAX - len(prefix) - 3]
		name, n = prefix + slug, 2
		while self.get_badge(name):
			name = f"{prefix}{slug}{n}"
			n += 1
		image = f"{name}.png"
		(self.root / "badges" / image).write_bytes(picture)
		badge = CustomBadge(name, badge_set.code, title, self.allocate_id("badge", taken_ids), image)
		self.save_badge(badge)
		return badge

	def delete_badge(self, name: str) -> None:
		badge = self.get_badge(name)
		if badge:
			self.badge_picture(badge).unlink(missing_ok=True)
		self.badge_file(name).unlink(missing_ok=True)

	def badge_cache_key(self, badge: CustomBadge, extra: str = "") -> str:
		picture = self.badge_picture(badge).read_bytes()
		return hashlib.sha1(picture + json.dumps(asdict(badge), sort_keys=True).encode() + extra.encode()).hexdigest()

	def cached(self, key: str) -> bytes | None:
		path = self.root / "cache" / f"{key}.bin"
		return path.read_bytes() if path.exists() else None

	def store_cache(self, key: str, data: bytes) -> None:
		(self.root / "cache" / f"{key}.bin").write_bytes(data)

	# ----- machines -----

	def machines(self) -> list[CustomMachine]:
		return [CustomMachine(**self._read(p)) for p in sorted((self.root / "machines").glob("*.json"))]

	def get_machine(self, name: str) -> CustomMachine | None:
		data = self._read(self.root / "machines" / f"{name}.json")
		return CustomMachine(**data) if data else None

	def save_machine(self, machine: CustomMachine) -> None:
		self._write(self.root / "machines" / f"{machine.name}.json", asdict(machine))

	def new_machine_name(self, series_code: str) -> str:
		series_code = "Cu" + slugify(series_code, "Custom").replace("_", "")[:SET_CODE_MAX]
		existing = {m.name for m in self.machines()}
		n = 0
		while f"{series_code}_{n:03d}" in existing:
			n += 1
		return f"{series_code}_{n:03d}"

	def delete_machine(self, name: str) -> None:
		(self.root / "machines" / f"{name}.json").unlink(missing_ok=True)

	def add_image(self, source: Path | bytes, stem_hint: str) -> str:
		"""Copies a picture into images/; returns its file name."""
		data = source if isinstance(source, bytes) else Path(source).read_bytes()
		name = f"{slugify(stem_hint)}_{hashlib.sha1(data).hexdigest()[:8]}.png"
		from PIL import Image
		import io
		Image.open(io.BytesIO(data)).convert("RGBA").save(self.root / "images" / name)
		return name

	def image_path(self, name: str) -> Path:
		return self.root / "images" / name

	# ----- weeks -----

	def weeks(self) -> list[WeekPlan]:
		return [WeekPlan(**self._read(p)) for p in sorted((self.root / "weeks").glob("*.json"))]

	def save_week(self, week: WeekPlan) -> Path:
		path = self.root / "weeks" / f"{slugify(week.name, 'week')}.json"
		self._write(path, asdict(week))
		return path

	def delete_week(self, name: str) -> None:
		(self.root / "weeks" / f"{slugify(name, 'week')}.json").unlink(missing_ok=True)

	def export_set(self, code: str, target: Path) -> None:
		"""A zip of a set's pictures, e.g. to share it."""
		import zipfile
		with zipfile.ZipFile(target, "w") as z:
			for badge in self.badges():
				if badge.set == code:
					z.write(self.badge_picture(badge), f"{badge.name.split('_', 2)[-1]}.png")

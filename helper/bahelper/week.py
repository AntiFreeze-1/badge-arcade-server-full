"""Building a week (data_v131): Nintendo's Dec 29, 2022 week with its machines replaced by
your choice of Nintendo machines and custom machines.

The schedule code (daily line-ups, the 999-machine limit) and redating are adapted from
badge-arcade-server's custom_week.py and serve.py.
"""

import datetime
import math
import re
import time

from PIL import Image

from . import boss, extras, formats as f, makers, sarc, yaz0
from .archive import Archive, BASE_WEEK_NAME, series
from .workspace import MACHINE_NAME_MAX, CustomBadge, CustomMachine, WeekPlan, Workspace

BOM = bytes.fromhex("efbbbf")
PLAYABLE_VERSION = "3"   # bump when the rebuilt-badge recipe changes (invalidates the cache)

# ----- schedule (from custom_week.py) -----

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

# The schedule names each day's machines DefaultStageName000, 001, ... through the week
# (three digits). A week with more than 1000 of them makes Badge Arcade crash.
SLOT_LIMIT = 1000
SLOT_KEY = re.compile(r"<Key>DefaultStageName(\d+)</Key>")


def schedule_days(xml: str) -> list[datetime.date]:
	"""The days of the week, from the PrizeCollection entry's dates."""
	item = re.search(r"<FileItem>(?:(?!</FileItem>).)*<RegexSetName>PrizeCollection</RegexSetName>.*?</FileItem>", xml, re.S)
	start = datetime.datetime.strptime(re.search(r"<DateStartText>(\d{8})<", item.group(0)).group(1), "%Y%m%d").date()
	end = datetime.datetime.strptime(re.search(r"<DateExpireText>(\d{8})<", item.group(0)).group(1), "%Y%m%d").date()
	return [start + datetime.timedelta(days=i) for i in range((end - start).days)]


def schedule_problem(schedule: str) -> str | None:
	"""Why Badge Arcade can't use this schedule (None if it can)."""
	slots = [int(n) for n in SLOT_KEY.findall(schedule)]
	if slots and max(slots) >= SLOT_LIMIT:
		return f"it puts {len(slots)} machines on the floor over the week, and the game crashes past {SLOT_LIMIT}"
	return None


def daily_lineup(setups: list[str], days: int, per_series: int, max_per_day: int | None = None) -> list[list[str]]:
	"""Up to per_series setups of every series each day, rotating through each series.
	per_series 0 puts every setup on the floor every day. With max_per_day, a day with
	more than that shows a different part of its line-up each day."""
	by_series: dict[str, list[str]] = {}
	for setup in setups:
		by_series.setdefault(series(setup), []).append(setup)
	lineup = []
	for day in range(days):
		today = []
		for members in by_series.values():
			count = len(members) if per_series <= 0 else min(per_series, len(members))
			today += [members[(day * count + i) % len(members)] for i in range(count)]
		if max_per_day and len(today) > max_per_day:
			start = day * max_per_day % len(today)
			today = [today[(start + i) % len(today)] for i in range(max_per_day)]
		lineup.append(today)
	return lineup


def write_schedule(xml: str, week: str, lineup: list[list[str]], days: list[datetime.date]) -> str:
	"""Replaces the daily machine line-up (DefaultStage) and bonus machine (BonusStage)."""
	items = re.findall(r"[ \t]*<FileItem>.*?</FileItem>\r?\n", xml, re.S)
	kept = [item for item in items if not re.search(r"<RegexSetName>(DefaultStage|BonusStage)</RegexSetName>", item)]
	if sum(len(today) for today in lineup) > SLOT_LIMIT:
		raise ValueError(f"a week can put at most {SLOT_LIMIT} machines on the floor in total (the game crashes past that)")
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


def redate(top: dict[str, bytes], new_start: datetime.date) -> dict[str, bytes]:
	"""Moves a week to new dates: its schedule and the name of its machine archive
	(sharc/YYMMDD-YYMMDD.sarc) shift by the same number of days. (From serve.py.)"""
	top = dict(top)
	week_name = next(name for name in top if name.startswith("sharc/"))
	match = re.fullmatch(r"sharc/(\d{6})-(\d{6})\.sarc", week_name)
	parse = lambda text: datetime.datetime.strptime(text, "%y%m%d").date()
	old_start, old_end = parse(match.group(1)), parse(match.group(2))
	delta = new_start - old_start
	new_end = old_end + delta

	def shift(m):
		date = datetime.datetime.strptime(m.group(2), "%Y%m%d").date() + delta
		return m.group(1) + date.strftime("%Y%m%d") + m.group(3)

	schedule = top["Schedule.xml"].decode("utf-8")
	schedule = re.sub(r"(<Date(?:Start|Expire)Text>)(\d{8})(<)", shift, schedule)
	schedule = schedule.replace(f"{old_start:%y%m%d}-{old_end:%y%m%d}", f"{new_start:%y%m%d}-{new_end:%y%m%d}")
	schedule = schedule.replace(f"Boss{old_start:%Y%m%d}_{old_end:%Y%m%d}", f"Boss{new_start:%Y%m%d}_{new_end:%Y%m%d}")
	top["Schedule.xml"] = schedule.encode("utf-8")
	top[f"sharc/{new_start:%y%m%d}-{new_end:%y%m%d}.sarc"] = top.pop(week_name)
	return top


# ----- PrizeCollection.xml -----

REGISTRY = {  # block -> entry tag
	"Categories": "Category", "Cranes": "Crane", "CraneIcons": "CraneIcon", "Prizes": "Prize",
	"Attachments": "Attachment", "FixedObjects": "FixedObject", "CraneInstances": "CraneInstance",
}


def registered(xml: str, block: str) -> list[str]:
	match = re.search(rf"<{block}(?: count=\"\d+\")?>(.*?)</{block}>", xml, re.S)
	return re.findall(rf'<{REGISTRY[block]} name="([^"]+)"', match.group(1)) if match else []


def set_registry(xml: str, block: str, names: list[str]) -> str:
	"""Rewrites a block of the registry with these names (keeping its count="" if it has one)."""
	tag = REGISTRY[block]
	match = re.search(rf"( *)<{block}( count=\"\d+\")?>\r?\n.*?</{block}>", xml, re.S)
	if not match:
		raise ValueError(f"PrizeCollection.xml has no {block} list")
	indent = match.group(1)
	newline = "\r\n" if "\r\n" in match.group(0) else "\n"
	count = f' count="{len(names)}"' if match.group(2) else ""
	lines = [f"{indent}<{block}{count}>"] + [f'{indent}  <{tag} name="{name}" />' for name in names] + [f"{indent}</{block}>"]
	return xml[:match.start()] + newline.join(lines) + xml[match.end():]


def add_to_registry(xml: str, block: str, names) -> str:
	current = registered(xml, block)
	missing = [n for n in dict.fromkeys(names) if n not in set(current)]
	return set_registry(xml, block, current + missing) if missing else xml


# ----- catalogue layout -----


# What follows the position in every one of Nintendo's 6,995 catalogue entries. It differs from a
# badge placement's: among other things its flag at 0x14 is 0 (1 on badges in the machine).
CATALOG_REST = bytes.fromhex(
	"cdcc4c3e0000803f0000003f0000803f0000803f000000000000803f0000803f0000803f00000000"
	"00000000000000000000803f0000803f0000803f0000803f0000803f0000803f")


def catalog_layout(machine: f.Machine) -> list[f.Placement]:
	"""One catalogue entry per badge type (the set's display in the collection), spread over
	the screen the way Nintendo's are, with Nintendo's catalogue values."""
	count = len(machine.prizes)
	template = f.Placement(0, 0.5, 0.5, 0.0, 0.0, 0.0, CATALOG_REST)
	per_row = 5 if count > 12 else 4
	rows = math.ceil(count / per_row)
	scale = 0.5 if count <= 12 else 0.4
	out = []
	for i in range(count):
		r, c = divmod(i, per_row)
		in_row = min(per_row, count - r * per_row)
		x = 200 + (c - (in_row - 1) / 2) * (300 / max(per_row - 1, 1))
		y = 120 + (r - (rows - 1) / 2) * (180 / max(rows, 3))
		out.append(template.copy(index=i, x=round(x, 1), y=round(y, 1), scale_x=scale, scale_y=scale, rotation=0.0))
	return out


# ----- building -----


class WeekBuilder:
	def __init__(self, archive: Archive, workspace: Workspace, progress=None):
		self.archive = archive
		self.workspace = workspace
		self.progress = progress or (lambda message: None)
		self.key = archive.key
		self.base_raw = (archive.other_dir / BASE_WEEK_NAME).read_bytes()
		self.base_body, payload = boss.open_container(self.base_raw, self.key)
		self.top = sarc.sarc_read(payload)
		self.week_name = next(name for name in self.top if name.startswith("sharc/"))
		self.week = sarc.sarc_read(self.top[self.week_name])
		self.xml = self.week["pc/PrizeCollection.xml"].decode("utf-8-sig")
		schedule = self.top["Schedule.xml"].decode("utf-8")
		self.days = schedule_days(schedule)
		self.max_per_day = min(archive.max_per_day(len(self.days), SLOT_LIMIT),
			SLOT_LIMIT // len(self.days))
		self._badge_cache: dict[str, tuple[bytes, str]] = {}

	# --- badges ---

	def custom_badge_file(self, badge: CustomBadge, badge_set_category: str) -> bytes:
		"""A custom badge, Yaz0-compressed (cached in the workspace)."""
		key = self.workspace.badge_cache_key(badge, "v" + PLAYABLE_VERSION + badge_set_category)
		data = self.workspace.cached(key)
		if data is None:
			built = self.make_custom_badge(badge, badge_set_category)
			data = yaz0.compress(built.build())
			self.workspace.store_cache(key, data)
		return data

	def make_custom_badge(self, badge: CustomBadge, category: str | None = None) -> f.Badge:
		badge_set = self.workspace.get_set(badge.set)
		return makers.make_badge(self.workspace.badge_picture(badge).read_bytes(), badge_id=badge.id, name=badge.name,
			category=category or (badge_set.category if badge_set else f"Cu{badge.set}"), title=badge.title,
			tiles=tuple(badge.tiles) if badge.tiles else None, pixel_art=badge.pixel_art, simple_shape=badge.simple_shape,
			launch_title=int(badge.launch_title, 16) if badge.launch_title else None)

	def nintendo_badge_file(self, name: str) -> bytes:
		"""A Nintendo badge; retired ones get their machine parts rebuilt."""
		info = self.archive.badges[name]
		if info.playable:
			return self.archive.compressed(name)
		key = f"playable-{PLAYABLE_VERSION}-{name}"
		data = self.workspace.cached(key)
		if data is None:
			data = yaz0.compress(makers.make_playable(self.archive.badge(name)).build())
			self.workspace.store_cache(key, data)
		return data

	def cached(self, key: tuple, make) -> bytes:
		"""make() -> an uncompressed file; returned Yaz0-compressed, kept in the workspace's cache."""
		import hashlib
		digest = hashlib.sha1(repr(key).encode("utf-8", "surrogatepass")).hexdigest()
		name = f"file-{digest}"
		data = self.workspace.cached(name)
		if data is None:
			data = yaz0.compress(make())
			self.workspace.store_cache(name, data)
		return data

	def picture_identity(self, name: str) -> str:
		"""What a badge's picture depends on (for caches)."""
		badge = self.workspace.get_badge(name)
		return self.workspace.badge_cache_key(badge) if badge else name

	def set_files(self, badge_set) -> dict[str, bytes]:
		"""A badge set's category and its book (the collection page)."""
		members = [b for b in self.workspace.badges() if b.set == badge_set.code]
		if not members:
			return {}
		if len(badge_set.book) > MACHINE_NAME_MAX:
			raise ValueError(f"The set {badge_set.title}'s name ({badge_set.book}) is longer than the game takes; "
				"restart the helper to shorten it")
		icon_badge = next((b for b in members if b.name == badge_set.icon_badge), members[0])
		picture = self.workspace.badge_picture(icon_badge)
		# Nintendo's books count their badges and their sets, a set being a machine (Pokemon:
		# 175 machines, 175 sets); the collection groups badges by the machine they're in
		in_machines = set()
		machines = self.workspace.machines_using({b.name for b in members})
		for cm in machines:
			in_machines |= set(cm.load().prizes)
		counted = [b for b in members if b.name in in_machines] or members
		groups = max(1, len(machines))
		members = counted
		files = {}
		for name, cid in ((badge_set.book, badge_set.book_id), (badge_set.category, badge_set.category_id)):
			key = ("category", name, cid, badge_set.book_id, badge_set.title, len(members), groups, picture.read_bytes())
			files[f"pc/rt/Ca/{name}.cab.szs"] = self.cached(key, lambda name=name, cid=cid: makers.make_category(
				name, badge_set.title, cid, badge_set.book_id, len(members), groups, picture).build())
		return files

	def machine_files(self, cm: CustomMachine) -> tuple[f.Machine, dict[str, bytes]]:
		"""A custom machine's own files: the machine, and its icon and background if they're its own."""
		m, files = self.compile_machine(cm)
		files[f"pc/ci/{m.name}.cib.szs"] = yaz0.compress(m.build())
		return m, files

	def badge_picture(self, name: str) -> Image.Image:
		badge = self.workspace.get_badge(name)
		if badge:
			return makers.load_image(self.workspace.badge_picture(badge))
		return Image.fromarray(self.archive.badge_image(name), "RGBA")

	# --- machines ---

	def compile_machine(self, cm: CustomMachine) -> tuple[f.Machine, dict[str, bytes]]:
		"""A custom machine and the files it adds (besides its badges)."""
		m = cm.load()
		if len(cm.name) > MACHINE_NAME_MAX:
			raise ValueError(f"{cm.title or cm.name}: its name ({cm.name}) is longer than the game takes "
				f"({MACHINE_NAME_MAX} characters); restart the helper to shorten it")
		m.name = cm.name
		m.titles = [cm.title] * f.LANGUAGES
		m.prune_names()
		m.prune_obstacles()
		m.catalog = catalog_layout(m)
		files = {}
		if cm.cabinet_image:
			trim = cm.cabinet_trim if self.archive.has(cm.cabinet_trim) else self.archive.cabinets()[0]
			picture = self.workspace.image_path(cm.cabinet_image)
			name = cm.cabinet_name
			files[f"pc/rt/Cr/{name}.crb.szs"] = self.cached(("cabinet", name, trim, picture.read_bytes()),
				lambda: makers.make_cabinet(picture, name, self.archive.cabinet(trim)).build())
			m.crane = name
		template_icon = self.archive.icon(self.archive.icons()[0])
		icon_key = None
		if cm.icon_mode == "custom" and cm.icon_image:
			picture = self.workspace.image_path(cm.icon_image)
			icon_key = ("icon", cm.icon_name, picture.read_bytes())
			make_icon = lambda: makers.make_icon(picture, cm.icon_name, template_icon).build()
		elif cm.icon_mode == "auto":
			names, arrangement = cm.collage(m)
			icon_key = ("collage", cm.icon_name, tuple(cm.icon_background), tuple(names),
				tuple(self.picture_identity(n) for n in names), repr(arrangement))
			make_icon = lambda: makers.make_icon(makers.collage([self.badge_picture(n) for n in names],
				tuple(cm.icon_background), layout=arrangement), cm.icon_name, template_icon).build()
		if icon_key:
			files[f"pc/rt/CI/{cm.icon_name}.icb.szs"] = self.cached(icon_key, make_icon)
			m.icon = cm.icon_name
		problems = m.problems()
		if problems:
			raise ValueError(f"{cm.title or cm.name}: " + "; ".join(problems))
		return m, files

	def build(self, plan: WeekPlan, ns_data_id: int, serial: int | None = None,
			start: datetime.date | None = None) -> bytes:
		"""The data_v131 container for a week plan. With start, the week is moved to begin then."""
		archive, workspace = self.archive, self.workspace
		machines = list(dict.fromkeys(plan.nintendo)) + list(dict.fromkeys(plan.custom))
		if not machines:
			raise ValueError("The week has no machines")

		# the base week's files that no machine references (categories, the invalid icon, ...)
		referenced = set()
		for name in (n for n in self.week if n.startswith("pc/ci/")):
			base_machine = f.Machine.parse(yaz0.decompress(self.week[name]))
			referenced |= set(archive.machine_parts(base_machine))
		files = {name: content for name, content in self.week.items()
			if name.startswith("pc/rt/") and name.rsplit("/", 1)[1].split(".")[0] not in referenced}

		badges: set[str] = set()
		for i, name in enumerate(plan.nintendo):
			self.progress(f"Adding {name} ({i + 1}/{len(plan.nintendo)})...")
			if name not in archive.machines:
				raise ValueError(f"No Nintendo machine called {name}")
			m = archive.machines[name]
			missing = archive.missing_parts(m)
			if missing:
				raise ValueError(f"{name} can't be built: missing {', '.join(missing)}")
			files[archive.path(name)] = archive.compressed(name)
			for part in [m.crane, m.icon] + m.attachments + m.fixed:
				files[archive.path(part)] = archive.compressed(part)
			badges |= set(m.prizes)

		for i, name in enumerate(plan.custom):
			cm = workspace.get_machine(name)
			if cm is None:
				raise ValueError(f"No custom machine called {name}")
			self.progress(f"Building {cm.title or name} ({i + 1}/{len(plan.custom)})...")
			m, extra = self.machine_files(cm)
			files.update(extra)
			made_here = {n.rsplit("/", 1)[1].split(".")[0] for n in extra}
			for part in [m.crane, m.icon] + m.attachments + m.fixed:
				if part in made_here:
					continue
				if not archive.has(part):
					raise ValueError(f"{cm.title or name}: {part} isn't in the archive")
				files[archive.path(part)] = archive.compressed(part)
			badges |= set(m.prizes)

		# badges and their categories
		custom_sets = {}
		categories = set()
		for i, name in enumerate(sorted(badges)):
			if i % 20 == 0:
				self.progress(f"Adding badges ({i + 1}/{len(badges)})...")
			custom = workspace.get_badge(name)
			if custom:
				badge_set = workspace.get_set(custom.set)
				if badge_set is None:
					raise ValueError(f"{name}'s set ({custom.set}) no longer exists")
				custom_sets[badge_set.code] = badge_set
				files[f"pc/rt/Pr/{name}.prb.szs"] = self.custom_badge_file(custom, badge_set.category)
			elif name in archive.badges:
				files[archive.path(name)] = self.nintendo_badge_file(name)
				categories.add(archive.badges[name].category)
			else:
				raise ValueError(f"Badge {name} isn't in the archive or your badges")
		for category in categories:
			path = f"pc/rt/Ca/{category}.cab.szs"
			if path not in files and archive.has(category):
				files[path] = archive.compressed(category)
		for badge_set in custom_sets.values():
			files.update(self.set_files(badge_set))

		# the registry: everything the week holds must be named in PrizeCollection.xml
		self.progress("Writing the registry and schedule...")
		names_in = lambda folder: sorted(n.rsplit("/", 1)[1].split(".")[0] for n in files if n.startswith(folder + "/"))
		xml = self.xml
		xml = add_to_registry(xml, "Prizes", names_in("pc/rt/Pr"))
		xml = add_to_registry(xml, "Cranes", names_in("pc/rt/Cr"))
		xml = add_to_registry(xml, "CraneIcons", names_in("pc/rt/CI"))
		xml = add_to_registry(xml, "CraneInstances", names_in("pc/ci"))
		xml = add_to_registry(xml, "Categories", [c for c in names_in("pc/rt/Ca") if not c.endswith("Book")])
		xml = set_registry(xml, "FixedObjects", names_in("pc/rt/FO"))
		xml = set_registry(xml, "Attachments", names_in("pc/rt/At"))
		files["pc/PrizeCollection.xml"] = BOM + xml.encode("utf-8")

		top = dict(self.top)
		schedule = top["Schedule.xml"].decode("utf-8")
		lineup = daily_lineup(machines, len(self.days), plan.per_series, self.max_per_day)
		week_stem = self.week_name.rsplit("/", 1)[1].split(".")[0]
		top["Schedule.xml"] = write_schedule(schedule, week_stem, lineup, self.days).encode("utf-8")
		self.progress("Packing...")
		top[self.week_name] = sarc.sarc_write(files, 0x80)
		if plan.extras:
			self.progress("Adding the hall pictures, Bunny's lines and gallery posts...")
			top = extras.apply(top, plan.extras, workspace.image_path)
		if start is not None:
			top = redate(top, start)
		payload = sarc.sarc_write(top, 0x80)
		out = boss.build_container(self.base_raw, self.base_body, self.key, payload, ns_data_id, serial or int(time.time()))

		# read it back the way the console will
		boss.verify_container(out, self.key)
		check_top = sarc.sarc_read(boss.open_container(out, self.key)[1])
		check_week = sarc.sarc_read(check_top[next(n for n in check_top if n.startswith("sharc/"))])
		assert all(check_week[name] == content for name, content in files.items())
		problem = schedule_problem(check_top["Schedule.xml"].decode("utf-8"))
		if problem:
			raise ValueError(problem)
		return out

	def lineup(self, plan: WeekPlan) -> list[list[str]]:
		machines = list(dict.fromkeys(plan.nintendo)) + list(dict.fromkeys(plan.custom))
		return daily_lineup(machines, len(self.days), plan.per_series, self.max_per_day)


def new_custom_machine(archive: Archive, workspace: Workspace, template: str, title: str,
		series_code: str = "Custom") -> CustomMachine:
	"""A custom machine that starts as a copy of a Nintendo machine (parts missing from the
	archive left out)."""
	made = archive.template_machine(template)
	if made is None:
		raise ValueError(f"None of {template}'s badges are in the archive")
	m, _notes = made
	name = workspace.new_machine_name(series_code)
	m.id = workspace.allocate_id("machine", archive.ids["machine"])
	m.name = name
	cm = CustomMachine(name=name, title=title, template=template, machine={}, cabinet_trim=m.crane)
	cm.store(m)
	workspace.save_machine(cm)
	return cm


def summarize_machine(m: f.Machine) -> str:
	return f"{len(m.prize_placements)} badges ({len(m.prizes)} kinds), {len(m.fixed_placements)} fixed objects"


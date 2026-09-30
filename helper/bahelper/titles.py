"""Programs a badge can open when it's tapped on the HOME Menu.

A badge's u64 at 0xA4 is a title ID (0xFFFFFFFFFFFFFFFF: it opens nothing). Nintendo's
"shortcut" badges (Pr_*_SC_*) use the USA system applets: System Settings 0004001000021000,
Download Play ...21100, Activity Log ...21200, Camera ...21400, Sound ...21500, Mii Maker
...21700, StreetPass Mii Plaza ...21800, Nintendo eShop ...21900. The other regions number the
same applets from 0x20000 (Japan) and 0x22000 (Europe).
"""

NONE = 0xFFFFFFFFFFFFFFFF
SYSTEM_APPLETS = {        # name -> low byte of the title ID
	"System Settings": 0x00,
	"Download Play": 0x01,
	"Activity Log": 0x02,
	"Nintendo 3DS Camera": 0x04,
	"Nintendo 3DS Sound": 0x05,
	"Mii Maker": 0x07,
	"StreetPass Mii Plaza": 0x08,
	"Nintendo eShop": 0x09,
}
REGION_BASES = {"USA": 0x21000, "JPN": 0x20000, "EUR": 0x22000}
SYSTEM_PREFIX = 0x00040010 << 32


def applet(name: str, region: str = "USA") -> int:
	return SYSTEM_PREFIX | (REGION_BASES[region] + (SYSTEM_APPLETS[name] << 8))


def describe(title_id: int | None) -> str:
	"""A title ID in words: "Download Play (USA)", or the ID itself."""
	if title_id is None or title_id == NONE:
		return "nothing"
	if title_id >> 32 == 0x00040010:
		low = title_id & 0xFFFFFFFF
		for region, base in REGION_BASES.items():
			for name, byte in SYSTEM_APPLETS.items():
				if low == base + (byte << 8):
					return f"{name} ({region})"
	return f"the program {title_id:016X}"


def parse(text: str) -> int | None:
	"""A title ID typed in hex (spaces and dashes allowed); None if it isn't one."""
	digits = "".join(c for c in text if c not in " -_").removeprefix("0x").removeprefix("0X")
	if len(digits) != 16:
		return None
	try:
		return int(digits, 16)
	except ValueError:
		return None

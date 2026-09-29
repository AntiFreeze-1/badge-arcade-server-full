"""Nintendo Badge Arcade server."""

from pathlib import Path


def _read_version() -> str:
	try:
		return (Path(__file__).resolve().parents[2] / "version.txt").read_text(encoding="utf-8").strip()
	except OSError:
		return "unknown"


__version__ = _read_version()

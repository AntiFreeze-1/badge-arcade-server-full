"""Maintenance: while it's on, the login server turns the 3DS away with the system's
"under maintenance" error.

The state is kept in maintenance.json next to config.json (the manager's
Maintenance tab writes it) and read again whenever it changes, so turning
maintenance on or off needs no restart. It can be on until turned off, or only
during a window (start and/or end time). It can also override the values the game
reads from SecureConnection::GetMaintenanceStatus, whose meaning is unknown.
"""

from dataclasses import dataclass
from pathlib import Path
import datetime
import json
import logging

logger = logging.getLogger(__name__)

# NEX result the login server answers with during maintenance (RendezVous is the
# authentication protocol's namespace, as for RendezVous::InvalidUsername)
MAINTENANCE_ERROR = "RendezVous::GameServerMaintenance"


def now() -> datetime.datetime:
	return datetime.datetime.now().astimezone()


def _parse_time(value) -> datetime.datetime | None:
	if not value:
		return None
	moment = datetime.datetime.fromisoformat(value)
	return moment if moment.tzinfo else moment.astimezone()  # no offset: local time


def _show(moment: datetime.datetime) -> str:
	return moment.astimezone().strftime("%Y-%m-%d %H:%M")


@dataclass
class MaintenanceState:
	enabled: bool = False
	start: datetime.datetime | None = None   # None: from now on
	end: datetime.datetime | None = None     # None: until turned off
	# Overrides for GetMaintenanceStatus (None: config.json's "maintenance" values)
	status: int | None = None
	time: int | None = None
	is_success: bool | None = None

	def active(self, at: datetime.datetime | None = None) -> bool:
		at = at or now()
		return (self.enabled and (self.start is None or at >= self.start)
			and (self.end is None or at < self.end))

	def describe(self, at: datetime.datetime | None = None) -> str:
		at = at or now()
		if not self.enabled:
			return "Off"
		if self.end is not None and at >= self.end:
			return f"Off (the maintenance window ended at {_show(self.end)})"
		if self.start is not None and at < self.start:
			return f"Scheduled from {_show(self.start)}" + (f" to {_show(self.end)}" if self.end else " (until turned off)")
		return "On" + (f" until {_show(self.end)}" if self.end else " (until turned off)")

	def to_json(self) -> dict:
		return {
			"enabled": self.enabled,
			"start": self.start.isoformat(timespec="minutes") if self.start else None,
			"end": self.end.isoformat(timespec="minutes") if self.end else None,
			"status": self.status,
			"time": self.time,
			"is_success": self.is_success,
		}

	@classmethod
	def from_json(cls, data: dict) -> "MaintenanceState":
		state = cls(
			enabled=bool(data.get("enabled")),
			start=_parse_time(data.get("start")),
			end=_parse_time(data.get("end")),
			status=None if data.get("status") is None else int(data["status"]),
			time=None if data.get("time") is None else int(data["time"]),
			is_success=None if data.get("is_success") is None else bool(data["is_success"]),
		)
		if state.status is not None and not 0 <= state.status <= 0xFFFF:
			raise ValueError("status must be between 0 and 0xFFFF")
		if state.time is not None and not 0 <= state.time <= 0xFFFFFFFF:
			raise ValueError("time must be between 0 and 0xFFFFFFFF")
		return state


def load(path: Path | None) -> MaintenanceState:
	"""The saved state; off when there's no file (or it can't be read)."""
	if path is None:
		return MaintenanceState()
	try:
		return MaintenanceState.from_json(json.loads(path.read_text(encoding="utf-8")))
	except FileNotFoundError:
		return MaintenanceState()
	except (OSError, ValueError, TypeError, AttributeError) as e:
		logger.warning("Ignoring %s (%s): maintenance is off", path, e)
		return MaintenanceState()


def save(path: Path, state: MaintenanceState) -> None:
	if state.start and state.end and state.end <= state.start:
		raise ValueError("The maintenance window has to end after it starts.")
	temp = path.with_name(path.name + ".tmp")
	temp.write_text(json.dumps(state.to_json(), indent="\t") + "\n", encoding="utf-8")
	temp.replace(path)


class MaintenanceFile:
	"""maintenance.json, read again whenever it changes."""

	def __init__(self, path: Path | None):
		self.path = path
		self._stamp: tuple[int, int] | None = None
		self._state = MaintenanceState()

	def current(self) -> MaintenanceState:
		if self.path is None:
			return self._state
		try:
			stat = self.path.stat()
			stamp = (stat.st_mtime_ns, stat.st_size)
		except OSError:
			stamp = None
		if stamp != self._stamp:
			self._stamp = stamp
			self._state = load(self.path) if stamp else MaintenanceState()
			logger.info("Maintenance: %s", self._state.describe())
		return self._state

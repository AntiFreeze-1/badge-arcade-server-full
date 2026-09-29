"""Configuration loading for the Badge Arcade server."""

from dataclasses import dataclass, field
import ipaddress
from pathlib import Path
import datetime
import json
import socket
import logging

logger = logging.getLogger(__name__)

# kerberos_password values that come with the code (install.py makes a random one)
PLACEHOLDER_PASSWORDS = {"change-me", "replace-with-a-random-string"}


def local_address_toward(ip: str) -> str | None:
	"""This PC's address on the route to ip (the one a device at ip can reach it on). No
	packet is sent."""
	with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
		try:
			s.connect((ip, 1))
			address = s.getsockname()[0]
		except OSError:
			return None
	return None if address == "0.0.0.0" else address


def lan_address() -> str | None:
	"""This PC's current LAN IP: the address of the adapter used for outgoing
	traffic. No packet is sent."""
	with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
		try:
			s.connect(("10.255.255.255", 1))
			return s.getsockname()[0]
		except OSError:
			return None


@dataclass
class MaintenanceConfig:
	# Values returned by SecureConnection (Badge Arcade)::GetMaintenanceStatus.
	# These match what Pretendo's server sends; 0xFFFF appears to mean
	# "no maintenance scheduled".
	status: int = 0xFFFF
	time: int = 0
	is_success: bool = True


@dataclass
class Config:
	# Address the 3DS uses to reach this machine: the PC's LAN IP. "auto" finds
	# the current one each time the server starts (for PCs without a fixed IP).
	public_host: str = "auto"
	bind_host: str = "0.0.0.0"

	auth_port: int = 59400
	secure_port: int = 59401
	http_port: int = 8080

	# Secret used to encrypt the tickets passed from the authentication
	# server to the secure server. The 3DS never sees it; change it to any
	# random string.
	kerberos_password: str = "change-me"

	access_key: str = "82d5962d"
	nex_version: int = 30716  # NEX 3.7.16

	data_dir: str = "data"
	boss_dir: str | None = "../other"

	# Mapping of PID -> NEX password (or a 32-hex-digit Kerberos key derived
	# from it).
	accounts: dict[int, str] = field(default_factory=dict)

	# nex-keys.txt as written to the SD card by the get_3ds_pid_password /
	# Get_PID_Passwrd homebrew ("pid:password" lines). Re-read when it changes.
	nex_keys_file: str | None = "nex-keys.txt"

	# Used for any PID that has no known password. Only useful if you know
	# every console you use shares it.
	default_nex_password: str | None = None

	# S3 key prefix used by the original servers.
	datastore_path: str = "10.CTR_JWVJ_datastore/ds/1/data"

	maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)

	# Date ("YYYY-MM-DD") the server reports to the game at login, with the
	# real time of day. Badge Arcade picks its SpotPass schedule (machines,
	# messages) by this date. null (the default) reports the current date;
	# spotpass-letter/serve.py moves served weeks to whichever date this is.
	game_date: str | None = None

	# Raw NintendoClients setting overrides (e.g. {"prudp.resend_limit": 8}).
	nex_settings: dict[str, object] = field(default_factory=dict)

	base_dir: Path = field(default_factory=Path.cwd)

	# Whether public_host was set to an address (rather than "auto"). An address is always
	# used as it is; otherwise each 3DS gets the address it can reach (see address_for).
	public_host_fixed: bool = field(init=False, default=False)
	_announced: set = field(init=False, default_factory=set, repr=False)

	def __post_init__(self):
		self.public_host_fixed = self.public_host not in ("auto", "")
		if not self.public_host_fixed:
			self.public_host = lan_address() or "127.0.0.1"

	def address_for(self, console_ip: str | None) -> str:
		"""The address to give a 3DS at console_ip: this PC's address on the route to it
		(the hotspot's address for a 3DS on the hotspot, the LAN address otherwise), so it
		stays right when the hotspot starts after the server or the PC changes network.
		public_host when it's fixed in the config, or when the route can't be found."""
		if self.public_host_fixed or not console_ip:
			return self.public_host
		try:
			if ipaddress.ip_address(console_ip).version != 4:
				return self.public_host
		except ValueError:
			return self.public_host
		address = local_address_toward(console_ip) or self.public_host
		if address != self.public_host and (console_ip, address) not in self._announced:
			self._announced.add((console_ip, address))
			logger.info("Giving the 3DS at %s the server address %s", console_ip, address)
		return address

	def resolve(self, path: str) -> Path:
		p = Path(path)
		return p if p.is_absolute() else (self.base_dir / p).resolve()

	@property
	def data_path(self) -> Path:
		return self.resolve(self.data_dir)

	@property
	def boss_path(self) -> Path | None:
		return self.resolve(self.boss_dir) if self.boss_dir else None

	@property
	def nex_keys_path(self) -> Path | None:
		return self.resolve(self.nex_keys_file) if self.nex_keys_file else None

	@property
	def http_base_url(self) -> str:
		return f"http://{self.public_host}:{self.http_port}"

	def http_url_for(self, console_ip: str | None) -> str:
		"""http_base_url with the address a 3DS at console_ip can reach (see address_for)."""
		return f"http://{self.address_for(console_ip)}:{self.http_port}"


def load_config(path: str | Path | None) -> Config:
	if path is None:
		logger.warning("No config file given, using defaults")
		return Config()

	path = Path(path).resolve()
	with open(path, encoding="utf-8") as f:
		raw = json.load(f)

	raw.pop("_comment", None)
	maintenance = MaintenanceConfig(**raw.pop("maintenance", {}))
	accounts = {int(pid): str(pw) for pid, pw in raw.pop("accounts", {}).items()}

	known = set(Config.__dataclass_fields__) - {"base_dir", "maintenance", "accounts", "public_host_fixed", "_announced"}
	unknown = set(raw) - known
	if unknown:
		raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")

	config = Config(
		**raw, accounts=accounts, maintenance=maintenance, base_dir=path.parent
	)
	if config.game_date:
		datetime.date.fromisoformat(config.game_date)  # fail early on a bad date
	if config.kerberos_password in PLACEHOLDER_PASSWORDS:
		logger.warning("kerberos_password in %s is still the example value; set it to any random string", path.name)
	return config

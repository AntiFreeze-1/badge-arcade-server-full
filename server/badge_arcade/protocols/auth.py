"""NEX authentication server (UDP 59400 by default)."""

from nintendo.nex import authentication, common, rmc, settings

from ..config import Config
from ..maintenance import MAINTENANCE_ERROR, MaintenanceFile
from ..nex_common import SECURE_SERVER_PID, build_ticket, secure_station_url, user_key_from_secret
from ..nex_keys import NexKeysFile
from ..storage import Storage

import datetime
import logging
logger = logging.getLogger(__name__)

BUILD_NAME = "Badge Arcade personal server"


class AuthenticationServer(authentication.AuthenticationServer):
	def __init__(self, s: settings.Settings, config: Config, storage: Storage):
		super().__init__()
		self.settings = s
		self.config = config
		self.storage = storage
		self.nex_keys = NexKeysFile(config.nex_keys_path)
		self.maintenance = MaintenanceFile(config.maintenance_path)

	def lookup_secret(self, pid: int, client: rmc.RMCClient) -> tuple[str, str] | None:
		"""Finds the NEX password (or derived key) for a PID, and where it came from."""
		account = self.storage.get_nex_account(pid)
		if account is not None:
			password, source = account
			labels = {"nasc": "NASC", "nasc-ip": "NASC", "nex_token": "NEX token", "nex_token_login": "NEX token"}
			return password, labels.get(source, source)

		secret = self.config.accounts.get(pid)
		if secret is not None:
			return secret, "config accounts"

		secret = self.nex_keys.get(pid)
		if secret is not None:
			return secret, self.nex_keys.path.name

		ip = client.remote_address()[0]
		password = self.storage.recent_ip_password(ip)
		if password is not None:
			self.storage.set_nex_password(pid, password, "nasc-ip")
			return password, f"NASC request from {ip}"

		if self.config.default_nex_password is not None:
			return self.config.default_nex_password, "default_nex_password"
		return None

	def user_key(self, pid: int, client: rmc.RMCClient) -> bytes | None:
		found = self.lookup_secret(pid, client)
		if found is None:
			logger.error(
				"No NEX password known for PID %i. Put nex-keys.txt from the SD card "
				"next to the config file, or add the PID to \"accounts\" (see README).", pid
			)
			return None

		secret, source = found
		logger.info("Login from PID %i (%s:%i), credentials from %s", pid, *client.remote_address(), source)
		return user_key_from_secret(secret, pid)

	def connection_data(self, client: rmc.RMCClient | None = None) -> authentication.RVConnectionData:
		data = authentication.RVConnectionData()
		console_ip = client.remote_address()[0] if client is not None else None
		data.main_station = secure_station_url(self.config, self.config.address_for(console_ip))
		data.special_protocols = []
		data.special_station = common.StationURL("")  # Empty string, like Pretendo's server
		data.server_time = self.game_time()
		return data

	def game_time(self) -> common.DateTime:
		now = common.DateTime.now()
		if not self.config.game_date:
			return now
		date = datetime.date.fromisoformat(self.config.game_date)
		return common.DateTime.make(date.year, date.month, date.day, now.hour(), now.minute(), now.second())

	async def login(self, client, username):
		return self.do_login(client, username)

	async def login_ex(self, client, username, extra_data):
		# Games that get their server through the NNID account server send the
		# NEX token back here. The game logs in with its NNID PID, which can
		# differ from the PID in the token reply, so go by the token.
		token = getattr(extra_data, "token", None)
		password = self.storage.nex_token_password(token) if token else None
		if password is not None and username.rstrip("\x00").isdigit():
			self.storage.set_nex_password(int(username.rstrip("\x00")), password, "nex_token_login")
		elif token:
			logger.warning("LoginEx with an unknown NEX token (issued before a server restart?)")
		return self.do_login(client, username)

	def do_login(self, client: rmc.RMCClient, username: str) -> rmc.RMCResponse:
		response = rmc.RMCResponse()
		response.connection_data = self.connection_data(client)

		try:
			pid = int(username.rstrip("\x00"))
		except ValueError:
			logger.warning("Login with non-numeric username %r rejected", username)
			pid = None

		if self.maintenance.current().active():
			logger.info("Login from PID %s refused: the server is in maintenance", pid)
			response.result = common.Result.error(MAINTENANCE_ERROR)
			response.pid = 0
			response.ticket = b""
			response.server_name = ""
			return response

		user_key = self.user_key(pid, client) if pid is not None else None
		if user_key is None:
			# An unknown user gets a successful call with the error in the
			# result field and the other fields left blank
			response.result = common.Result.error("RendezVous::InvalidUsername")
			response.pid = 0
			response.ticket = b""
			response.server_name = ""
			return response

		response.result = common.Result.success()
		response.pid = pid
		response.ticket = build_ticket(self.settings, self.config, pid, user_key)
		response.server_name = BUILD_NAME
		return response

	async def request_ticket(self, client, source, target):
		if target != SECURE_SERVER_PID:
			logger.warning("Ticket requested for unknown target PID %i", target)

		response = rmc.RMCResponse()
		if self.maintenance.current().active():
			response.result = common.Result.error(MAINTENANCE_ERROR)
			response.ticket = b""
			return response
		user_key = self.user_key(source, client)
		if user_key is None:
			response.result = common.Result.error("RendezVous::InvalidUsername")
			response.ticket = b""
			return response

		response.result = common.Result.success()
		response.ticket = build_ticket(self.settings, self.config, source, user_key, target)
		return response

	async def get_pid(self, client, username):
		try:
			return int(username)
		except ValueError:
			raise common.RMCError("RendezVous::InvalidUsername") from None

	async def get_name(self, client, pid):
		return str(pid)

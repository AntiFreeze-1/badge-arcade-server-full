"""SecureConnection protocol with the Badge Arcade GetMaintenanceStatus extension."""

from nintendo.nex import common, rmc, secure

from ..config import Config
from ..maintenance import MaintenanceFile

import itertools
import logging
logger = logging.getLogger(__name__)


class SecureConnectionServer(secure.SecureConnectionServer):
	METHOD_GET_MAINTENANCE_STATUS = 9

	def __init__(self, config: Config):
		super().__init__()
		self.config = config
		self.maintenance = MaintenanceFile(config.maintenance_path)
		self.methods[self.METHOD_GET_MAINTENANCE_STATUS] = self.handle_get_maintenance_status
		self._connection_ids = itertools.count(1)
		self._stations: dict[int, list[common.StationURL]] = {}

	async def logout(self, client):
		pid = client.pid()
		if pid is not None:
			logger.info("PID %i disconnected", pid)
			self._stations.pop(pid, None)

	def _register(self, client: rmc.RMCClient, urls: list[common.StationURL]) -> rmc.RMCResponse:
		host, port = client.remote_address()
		connection_id = next(self._connection_ids)

		public = urls[0].copy() if urls else common.StationURL("prudp")
		public["address"] = host
		public["port"] = port
		public["natf"] = 0
		public["natm"] = 0
		public["type"] = 3

		self._stations[client.pid()] = [*urls, public]
		logger.info("PID %i registered from %s:%i", client.pid(), host, port)

		response = rmc.RMCResponse()
		response.result = common.Result.success()
		response.connection_id = connection_id
		response.public_station = public
		return response

	async def register(self, client, urls):
		return self._register(client, urls)

	async def register_ex(self, client, urls, login_data):
		return self._register(client, urls)

	async def request_connection_data(self, client, cid, pid):
		response = rmc.RMCResponse()
		response.result = False
		response.connection_data = []
		return response

	async def request_urls(self, client, cid, pid):
		urls = self._stations.get(pid, [])
		response = rmc.RMCResponse()
		response.result = bool(urls)
		response.urls = urls
		return response

	async def test_connectivity(self, client):
		pass

	async def update_urls(self, client, urls):
		pass

	async def replace_url(self, client, url, new):
		pass

	async def send_report(self, client, report_id, data):
		logger.info("PID %s sent report %i (%i bytes)", client.pid(), report_id, len(data))

	async def handle_get_maintenance_status(self, client, input, output):
		logger.info("SecureConnection.get_maintenance_status()")
		defaults = self.config.maintenance
		state = self.maintenance.current()  # the Maintenance tab's overrides, if any
		output.u16(defaults.status if state.status is None else state.status)
		output.u32(defaults.time if state.time is None else state.time)
		output.bool(defaults.is_success if state.is_success is None else state.is_success)

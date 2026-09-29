"""Runs the NEX authentication/secure servers and the HTTP server together."""

from contextlib import asynccontextmanager
from nintendo.nex import rmc
import anyio

from .config import Config
from .http_server import start_http_server
from .nex_common import make_settings, secure_server_key
from .protocols.auth import AuthenticationServer
from .protocols.datastore import DataStoreServer
from .protocols.secure import SecureConnectionServer
from .protocols.shop import ShopServer
from .storage import Storage

import logging
logger = logging.getLogger(__name__)


@asynccontextmanager
async def run_servers(config: Config):
	storage = Storage(config.data_path)
	if pruned := storage.prune_uploads():
		logger.info("Forgot %i unfinished upload(s) older than a day", pruned)
	s =make_settings(config)

	auth_servers = [AuthenticationServer(s, config, storage)]
	secure_servers = [
		SecureConnectionServer(config),
		DataStoreServer(config, storage),
		ShopServer(storage),
	]

	http = start_http_server(config, storage)
	try:
		async with rmc.serve(s, auth_servers, config.bind_host, config.auth_port):
			async with rmc.serve(
				s, secure_servers, config.bind_host, config.secure_port,
				key=secure_server_key(config)
			):
				logger.info(
					"Ready. Auth: %s:%i/udp, secure: %s:%i/udp, HTTP: %s",
					config.public_host, config.auth_port, config.public_host,
					config.secure_port, config.http_base_url
				)
				if config.boss_path and not config.boss_path.is_dir():
					logger.warning("SpotPass directory %s does not exist", config.boss_path)
				yield storage
	finally:
		http.shutdown()
		http.server_close()
		storage.close()


async def serve_forever(config: Config) -> None:
	async with run_servers(config):
		await anyio.sleep_forever()

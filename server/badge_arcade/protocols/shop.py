"""Shop (Badge Arcade) protocol.

On the wire the protocol byte is 0x7F followed by a u16 custom ID of 200,
which NintendoClients' RMC layer maps to protocol ID 200.
"""

from nintendo.nex import common

from ..storage import Storage

import logging
logger = logging.getLogger(__name__)


class ShopPostPlayLogParam(common.Structure):
	def __init__(self):
		super().__init__()
		self.unknown1 = []
		self.timestamp = common.DateTime(0)
		self.unknown2 = ""

	def load(self, stream, version):
		self.unknown1 = stream.list(stream.u32)
		self.timestamp = stream.datetime()
		self.unknown2 = stream.string()

	def save(self, stream, version):
		stream.list(self.unknown1, stream.u32)
		stream.datetime(self.timestamp)
		stream.string(self.unknown2)


class ShopServer:
	PROTOCOL_ID = 200

	METHOD_GET_RIV_TOKEN = 1
	METHOD_POST_PLAY_LOG = 2

	def __init__(self, storage: Storage):
		self.storage = storage
		self.methods = {
			self.METHOD_GET_RIV_TOKEN: self.handle_get_riv_token,
			self.METHOD_POST_PLAY_LOG: self.handle_post_play_log,
		}

	async def logout(self, client):
		pass

	async def handle(self, client, method_id, input, output):
		if method_id in self.methods:
			await self.methods[method_id](client, input, output)
		else:
			logger.warning("Unknown Shop method called: %i", method_id)
			raise common.RMCError("Core::NotImplemented")

	async def handle_get_riv_token(self, client, input, output):
		item_code = input.string()
		reference_id = input.qbuffer()
		logger.info("Shop.get_riv_token(%r, %s) - stubbed", item_code, reference_id.hex())
		output.string("")

	async def handle_post_play_log(self, client, input, output):
		param = input.extract(ShopPostPlayLogParam)
		logger.info(
			"Shop.post_play_log(%s, %s, %r)", param.unknown1, param.timestamp, param.unknown2
		)
		self.storage.add_play_log(client.pid(), {
			"unknown1": param.unknown1,
			"timestamp": str(param.timestamp),
			"unknown2": param.unknown2,
		})

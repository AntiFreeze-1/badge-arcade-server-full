"""DataStore protocol with the Badge Arcade GetMetaByOwnerId extension.

What Badge Arcade stores (as far as Pretendo's research shows):
  * "FreePlayData": a DataStore object of data type 100 whose meta binary
    tracks free plays / practice crane state. Updated with ChangeMeta and
    read back with GetMetaByOwnerId.
  * The user's play info: object data (a few hundred bytes) uploaded over
    HTTP with PreparePostObject/PrepareUpdateObject and downloaded with
    PrepareGetObject. Found through persistence slot 0.

Pretendo's server uses a single data ID for both. This implementation is a
generic object store that keeps the same behaviour: PreparePostObject reuses
the object in the target persistence slot if it has no data uploaded yet.
"""

from nintendo.nex import common, datastore, rmc

from ..config import Config
from ..storage import Storage

import base64
import datetime
import json
import logging
import secrets
logger = logging.getLogger(__name__)

NO_PERSISTENCE = 65535

# DataStoreChangeMetaParam.modifies_flag bits
MODIFY_NAME = 0x01
MODIFY_PERMISSION = 0x02
MODIFY_DEL_PERMISSION = 0x04
MODIFY_PERIOD = 0x08
MODIFY_META_BINARY = 0x10
MODIFY_TAGS = 0x20
MODIFY_DATA_TYPE = 0x80
MODIFY_STATUS = 0x100


class DataStoreGetMetaByOwnerIdParam(common.Structure):
	def __init__(self):
		super().__init__()
		self.owner_ids = []
		self.data_types = []
		self.result_option = 0
		self.result_range = common.ResultRange()

	def load(self, stream, version):
		self.owner_ids = stream.list(stream.u32)
		self.data_types = stream.list(stream.u16)
		self.result_option = stream.u8()
		self.result_range = stream.extract(common.ResultRange)

	def save(self, stream, version):
		stream.list(self.owner_ids, stream.u32)
		stream.list(self.data_types, stream.u16)
		stream.u8(self.result_option)
		stream.add(self.result_range)


class ChangeMetaParam(common.Structure):
	"""DataStoreChangeMetaParam where persistence_target is optional.

	It is only present in structure version 1 and Badge Arcade may not send
	it, which would make NintendoClients' version fail to parse.
	"""

	def __init__(self):
		super().__init__()
		self.data_id = 0
		self.modifies_flag = 0
		self.name = ""
		self.permission = datastore.DataStorePermission()
		self.delete_permission = datastore.DataStorePermission()
		self.period = 0
		self.meta_binary = b""
		self.tags = []
		self.update_password = 0
		self.referred_count = 0
		self.data_type = 0
		self.status = 0
		self.compare_param = datastore.DataStoreChangeMetaCompareParam()
		self.persistence_target = None

	def max_version(self, settings):
		return 1

	def load(self, stream, version):
		self.data_id = stream.u64()
		self.modifies_flag = stream.u32()
		self.name = stream.string()
		self.permission = stream.extract(datastore.DataStorePermission)
		self.delete_permission = stream.extract(datastore.DataStorePermission)
		self.period = stream.u16()
		self.meta_binary = stream.qbuffer()
		self.tags = stream.list(stream.string)
		self.update_password = stream.u64()
		self.referred_count = stream.u32()
		self.data_type = stream.u16()
		self.status = stream.u8()
		self.compare_param = stream.extract(datastore.DataStoreChangeMetaCompareParam)
		if version >= 1 and not stream.eof():
			self.persistence_target = stream.extract(datastore.DataStorePersistenceTarget)

	def save(self, stream, version):
		stream.u64(self.data_id)
		stream.u32(self.modifies_flag)
		stream.string(self.name)
		stream.add(self.permission)
		stream.add(self.delete_permission)
		stream.u16(self.period)
		stream.qbuffer(self.meta_binary)
		stream.list(self.tags, stream.string)
		stream.u64(self.update_password)
		stream.u32(self.referred_count)
		stream.u16(self.data_type)
		stream.u8(self.status)
		stream.add(self.compare_param)
		if version >= 1:
			stream.add(self.persistence_target or datastore.DataStorePersistenceTarget())


def make_permission(permission: int, recipients: list[int]) -> datastore.DataStorePermission:
	p = datastore.DataStorePermission()
	p.permission = permission
	p.recipients = list(recipients)
	return p


def make_key_value(key: str, value: str) -> datastore.DataStoreKeyValue:
	kv = datastore.DataStoreKeyValue()
	kv.key = key
	kv.value = value
	return kv


class DataStoreServer(datastore.DataStoreServer):
	METHOD_GET_META_BY_OWNER_ID = 45  # Replaces GetObjectInfos

	def __init__(self, config: Config, storage: Storage):
		super().__init__()
		self.config = config
		self.storage = storage
		self.methods[self.METHOD_GET_META_BY_OWNER_ID] = self.handle_get_meta_by_owner_id
		self.methods[self.METHOD_CHANGE_META] = self.handle_change_meta_badge_arcade

	# ----- helpers -----

	def object_key(self, data_id: int, version: int) -> str:
		return f"{self.config.datastore_path}/{data_id:011d}-{version:05d}"

	def upload_form(self, key: str, size: int) -> list[datastore.DataStoreKeyValue]:
		"""Form fields the console echoes back in its multipart upload.

		They mimic an S3 browser-based upload like the original servers
		used. The HTTP server only looks at "key" and "file".
		"""
		expiration = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=15)
		policy = {
			"expiration": expiration.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
			"conditions": [
				{"bucket": "badge-arcade-local"},
				{"acl": "private"},
				["eq", "$key", key],
				["content-length-range", size, size],
			],
		}
		return [
			make_key_value("key", key),
			make_key_value("acl", "private"),
			make_key_value("AWSAccessKeyId", "BADGEARCADELOCAL"),
			make_key_value("policy", base64.b64encode(json.dumps(policy).encode()).decode()),
			make_key_value("signature", base64.b64encode(secrets.token_bytes(20)).decode()),
		]

	@staticmethod
	def effective_slot(param: datastore.DataStorePreparePostParam) -> int:
		# Pretendo always uses slot 0, so fall back to it when the game asks
		# for no persistence.
		slot = param.persistence_init_param.persistence_id
		return 0 if slot == NO_PERSISTENCE else slot

	def get_owned_object(self, client, data_id: int) -> dict:
		obj = self.storage.get_object(data_id)
		if obj is None:
			logger.warning("Data ID %i not found", data_id)
			raise common.RMCError("DataStore::NotFound")
		if obj["owner_id"] != client.pid():
			logger.warning("PID %s tried to modify data ID %i owned by %i", client.pid(), data_id, obj["owner_id"])
			raise common.RMCError("DataStore::PermissionDenied")
		return obj

	def resolve_target(self, data_id: int, target: datastore.DataStorePersistenceTarget) -> dict:
		if data_id == 0:
			found = self.storage.get_persistence(target.owner_id, target.persistence_id)
			if found is None:
				raise common.RMCError("DataStore::NotFound")
			data_id = found

		obj = self.storage.get_object(data_id)
		if obj is None:
			raise common.RMCError("DataStore::NotFound")
		return obj

	def fields_from_post_param(self, param: datastore.DataStorePreparePostParam) -> dict:
		return dict(
			size=param.size,
			name=param.name,
			data_type=param.data_type,
			meta_binary=param.meta_binary,
			permission=param.permission.permission,
			permission_recipients=param.permission.recipients,
			del_permission=param.delete_permission.permission,
			del_permission_recipients=param.delete_permission.recipients,
			flag=param.flag,
			period=param.period,
			refer_data_id=param.refer_data_id,
			tags=param.tags,
		)

	def create_persistent_object(self, client, param) -> int:
		pid = client.pid()
		slot = self.effective_slot(param)
		data_id = self.storage.create_object(pid, **self.fields_from_post_param(param))
		previous = self.storage.set_persistence(pid, slot, data_id)
		if previous is not None and param.persistence_init_param.delete_last_object:
			self.storage.update_object(previous, deleted=1)
		logger.info(
			"Created data ID %i (type %i, owner %i, persistence slot %i)",
			data_id, param.data_type, pid, slot
		)
		return data_id

	@staticmethod
	def to_meta_info(obj: dict) -> datastore.DataStoreMetaInfo:
		info = datastore.DataStoreMetaInfo()
		info.data_id = obj["data_id"]
		info.owner_id = obj["owner_id"]
		info.size = obj["size"]
		info.name = obj["name"]
		info.data_type = obj["data_type"]
		info.meta_binary = obj["meta_binary"]
		info.permission = make_permission(obj["permission"], obj["permission_recipients"])
		info.delete_permission = make_permission(obj["del_permission"], obj["del_permission_recipients"])
		info.create_time = common.DateTime.fromtimestamp(obj["created"])
		info.update_time = common.DateTime.fromtimestamp(obj["updated"])
		info.period = obj["period"]
		info.status = obj["status"]
		info.referred_count = 0
		info.refer_data_id = obj["refer_data_id"]
		info.flag = obj["flag"]
		info.referred_time = common.DateTime.fromtimestamp(obj["updated"])
		info.expire_time = common.DateTime.future()
		info.tags = obj["tags"]
		info.ratings = []
		return info

	@staticmethod
	def empty_meta_info() -> datastore.DataStoreMetaInfo:
		info = datastore.DataStoreMetaInfo()
		info.data_id = info.owner_id = info.size = 0
		info.name = ""
		info.data_type = 0
		info.meta_binary = b""
		info.create_time = info.update_time = info.referred_time = info.expire_time = common.DateTime(0)
		info.period = info.status = info.referred_count = info.refer_data_id = info.flag = 0
		info.tags = []
		info.ratings = []
		return info

	# ----- persistence / meta -----

	async def get_persistence_info(self, client, owner_id, slot_id):
		data_id = self.storage.get_persistence(owner_id, slot_id)
		logger.info("GetPersistenceInfo(owner=%i, slot=%i) -> %s", owner_id, slot_id, data_id)
		if data_id is None:
			raise common.RMCError("DataStore::NotFound")

		info = datastore.DataStorePersistenceInfo()
		info.owner_id = owner_id
		info.slot_id = slot_id
		info.data_id = data_id
		return info

	async def post_meta_binary(self, client, param):
		logger.info(
			"PostMetaBinary(type=%i, name=%r, meta=%i bytes, slot=%i)", param.data_type,
			param.name, len(param.meta_binary), param.persistence_init_param.persistence_id
		)
		return self.create_persistent_object(client, param)

	async def get_meta(self, client, param):
		obj = self.resolve_target(param.data_id, param.persistence_target)
		return self.to_meta_info(obj)

	async def get_metas(self, client, data_ids, param):
		response = rmc.RMCResponse()
		response.info = []
		response.results = []
		for data_id in data_ids:
			obj = self.storage.get_object(data_id)
			if obj is None:
				response.info.append(self.empty_meta_info())
				response.results.append(common.Result.error("DataStore::NotFound"))
			else:
				response.info.append(self.to_meta_info(obj))
				response.results.append(common.Result.success())
		return response

	async def handle_get_meta_by_owner_id(self, client, input, output):
		param = input.extract(DataStoreGetMetaByOwnerIdParam)
		logger.info(
			"GetMetaByOwnerId(owners=%s, types=%s, option=%i, range=%i+%i)",
			param.owner_ids, param.data_types, param.result_option,
			param.result_range.offset, param.result_range.size
		)

		if len(param.owner_ids) == len(param.data_types):
			pairs = list(zip(param.owner_ids, param.data_types))
		else:
			pairs = [(o, t) for o in param.owner_ids for t in param.data_types]

		infos = []
		for owner_id, data_type in pairs:
			obj = self.storage.latest_object(owner_id, data_type)
			if obj is not None:
				infos.append(self.to_meta_info(obj))

		offset = param.result_range.offset
		if offset == 0xFFFFFFFF:
			offset = 0
		page = infos[offset:offset + param.result_range.size]
		has_next = offset + len(page) < len(infos)

		output.list(page, output.add)
		output.bool(has_next)

	async def handle_change_meta_badge_arcade(self, client, input, output):
		param = input.extract(ChangeMetaParam)
		obj = self.get_owned_object(client, param.data_id)
		flag = param.modifies_flag
		logger.info(
			"ChangeMeta(data_id=%i, flag=0x%X, meta=%i bytes)",
			param.data_id, flag, len(param.meta_binary)
		)

		changes = {}
		if flag & MODIFY_NAME:
			changes["name"] = param.name
		if flag & MODIFY_PERMISSION:
			changes["permission"] = param.permission.permission
			changes["permission_recipients"] = param.permission.recipients
		if flag & MODIFY_DEL_PERMISSION:
			changes["del_permission"] = param.delete_permission.permission
			changes["del_permission_recipients"] = param.delete_permission.recipients
		if flag & MODIFY_PERIOD:
			changes["period"] = param.period
		# Pretendo updates the meta binary unconditionally, so also accept a
		# non-empty meta binary without the flag.
		if flag & MODIFY_META_BINARY or param.meta_binary:
			changes["meta_binary"] = param.meta_binary
		if flag & MODIFY_TAGS:
			changes["tags"] = param.tags
		if flag & MODIFY_DATA_TYPE:
			changes["data_type"] = param.data_type
		if flag & MODIFY_STATUS:
			changes["status"] = param.status

		if "meta_binary" in changes:
			self.log_meta_changes(obj["meta_binary"], changes["meta_binary"])
		self.storage.update_object(obj["data_id"], **changes)

	@staticmethod
	def log_meta_changes(old: bytes, new: bytes) -> None:
		"""Logs which 32-bit fields of a meta binary changed, to help work out
		what Badge Arcade keeps in FreePlayData. The trailing 32-byte signature
		(an HMAC of the fields) is skipped."""
		if len(old) != len(new):
			logger.info("  meta binary size changed: %i -> %i bytes", len(old), len(new))
			return
		end = max(len(new) - 32, 0)
		changed = [
			f"0x{offset:02x}: {int.from_bytes(old[offset:offset + 4], 'little')} -> {int.from_bytes(new[offset:offset + 4], 'little')}"
			for offset in range(0, end - end % 4, 4)
			if old[offset:offset + 4] != new[offset:offset + 4]
		]
		logger.info("  meta changes: %s", ", ".join(changed) or "none")

	# ----- object data -----

	async def prepare_post_object(self, client, param):
		pid = client.pid()
		slot = self.effective_slot(param)
		logger.info(
			"PreparePostObject(type=%i, size=%i, slot=%i)", param.data_type, param.size,
			param.persistence_init_param.persistence_id
		)

		existing_id = self.storage.get_persistence(pid, slot)
		existing = self.storage.get_object(existing_id) if existing_id is not None else None
		if existing is not None and existing["version"] == 0:
			# Attach the data to the meta-only object from PostMetaBinary
			data_id = existing_id
			self.storage.update_object(data_id, size=param.size)
			logger.info("Uploading data for existing data ID %i", data_id)
		else:
			data_id = self.create_persistent_object(client, param)

		key = self.object_key(data_id, 1)
		self.storage.add_upload(key, data_id, 1, param.size)

		info = datastore.DataStoreReqPostInfo()
		info.data_id = data_id
		info.url = f"{self.config.http_url_for(client.remote_address()[0])}/"
		info.headers = []
		info.form = self.upload_form(key, param.size)
		info.root_ca_cert = b""
		return info

	async def complete_post_object(self, client, param):
		logger.info("CompletePostObject(data_id=%i, success=%s)", param.data_id, param.success)
		if not param.success:
			return

		self.get_owned_object(client, param.data_id)
		if not self.storage.finish_upload(param.data_id, 1):
			logger.error("CompletePostObject: no uploaded data received for data ID %i", param.data_id)
			raise common.RMCError("DataStore::NotFound")

	async def prepare_update_object(self, client, param):
		obj = self.get_owned_object(client, param.data_id)
		version = obj["version"] + 1
		logger.info("PrepareUpdateObject(data_id=%i, size=%i) -> version %i", param.data_id, param.size, version)

		key = self.object_key(param.data_id, version)
		self.storage.add_upload(key, param.data_id, version, param.size)

		info = datastore.DataStoreReqUpdateInfo()
		info.version = version
		info.url = f"{self.config.http_url_for(client.remote_address()[0])}/"
		info.headers = []
		info.form = self.upload_form(key, param.size)
		info.root_ca_cert = b""
		return info

	async def complete_update_object(self, client, param):
		logger.info(
			"CompleteUpdateObject(data_id=%i, version=%i, success=%s)",
			param.data_id, param.version, param.success
		)
		if not param.success:
			return

		self.get_owned_object(client, param.data_id)
		if not self.storage.finish_upload(param.data_id, param.version):
			logger.error(
				"CompleteUpdateObject: no uploaded data received for data ID %i version %i",
				param.data_id, param.version
			)
			raise common.RMCError("DataStore::NotFound")

	async def prepare_get_object(self, client, param):
		obj = self.resolve_target(param.data_id, param.persistence_target)
		data_id, version = obj["data_id"], obj["version"]
		logger.info("PrepareGetObject(data_id=%i) -> version %i", data_id, version)

		path = self.storage.object_file(data_id, version)
		if version == 0 or not path.is_file():
			raise common.RMCError("DataStore::NotFound")

		info = datastore.DataStoreReqGetInfo()
		info.url = f"{self.config.http_url_for(client.remote_address()[0])}/{self.object_key(data_id, version)}"
		info.headers = []
		info.size = path.stat().st_size
		info.root_ca_cert = b""
		info.data_id = data_id
		return info

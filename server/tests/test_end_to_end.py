"""End-to-end test: NintendoClients plays the part of the 3DS.

Runs the whole server on localhost and goes through NASC login, NEX
authentication, the secure server and the DataStore save/load flow,
including the HTTP uploads and downloads.

Run with:  python -m pytest tests   (or: python tests/test_end_to_end.py)
"""

from pathlib import Path
import contextlib
import io
import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

import anyio
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nintendo.nex import authentication, backend, common, datastore, rmc, settings, streams
from badge_arcade import admin
from badge_arcade.config import Config, load_config
from badge_arcade.nex_common import derive_user_key
from badge_arcade.http_server import nasc_decode, nasc_encode
from badge_arcade.protocols.datastore import ChangeMetaParam, DataStoreGetMetaByOwnerIdParam
from badge_arcade.protocols.shop import ShopPostPlayLogParam
from badge_arcade.server import run_servers

PID = 1750000001
PASSWORD = "abcdefghijklmnop"
NASC_PID = 1750000002
NASC_PASSWORD = "qrstuvwxyz012345"
IP_PID = 1750000003
IP_PASSWORD = "IPmatchedPasswd1"
KEYS_PID = 1750000004
KEYS_PASSWORD = "keysFilePasswrd1"
BIG_PID = 3000000001  # Written as a negative number by the dump homebrew
BIG_PASSWORD = "bigPidPassword12"
KEY_PID = 1750000005  # Stored as a derived key, as the Wireshark dissector does
KEY_PASSWORD = "derivedKeyPass12"
TOKEN_PID = 1750000006  # Gets its password from a NEX token request
TOKEN_LOGIN_PID = 1750000007  # Logs in with that token under another PID (NNID PID)


class ChangeMetaParamV0(common.Structure):
	"""ChangeMetaParam encoded as structure version 0 (no persistence target)."""

	def __init__(self, param: ChangeMetaParam):
		super().__init__()
		self.param = param

	def save(self, stream, version):
		ChangeMetaParam.save(self.param, stream, 0)


def client_settings(prudp_version: int) -> settings.Settings:
	s = settings.load("3ds") if prudp_version == 0 else settings.default()
	s.configure("82d5962d", 30716)
	s["prudp.version"] = prudp_version
	if prudp_version == 1:
		s["prudp.minor_version"] = 3
	s["nex.struct_header"] = 1
	return s


def http(url: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
	request = urllib.request.Request(url, data=data, headers=headers or {})
	with urllib.request.urlopen(request, timeout=5) as response:
		return response.status, response.read()


def multipart(form: list[datastore.DataStoreKeyValue], content: bytes) -> tuple[bytes, str]:
	boundary = "----------BOUNDARY--------B34F03DAD085D8D5"
	body = b""
	for kv in form:
		body += f'--{boundary}\r\nContent-Disposition: form-data; name="{kv.key}"\r\n\r\n{kv.value}\r\n'.encode()
	body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"\r\n\r\n'.encode()
	body += content + f"\r\n--{boundary}--\r\n".encode()
	return body, f"multipart/form-data; boundary={boundary}"


def post_param(data_type: int, size: int = 0, meta: bytes = b"") -> datastore.DataStorePreparePostParam:
	param = datastore.DataStorePreparePostParam()
	param.size = size
	param.name = ""
	param.data_type = data_type
	param.meta_binary = meta
	param.permission.permission = 0
	param.delete_permission.permission = 3
	param.flag = 0
	param.period = 90
	param.refer_data_id = 0
	param.tags = []
	param.rating_init_param = []
	param.persistence_init_param.persistence_id = 0
	param.persistence_init_param.delete_last_object = True
	param.extra_data = []
	return param


async def get_meta_by_owner_id(client, s, owner_id: int, data_type: int) -> list:
	param = DataStoreGetMetaByOwnerIdParam()
	param.owner_ids = [owner_id]
	param.data_types = [data_type]
	param.result_option = 4
	param.result_range = common.ResultRange(0, 10)
	out = streams.StreamOut(s)
	out.add(param)
	body = await client.request(0x73, 45, out.get())
	stream = streams.StreamIn(body, s)
	infos = stream.list(datastore.DataStoreMetaInfo)
	assert stream.bool() is False
	return infos


async def change_meta(client, s, data_id: int, meta: bytes, v0: bool) -> None:
	param = ChangeMetaParam()
	param.data_id = data_id
	param.modifies_flag = 0x10
	param.meta_binary = meta
	param.persistence_target = datastore.DataStorePersistenceTarget()

	compare = param.compare_param
	compare.comparison_flag = 0
	compare.name = ""
	compare.period = 0
	compare.meta_binary = b""
	compare.tags = []
	compare.referred_count = 0
	compare.data_type = 0
	compare.status = 0

	out = streams.StreamOut(s)
	out.add(ChangeMetaParamV0(param) if v0 else param)
	await client.request(0x73, 38, out.get())


async def upload(info, key_prefix: str, content: bytes) -> None:
	body, content_type = multipart(info.form, content)
	status, _ = await anyio.to_thread.run_sync(
		lambda: http(info.url, body, {"Content-Type": content_type})
	)
	assert status == 204
	key = next(kv.value for kv in info.form if kv.key == "key")
	assert key.startswith(key_prefix)


async def download(info) -> bytes:
	status, data = await anyio.to_thread.run_sync(lambda: http(info.url))
	assert status == 200 and len(data) == info.size
	return data


async def run_flow(config: Config, prudp_version: int, pid: int, password: str, auth_info=None,
		status: tuple[int, int, bool] = (0xFFFF, 0, True)) -> None:
	s = client_settings(prudp_version)

	async with backend.connect(s, "127.0.0.1", config.auth_port) as be:
		async with be.login(str(pid), password, auth_info) as client:
			assert client.pid() == pid

			# SecureConnection::Register and the Badge Arcade maintenance check
			from nintendo.nex import secure
			sc = secure.SecureConnectionClient(client)
			local = common.StationURL("prudp", address="192.168.0.50", port=40000, type=1)
			reg = await sc.register([local])
			assert reg.result.is_success()
			assert reg.public_station["address"] == "127.0.0.1"

			body = await client.request(11, 9, b"")
			stream = streams.StreamIn(body, s)
			assert (stream.u16(), stream.u32(), stream.bool()) == status

			ds = datastore.DataStoreClient(client)

			# First launch: nothing stored yet
			with pytest.raises(common.RMCError) as error:
				await ds.get_persistence_info(pid, 0)
			assert error.value.result().name() == "DataStore::NotFound"

			# FreePlayData meta, then the play info object in the same slot
			data_id = await ds.post_meta_binary(post_param(100, meta=b"free-play-v1"))
			# Binary data with CRLFs and dashes, to exercise the multipart parser
			playinfo = bytes(range(256)) + b"\r\n--not-a-boundary\r\n" + bytes(170)

			post_info = await ds.prepare_post_object(post_param(100, size=len(playinfo)))
			assert post_info.data_id == data_id
			await upload(post_info, config.datastore_path, playinfo)

			complete = datastore.DataStoreCompletePostParam()
			complete.data_id = data_id
			complete.success = True
			await ds.complete_post_object(complete)

			info = await ds.get_persistence_info(pid, 0)
			assert info.data_id == data_id

			metas = await get_meta_by_owner_id(client, s, pid, 100)
			assert [m.data_id for m in metas] == [data_id]
			assert metas[0].meta_binary == b"free-play-v1"

			# ChangeMeta, both with and without the optional persistence target
			await change_meta(client, s, data_id, b"free-play-v2", v0=True)
			assert (await get_meta_by_owner_id(client, s, pid, 100))[0].meta_binary == b"free-play-v2"
			await change_meta(client, s, data_id, b"free-play-v3", v0=False)
			assert (await get_meta_by_owner_id(client, s, pid, 100))[0].meta_binary == b"free-play-v3"

			get_param = datastore.DataStorePrepareGetParam()
			get_param.data_id = data_id
			get_info = await ds.prepare_get_object(get_param)
			assert await download(get_info) == playinfo

			# Save again: PrepareUpdateObject -> upload -> CompleteUpdateObject
			new_playinfo = playinfo[::-1]
			update = datastore.DataStorePrepareUpdateParam()
			update.data_id = data_id
			update.size = len(new_playinfo)
			update.update_password = 0
			update.extra_data = []
			update_info = await ds.prepare_update_object(update)
			assert update_info.version == 2
			await upload(update_info, config.datastore_path, new_playinfo)

			complete = datastore.DataStoreCompleteUpdateParam()
			complete.data_id = data_id
			complete.version = 2
			complete.success = True
			await ds.complete_update_object(complete)

			get_info = await ds.prepare_get_object(get_param)
			assert get_info.url.endswith("-00002")
			assert await download(get_info) == new_playinfo

			# Shop (Badge Arcade), protocol 200
			log = ShopPostPlayLogParam()
			log.unknown1 = [1, 2, 3]
			log.timestamp = common.DateTime.now()
			log.unknown2 = "test"
			out = streams.StreamOut(s)
			out.add(log)
			await client.request(200, 2, out.get())

			out = streams.StreamOut(s)
			out.string("item")
			out.qbuffer(b"\x01\x02")
			body = await client.request(200, 1, out.get())
			assert streams.StreamIn(body, s).string() == ""


def nasc_login(config: Config, form: dict[str, str], headers: dict | None = None) -> dict[str, bytes]:
	body = "&".join(f"{k}={nasc_encode(v)}" for k, v in form.items()).encode()
	status, data = http(
		f"{config.http_base_url}/ac", body,
		{"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
	)
	assert status == 200
	return {k: nasc_decode(v) for k, v in (p.split("=", 1) for p in data.decode().split("&"))}


async def run_all(prudp_version: int) -> None:
	with tempfile.TemporaryDirectory() as tmp:
		boss_dir = Path(tmp) / "boss"
		boss_dir.mkdir()
		(boss_dir / "playinfo_v131.dat.boss").write_bytes(b"boss" + bytes(60))

		offset = prudp_version * 10
		config = Config(
			public_host="127.0.0.1", bind_host="127.0.0.1",
			auth_port=49400 + offset, secure_port=49401 + offset, http_port=48080 + offset,
			kerberos_password="test", data_dir=str(Path(tmp) / "data"),
			boss_dir=str(boss_dir), accounts={PID: PASSWORD}, base_dir=Path(tmp),
		)

		async with run_servers(config):
			# NASC login captures the password, which the auth server then uses
			response = await anyio.to_thread.run_sync(lambda: nasc_login(config, {
				"action": "LOGIN", "gameid": "00134600", "titleid": "0004000000130800",
				"userid": str(NASC_PID), "passwd": NASC_PASSWORD,
			}))
			assert response["returncd"] == b"001"
			assert response["locator"] == f"127.0.0.1:{config.auth_port}".encode()

			# SpotPass files are matched by name, with or without ".boss"
			for name in ("playinfo_v131.dat", "playinfo_v131.dat.boss"):
				status, data = await anyio.to_thread.run_sync(
					lambda: http(f"{config.http_base_url}/boss/p01/nsa/xyz/task/{name}")
				)
				assert status == 200 and data.startswith(b"boss")

			await run_flow(config, prudp_version, PID, PASSWORD)
			await run_flow(config, prudp_version, NASC_PID, NASC_PASSWORD)

			# NASC request with only a password, relayed by mitmproxy: matched
			# to the next login from the console's IP
			await anyio.to_thread.run_sync(lambda: nasc_login(
				config, {"action": "LOGIN", "passwd": IP_PASSWORD},
				{"X-Forwarded-For": "127.0.0.1"}
			))
			await run_flow(config, prudp_version, IP_PID, IP_PASSWORD)


def http_request(url: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
	"""Like http(), but returns (status, headers, body) for any status."""
	request = urllib.request.Request(url, headers=headers or {})
	try:
		with urllib.request.urlopen(request, timeout=5) as response:
			return response.status, response.headers, response.read()
	except urllib.error.HTTPError as e:
		return e.code, e.headers, e.read()


def http_request_post(url: str, body: bytes) -> tuple[int, dict, bytes]:
	request = urllib.request.Request(url, data=body)
	try:
		with urllib.request.urlopen(request, timeout=5) as response:
			return response.status, response.headers, response.read()
	except urllib.error.HTTPError as e:
		return e.code, e.headers, e.read()


def run_admin(config_path: Path, *args: str) -> str:
	output = io.StringIO()
	with contextlib.redirect_stdout(output):
		assert admin.main(["--config", str(config_path), *args]) == 0
	return output.getvalue()


async def run_extras() -> None:
	with tempfile.TemporaryDirectory() as tmp:
		tmp = Path(tmp)
		(tmp / "boss").mkdir()
		(tmp / "boss" / "allbadge_v131.dat.boss").write_bytes(b"boss" + bytes(1000))

		config_path = tmp / "config.json"
		config_path.write_text(json.dumps({
			"public_host": "127.0.0.1", "bind_host": "127.0.0.1",
			"auth_port": 49430, "secure_port": 49431, "http_port": 48130,
			"kerberos_password": "test", "data_dir": "data", "boss_dir": "boss",
		}))
		config = load_config(config_path)

		async with run_servers(config):
			# nex-keys.txt from the SD card, added while the server is running
			(tmp / "nex-keys.txt").write_text(
				f"{KEYS_PID}:{KEYS_PASSWORD}\n"
				f"{BIG_PID - 2**32}:{BIG_PASSWORD}\n"
				f"{KEY_PID}:{derive_user_key(KEY_PASSWORD, KEY_PID).hex()}\n"
			)
			for pid, password in [(KEYS_PID, KEYS_PASSWORD), (BIG_PID, BIG_PASSWORD), (KEY_PID, KEY_PASSWORD)]:
				await run_flow(config, 1, pid, password)

			# NEX token (what Badge Arcade gets from the NNID account server): the
			# server picks the password, and the game logs in with it
			def nex_token(pid: str, ip: str = "") -> tuple[int, str]:
				body = f"pid={pid}&title_id=0004000000153500&game_server_id=00134600&ip={ip}".encode()
				status, _, data = http_request_post(f"{config.http_base_url}/nex_token", body)
				return status, data.decode()

			status, xml = await anyio.to_thread.run_sync(lambda: nex_token(str(TOKEN_PID)))
			assert status == 200
			fields = dict(re.findall(r"<(\w+)>([^<]*)</\1>", xml))
			assert fields["host"] == "127.0.0.1" and fields["port"] == str(config.auth_port)
			assert fields["pid"] == str(TOKEN_PID) and len(fields["nex_password"]) == 16
			await run_flow(config, 1, TOKEN_PID, fields["nex_password"])

			# What the 3DS does: LoginEx with its NNID PID and the token from the
			# reply. The server finds the password through the token.
			auth_info = authentication.AuthenticationInfo()
			auth_info.token = fields["token"]
			await run_flow(config, 1, TOKEN_LOGIN_PID, fields["nex_password"], auth_info)

			# Same password next time, and the known PID is used if the proxy missed it
			status, xml_again = await anyio.to_thread.run_sync(lambda: nex_token(""))
			assert status == 200 and dict(re.findall(r"<(\w+)>([^<]*)</\1>", xml_again))["nex_password"] == fields["nex_password"]

			# Two consoles: when the proxy misses the PID (it restarted after the
			# consoles' friends logins), each console's IP gets its own PID back,
			# and an unknown IP gets the most recent console
			pid_of = lambda xml: dict(re.findall(r"<(\w+)>([^<]*)</\1>", xml))["pid"]
			other_pid = TOKEN_PID + 1
			for pid, ip in ((TOKEN_PID, "192.168.0.20"), (other_pid, "192.168.0.21")):
				status, _ = await anyio.to_thread.run_sync(lambda: nex_token(str(pid), ip))
				assert status == 200
			for ip, expected in (("192.168.0.20", TOKEN_PID), ("192.168.0.21", other_pid), ("192.168.0.99", other_pid)):
				status, xml_ip = await anyio.to_thread.run_sync(lambda: nex_token("", ip))
				assert status == 200 and pid_of(xml_ip) == str(expected), (ip, xml_ip)

			# Unknown account: a successful call with InvalidUsername in the result
			async with rmc.connect(client_settings(1), "127.0.0.1", config.auth_port) as client:
				response = await authentication.AuthenticationClient(client).login("1750009999")
				assert response.result.name() == "RendezVous::InvalidUsername"
				assert response.pid == 0 and response.ticket == b""

			# SpotPass files can be revalidated instead of downloaded again
			url = f"{config.http_base_url}/boss/p01/nsa/OvbmGLZ9senvgV3K/data/allbadge_v131.dat"
			status, headers, body = await anyio.to_thread.run_sync(lambda: http_request(url))
			assert status == 200 and len(body) == 1004
			for conditional in ({"If-None-Match": headers["ETag"]}, {"If-Modified-Since": headers["Last-Modified"]}):
				status, _, body = await anyio.to_thread.run_sync(lambda: http_request(url, conditional))
				assert status == 304 and body == b""
			status, _, _ = await anyio.to_thread.run_sync(lambda: http_request(url, {"If-None-Match": '"other"'}))
			assert status == 200

			# Save tools
			output = run_admin(config_path, "saves")
			assert f"PID {KEYS_PID}" in output and "version 2" in output

			output = run_admin(config_path, "backup")
			with zipfile.ZipFile(output.split("Backup saved to ", 1)[1].strip()) as archive:
				names = archive.namelist()
			assert "badge_arcade.db" in names and any(name.startswith("objects/") for name in names)

			output = run_admin(config_path, "reset", str(KEYS_PID), "--yes")
			assert "Removed 1 save object(s)" in output
			assert f"PID {KEYS_PID}" not in run_admin(config_path, "saves")

			# After a reset the game goes through first-time setup again
			await run_flow(config, 1, KEYS_PID, KEYS_PASSWORD)


def test_prudp_v1():
	anyio.run(run_all, 1)


def test_prudp_v0():
	anyio.run(run_all, 0)


def test_accounts_caching_and_save_tools():
	anyio.run(run_extras)


def test_game_date():
	from badge_arcade.protocols.auth import AuthenticationServer
	from badge_arcade.storage import Storage

	with tempfile.TemporaryDirectory() as tmp:
		storage = Storage(Path(tmp))
		try:
			s = settings.default()
			real = AuthenticationServer(s, Config(base_dir=Path(tmp)), storage).connection_data().server_time
			assert real.year() >= 2024

			config = Config(game_date="2022-12-30", base_dir=Path(tmp))
			fixed = AuthenticationServer(s, config, storage).connection_data().server_time
			assert (fixed.year(), fixed.month(), fixed.day()) == (2022, 12, 30)
			assert fixed.hour() == real.hour() or fixed.minute() == 0  # real time of day
		finally:
			storage.close()

	# Checked when the config is loaded
	with tempfile.TemporaryDirectory() as tmp:
		path = Path(tmp) / "config.json"
		path.write_text(json.dumps({"game_date": "2022-13-45"}))
		with pytest.raises(ValueError):
			load_config(path)


if __name__ == "__main__":
	import logging
	logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
	logging.getLogger("nintendo").setLevel(logging.WARNING)
	test_prudp_v1()
	test_prudp_v0()
	test_accounts_caching_and_save_tools()
	print("All end-to-end tests passed")

"""NEX settings and Kerberos helpers shared by the auth and secure servers."""

from nintendo.nex import common, kerberos, settings
import re
import secrets

from .config import Config

# The secure server's "PID" in the station URL handed out by the auth server
SECURE_SERVER_PID = 2


def make_settings(config: Config) -> settings.Settings:
	s = settings.default()
	s.configure(config.access_key, config.nex_version)

	# Badge Arcade uses PRUDP v1. "prudp.version = 2" makes NintendoClients
	# auto-detect v0/v1 per packet, so either works.
	s["prudp.version"] = 2
	# NintendoClients answers with min(this, console's minor version) and
	# rejects consoles above it. Pretendo's server echoes the console's value,
	# so set the maximum to do the same. (Only negotiation and structure
	# headers depend on it, and those are always on here.)
	s["prudp.minor_version"] = 0xFF
	s["prudp.fragment_size"] = 962  # Same as NintendoClients' 3DS preset

	# NEX 3.5+ prefixes structures with a version + length header
	s["nex.struct_header"] = 1

	# Be more tolerant of Wi-Fi packet loss than the library defaults
	s["prudp.resend_timeout"] = 1.5
	s["prudp.resend_limit"] = 5

	for name, value in config.nex_settings.items():
		s[name] = value
	return s


def derive_user_key(password: str, pid: int) -> bytes:
	"""Kerberos key derivation used by the 3DS (65000 + pid % 1024 MD5 rounds)."""
	return kerberos.KeyDerivationOld(65000, 1024).derive_key(password.encode(), pid)


def user_key_from_secret(secret: str, pid: int) -> bytes:
	"""Accepts a NEX password or a Kerberos key already derived from it.

	3DS NEX passwords are 16 characters, so 32 hex digits can only be a key
	(the NEX Wireshark dissector rewrites nex-keys.txt into that form).
	"""
	if re.fullmatch(r"[0-9a-fA-F]{32}", secret):
		return bytes.fromhex(secret)
	return derive_user_key(secret, pid)


def secure_server_key(config: Config) -> bytes:
	return derive_user_key(config.kerberos_password, SECURE_SERVER_PID)


def secure_station_url(config: Config) -> common.StationURL:
	return common.StationURL(
		"prudps", address=config.public_host, port=config.secure_port,
		CID=1, PID=SECURE_SERVER_PID, sid=1, stream=10, type=2
	)


def build_ticket(
	s: settings.Settings, config: Config, pid: int, user_key: bytes,
	target: int = SECURE_SERVER_PID
) -> bytes:
	"""Builds the ticket returned by Login / RequestTicket.

	The outer ticket is encrypted with the user's key so only the console can
	read it. It contains the session key and an inner ticket, encrypted with
	the secure server's key, which the console presents when connecting to
	the secure server.
	"""
	session_key = secrets.token_bytes(s["kerberos.key_size"])

	internal = kerberos.ServerTicket()
	internal.timestamp = common.DateTime.now()
	internal.source = pid
	internal.session_key = session_key

	ticket = kerberos.ClientTicket()
	ticket.session_key = session_key
	ticket.target = target
	ticket.internal = internal.encrypt(secure_server_key(config), s)
	return ticket.encrypt(user_key, s)

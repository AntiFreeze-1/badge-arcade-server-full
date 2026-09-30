"""The server and the proxy as background processes, and what the manager checks about them."""

from pathlib import Path
import json
import os
import re
import socket
import subprocess
import sys
import time

import hotspot

from . import LOG_DIR, PID_FILE, SERVER_DIR

SERVER_EVENTS = re.compile(r"Ready|Login from|SpotPass|ChangeMeta|meta changes|Sent data ID|disconnected|ERROR|Traceback|Error")
# "does not trust": the 3DS rejected the proxy's certificate, i.e. the NoSSL patch isn't active
PROXY_EVENTS = re.compile(r"Redirecting|Answering|Passing|listening|does not trust|Traceback|Error|error")


class Service:
	"""The server or the proxy, started by the manager (no console window) or found running."""

	def __init__(self, name: str, args, port: int):
		self.name = name
		self.args = args  # called at each start: the arguments depend on the connection mode
		self.port = port
		self.log = LOG_DIR / f"{name}.log"
		self.process: subprocess.Popen | None = None
		self.started_at = 0.0  # when this window started it (0: earlier, or elsewhere)

	def pid(self) -> int | None:
		if self.process and self.process.poll() is None:
			return self.process.pid
		try:
			pid = json.loads(PID_FILE.read_text()).get(self.name)
		except (FileNotFoundError, ValueError):
			return None
		return pid if pid and is_python(pid) else None

	def remember_pid(self, pid: int | None) -> None:
		try:
			pids = json.loads(PID_FILE.read_text())
		except (FileNotFoundError, ValueError):
			pids = {}
		pids[self.name] = pid
		LOG_DIR.mkdir(exist_ok=True)
		PID_FILE.write_text(json.dumps(pids))

	def listening(self) -> bool:
		with socket.socket() as s:
			s.settimeout(0.3)
			return s.connect_ex(("127.0.0.1", self.port)) == 0

	def start(self) -> None:
		if self.listening():
			raise RuntimeError(f"The {self.name} is already running.")
		LOG_DIR.mkdir(exist_ok=True)
		log = open(self.log, "ab")
		env = dict(os.environ, PYTHONUNBUFFERED="1")
		# python.exe even when the manager runs under pythonw.exe, so the proxy's own
		# helper process doesn't open a console window
		python = Path(sys.executable)
		if python.stem.lower() == "pythonw" and python.with_name("python.exe").exists():
			python = python.with_name("python.exe")
		self.started_at = time.time()
		self.process = subprocess.Popen(
			[str(python), *self.args()], cwd=SERVER_DIR, stdout=log, stderr=subprocess.STDOUT,
			stdin=subprocess.DEVNULL, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
		)
		self.remember_pid(self.process.pid)

	def stop(self) -> bool:
		"""Stops it if the manager started it (now or last time). False if it was started elsewhere."""
		pid = self.pid()
		if not pid:
			self.remember_pid(None)
			return False
		subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
		self.process = None
		self.remember_pid(None)
		return True


def public_host_fixed() -> bool:
	"""Whether server/config.json sets public_host to an address (instead of "auto", which
	makes the server give each 3DS the address it can reach)."""
	try:
		value = json.loads((SERVER_DIR / "config.json").read_text(encoding="utf-8")).get("public_host", "auto")
	except (OSError, ValueError):
		return False
	return value not in ("auto", "", None)


def port_open(ip: str, port: int) -> bool:
	"""Whether something accepts TCP connections on ip:port (this PC's own addresses)."""
	with socket.socket() as s:
		s.settimeout(0.3)
		return s.connect_ex((ip, port)) == 0


def hotspot_address() -> str | None:
	"""The hotspot's address while the hotspot is on (whatever the connection mode)."""
	ip = hotspot.hotspot_ip()
	with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
		try:
			s.bind((ip, 0))  # only works while the hotspot has the address
			return ip
		except OSError:
			return None


def is_python(pid: int) -> bool:
	"""Whether a process ID still belongs to a Python process (IDs get reused)."""
	result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
		creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
	return "python" in result.stdout.lower()


def tidy(line: str) -> str:
	"""'2026-09-27 13:51:42,580 [INFO] badge_arcade.protocols.auth: Login...' -> '13:51:42  Login...'."""
	match = re.match(r"\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ \[\w+\] [\w.]+: (.*)", line)
	line = f"{match.group(1)}  {match.group(2)}" if match else line.strip()
	if "does not trust" in line:
		line += ("\n    -> The 3DS checked the certificate, so the NoSSL patch isn't active: put "
			"luma/sysmodules/0004013000002F02.ips on the SD card and turn on \"Enable game patching\" in Luma.")
	return line

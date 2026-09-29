"""Hotspot mode: the 3DS joins this PC's Windows Mobile Hotspot and needs no
proxy settings.

  python mitm/hotspot.py on        start the hotspot and point the Nintendo hosts at it
  python mitm/hotspot.py off       stop the hotspot and remove the hosts entries
  python mitm/hotspot.py status    hotspot name, password, clients and a DNS check

Windows' hotspot (Internet Connection Sharing) gives its clients the PC as
their DNS server, and answers from the PC's own resolver, which reads the
hosts file. So a block in the hosts file points the few hosts the proxy
addon handles (nintendo_hosts.py) at the hotspot's address, where
start_mitm.py --hotspot listens on ports 443 and 80. Everything else the 3DS
does goes straight to the internet.

Changing the hosts file and the firewall needs admin rights, so "on" and "off"
show one UAC prompt. "on" also adds firewall rules for the server and proxy
ports. The 3DS still needs the NoSSL patch (see setup_mitm.py).

The manager window (../../manager.py) uses these functions. Windows only.
"""

from pathlib import Path
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import time

from nintendo_hosts import HANDLED_HOSTS

HERE = Path(__file__).resolve().parent
SERVER_CONFIG = HERE.parent / "config.json"
LOG_FILE = HERE.parent / "logs" / "hotspot.log"
HOSTS_FILE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
BEGIN = "# BEGIN Badge Arcade hotspot (added by the Badge Arcade server; removed when hotspot mode stops)"
END = "# END Badge Arcade hotspot"
DEFAULT_HOTSPOT_IP = "192.168.137.1"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Asks Windows' tethering API (the same one as Settings > Mobile hotspot) to
# start, stop or describe the hotspot, and prints the result as JSON
TETHERING_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$methods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
$asTaskOperation = $methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$asTaskAction = $methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
function Await($operation, [Type]$type) {
	$task = $asTaskOperation.MakeGenericMethod($type).Invoke($null, @($operation))
	$task.Wait(-1) | Out-Null
	$task.Result
}
function AwaitAction($action) { $asTaskAction.Invoke($null, @($action)).Wait(-1) | Out-Null }

$null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
$null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
$resultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]

$connection = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
if (-not $connection) { throw 'This PC is not connected to the internet, and Windows only starts the hotspot when it has a connection to share.' }
$manager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($connection)

$action = '__ACTION__'
if ($action -eq 'start' -and $manager.TetheringOperationalState -ne 'On') {
	# The 3DS only has 2.4 GHz Wi-Fi (older Windows versions can't choose the band).
	# Only when needed: Windows may reset the name and password when it applies this.
	try {
		$config = $manager.GetCurrentAccessPointConfiguration()
		if ([string]$config.Band -ne 'TwoPointFourGigahertz' -and $config.IsBandSupported('TwoPointFourGigahertz')) {
			$config.Band = 'TwoPointFourGigahertz'
			AwaitAction $manager.ConfigureAccessPointAsync($config)
		}
	} catch {}
	$result = Await $manager.StartTetheringAsync() $resultType
	if ([string]$result.Status -ne 'Success') { throw "Windows couldn't start the hotspot ($($result.Status)). $($result.AdditionalErrorMessage)" }
} elseif ($action -eq 'stop' -and $manager.TetheringOperationalState -ne 'Off') {
	$result = Await $manager.StopTetheringAsync() $resultType
	if ([string]$result.Status -ne 'Success') { throw "Windows couldn't stop the hotspot ($($result.Status)). $($result.AdditionalErrorMessage)" }
}

$config = $manager.GetCurrentAccessPointConfiguration()
$band = ''
try { $band = [string]$config.Band } catch {}
@{
	state = [string]$manager.TetheringOperationalState
	ssid = $config.Ssid
	passphrase = $config.Passphrase
	band = $band
	clients = $manager.ClientCount
} | ConvertTo-Json -Compress
"""


# ----- the hotspot -----

def powershell(script: str) -> subprocess.CompletedProcess:
	return subprocess.run(
		["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
		capture_output=True, text=True, creationflags=NO_WINDOW,
	)


def tethering(action: str = "status") -> dict:
	"""Starts ("start"), stops ("stop") or describes ("status") the Mobile Hotspot."""
	result = powershell(TETHERING_SCRIPT.replace("__ACTION__", action))
	if result.returncode != 0:
		# The first line of PowerShell's error text is the message
		lines = result.stderr.strip().splitlines()
		raise RuntimeError(lines[0] if lines else "Windows' hotspot settings couldn't be read.")
	return json.loads(result.stdout.strip().splitlines()[-1])


def hotspot_ip() -> str:
	"""The PC's address on its hotspot (Internet Connection Sharing's ScopeAddress)."""
	try:
		import winreg
		with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters") as key:
			return winreg.QueryValueEx(key, "ScopeAddress")[0] or DEFAULT_HOTSPOT_IP
	except OSError:
		return DEFAULT_HOTSPOT_IP


def wifi_channel() -> int | None:
	"""The channel of the PC's own Wi-Fi connection. A hotspot shared from the same
	adapter uses it too, and the 3DS can't see 5 GHz channels (above 14)."""
	result = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, creationflags=NO_WINDOW)
	match = re.search(r"^\s*Channel\s*:\s*(\d+)", result.stdout, re.MULTILINE)
	return int(match.group(1)) if match else None


# ----- the hosts file -----

def strip_block(text: str) -> str:
	"""The hosts file text without our block."""
	return re.sub(rf"(\r?\n)?{re.escape(BEGIN)}.*?{re.escape(END)}[^\n]*(\r?\n)?", lambda m: m.group(1) or "", text, flags=re.DOTALL)


def with_block(text: str, ip: str) -> str:
	"""The hosts file text with our block, pointing the handled hosts at ip."""
	text = strip_block(text)
	if text and not text.endswith("\n"):
		text += "\r\n"
	lines = [BEGIN, *(f"{ip}\t{host}" for host in sorted(HANDLED_HOSTS)), END]
	return text + "\r\n".join(lines) + "\r\n"


def block_ip(text: str) -> str | None:
	"""The address our block points at, or None if there's no block."""
	match = re.search(rf"{re.escape(BEGIN)}\s*\n\s*([\d.]+)\s", text)
	return match.group(1) if match else None


def read_hosts() -> str:
	# latin-1 round-trips whatever bytes are in there
	return HOSTS_FILE.read_text(encoding="latin-1") if HOSTS_FILE.exists() else ""


def hosts_ip() -> str | None:
	return block_ip(read_hosts())


def write_hosts(ip: str | None) -> None:
	"""Adds (ip) or removes (None) our block. Needs admin."""
	text = read_hosts()
	new = with_block(text, ip) if ip else strip_block(text)
	if new != text:
		HOSTS_FILE.write_text(new, encoding="latin-1", newline="")
	subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=NO_WINDOW)


# ----- the firewall -----

def firewall_rules() -> list[tuple[str, str, str]]:
	"""(name, protocol, ports) for the server and proxy, from the server's config."""
	try:
		config = json.loads(SERVER_CONFIG.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		config = {}
	http, auth, secure = config.get("http_port", 8080), config.get("auth_port", 59400), config.get("secure_port", 59401)
	return [
		("Badge Arcade server and proxy (TCP)", "TCP", f"80,443,{http},8083"),
		("Badge Arcade server (UDP)", "UDP", f"{auth},{secure}"),
	]


def firewall_rule_exists(name: str) -> bool:
	result = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
		capture_output=True, text=True, creationflags=NO_WINDOW)
	return result.returncode == 0


def add_firewall_rules() -> None:
	"""Allows the server's and proxy's ports in, on every network type. Needs admin."""
	for name, protocol, ports in firewall_rules():
		subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"],
			capture_output=True, creationflags=NO_WINDOW)
		subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={name}", "dir=in", "action=allow",
			f"protocol={protocol}", f"localport={ports}", "profile=any"], check=True, capture_output=True, creationflags=NO_WINDOW)


def firewall_ready() -> bool:
	return all(firewall_rule_exists(name) for name, _, _ in firewall_rules())


# ----- admin steps -----

def run_elevated(*args: str) -> None:
	"""Runs this script with admin rights (one UAC prompt) and waits for it."""
	python = Path(sys.executable)
	if python.stem.lower() == "pythonw" and python.with_name("python.exe").exists():
		python = python.with_name("python.exe")
	arguments = subprocess.list2cmdline([str(Path(__file__)), *args])

	def quote(text: str) -> str:
		return "'" + text.replace("'", "''") + "'"

	result = powershell(
		f"try {{ $p = Start-Process -FilePath {quote(str(python))} -ArgumentList {quote(arguments)} "
		"-Verb RunAs -WindowStyle Hidden -Wait -PassThru } catch { exit 1223 }; exit $p.ExitCode"
	)
	if result.returncode == 1223:
		raise RuntimeError("Windows didn't give admin rights (the UAC prompt was declined), so nothing was changed.")
	if result.returncode != 0:
		raise RuntimeError(f"Changing the hosts file or firewall failed. See {LOG_FILE}.")


def elevated_main(action: str, ip: str | None) -> int:
	"""The part that runs as admin: hosts file, DNS cache, firewall."""
	try:
		if action == "apply":
			write_hosts(ip)
			add_firewall_rules()
		else:
			write_hosts(None)
		return 0
	except Exception as e:  # logged for the non-admin side to point at
		LOG_FILE.parent.mkdir(exist_ok=True)
		with open(LOG_FILE, "a", encoding="utf-8") as f:
			f.write(f"{action}: {e!r}\n")
		return 1


# ----- checking it works -----

def dns_query(name: str, query_id: int) -> bytes:
	header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)  # recursion desired, one question
	question = b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\0"
	return header + question + struct.pack(">HH", 1, 1)  # type A, class IN


def skip_name(data: bytes, offset: int) -> int:
	while True:
		length = data[offset]
		if length == 0:
			return offset + 1
		if length & 0xC0 == 0xC0:  # compressed: a pointer ends the name
			return offset + 2
		offset += 1 + length


def a_records(reply: bytes, query_id: int) -> list[str]:
	"""The IPv4 addresses in a DNS reply."""
	reply_id, _, questions, answers, _, _ = struct.unpack(">HHHHHH", reply[:12])
	if reply_id != query_id:
		return []
	offset = 12
	for _ in range(questions):
		offset = skip_name(reply, offset) + 4
	addresses = []
	for _ in range(answers):
		offset = skip_name(reply, offset)
		record_type, _, _, length = struct.unpack(">HHIH", reply[offset:offset + 10])
		offset += 10
		if record_type == 1 and length == 4:
			addresses.append(socket.inet_ntoa(reply[offset:offset + 4]))
		offset += length
	return addresses


def lookup(name: str, dns_server: str, timeout: float = 2.0) -> list[str]:
	"""Asks dns_server (the hotspot's DNS, as the 3DS would) for name's addresses."""
	query_id = secrets.randbelow(0x10000)
	with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
		s.settimeout(timeout)
		s.sendto(dns_query(name, query_id), (dns_server, 53))
		return a_records(s.recv(4096), query_id)


def check_dns(ip: str, attempts: int = 1) -> str | None:
	"""None if the hotspot sends Badge Arcade's hosts to ip, else what's wrong.
	The hotspot's DNS takes a few seconds to start, hence attempts."""
	for attempt in range(attempts):
		try:
			found = lookup("nasc.nintendowifi.net", ip)
			break
		except OSError as e:
			if attempt == attempts - 1:
				return f"The hotspot's DNS at {ip} didn't answer ({e})."
			time.sleep(1)
	if ip not in found:
		return (f"The hotspot's DNS answered {', '.join(found) or 'nothing'} for nasc.nintendowifi.net instead of {ip}, "
			"so Windows isn't using the hosts file for the hotspot. Use proxy mode instead.")
	return None


# ----- on / off / status -----

def status(dns_attempts: int = 1) -> dict:
	"""What the manager shows: the hotspot, the hosts entries and whether they work."""
	ip = hotspot_ip()
	info = {"ip": ip, "hosts_ip": hosts_ip(), "state": "Unknown", "ssid": "", "passphrase": "", "clients": 0,
		"band": "", "error": None, "dns_problem": None, "warning": None}
	try:
		info.update(tethering("status"))
	except (RuntimeError, ValueError) as e:
		info["error"] = str(e)
	if info["state"] == "On" and info["hosts_ip"]:
		info["dns_problem"] = check_dns(ip, dns_attempts)
	channel = wifi_channel()
	if channel and channel > 14 and info["band"] != "TwoPointFourGigahertz":
		info["warning"] = (f"This PC's Wi-Fi is on a 5 GHz channel ({channel}), so the hotspot probably is too, and the "
			"3DS can only see 2.4 GHz networks. Connect the PC to a 2.4 GHz network or by cable.")
	return info


def turn_on() -> dict:
	ip = hotspot_ip()
	if hosts_ip() != ip or not firewall_ready():
		run_elevated("elevated", "apply", ip)
	tethering("start")
	return status(dns_attempts=8)


def turn_off(stop_hotspot: bool = True) -> dict:
	if stop_hotspot:
		tethering("stop")
	if hosts_ip():
		run_elevated("elevated", "clear")
	return status()


def main() -> int:
	if len(sys.argv) >= 3 and sys.argv[1] == "elevated":
		return elevated_main(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
	action = sys.argv[1] if len(sys.argv) > 1 else "status"
	if action not in ("on", "off", "status"):
		print(__doc__)
		return 2
	info = {"on": turn_on, "off": turn_off, "status": status}[action]()
	print(f"Hotspot:     {info['state']}" + (f"  ({info['error']})" if info["error"] else ""))
	print(f"Name:        {info['ssid']}")
	print(f"Password:    {info['passphrase']}")
	print(f"Clients:     {info['clients']}")
	print(f"Address:     {info['ip']}")
	print("Hosts file:  " + (f"Nintendo hosts point at {info['hosts_ip']}" if info["hosts_ip"] else "no entries"))
	if info["state"] == "On" and info["hosts_ip"]:
		print(f"DNS check:   {info['dns_problem'] or 'OK, the 3DS will reach this PC'}")
	if info["warning"]:
		print(f"Warning:     {info['warning']}")
	return 0


if __name__ == "__main__":
	sys.exit(main())

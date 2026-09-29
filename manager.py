"""Badge Arcade Manager: one window for setup, the server, the proxy or
hotspot, the machines, free plays, letters and saves.

  python manager.py        (or double-click "Badge Arcade Manager.bat")

Everything it does can also be done with the command-line tools it uses:
install.py, server/mitm/hotspot.py, spotpass-letter/serve.py,
spotpass-letter/custom_week.py and server's "python -m badge_arcade.admin".
"""

from collections import Counter
from pathlib import Path
import datetime
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent
SERVER_DIR = ROOT / "server"
LETTER_DIR = ROOT / "spotpass-letter"
LOG_DIR = SERVER_DIR / "logs"
PID_FILE = LOG_DIR / "manager_pids.json"
SETTINGS_FILE = ROOT / "manager_settings.json"
sys.path[:0] = [str(LETTER_DIR), str(SERVER_DIR), str(SERVER_DIR / "mitm")]

import hotspot  # noqa: E402
import install  # noqa: E402
import letters  # noqa: E402
import make_letter  # noqa: E402
import serve  # noqa: E402
import update  # noqa: E402
from custom_week import Builder, daily_lineup, series as series_of  # noqa: E402
from badge_arcade import admin  # noqa: E402
from badge_arcade.config import lan_address, load_config  # noqa: E402
from badge_arcade.storage import Storage  # noqa: E402

# Internal series codes -> names (from the badge files; a few are best guesses)
SERIES_NAMES = {
	"Amiibo": "amiibo", "Animal": "Animal Crossing", "AnimalFes": "Animal Crossing: amiibo Festival",
	"BTCenter": "BTCenter", "CatMario": "Cat Mario", "DotHard": "Retro consoles (pixel art)",
	"Emblem": "Fire Emblem", "FcRemix": "NES Remix", "Kirby": "Kirby", "MH": "Monster Hunter",
	"MdWario": "WarioWare", "MroKrt8": "Mario Kart 8", "MroMkr": "Super Mario Maker",
	"MroPrt": "Mario Party", "MroRPGMix": "Mario RPGs", "MroSMB": "Super Mario Bros.",
	"MroTnsU": "Mario Tennis: Ultra Smash", "Nikki": "Swapnote (Nikki)", "Pkm": "Pikmin",
	"PokeDot": "Pokémon (pixel art)", "PokeExt": "Pokémon (extra)", "Pokemon": "Pokémon",
	"Rhythm": "Rhythm Heaven", "Rockman": "Mega Man", "SFZero": "Star Fox Zero", "Spltn": "Splatoon",
	"Tank": "Tank Troopers", "Tomodachi": "Tomodachi Life", "YshWW": "Yoshi's Woolly World",
	"Yokai": "Yo-kai Watch", "ZelBoW": "Zelda: Breath of the Wild", "ZelDisk": "Zelda (Famicom Disk)",
	"ZelHero": "Zelda: heroes", "ZelTPri": "Zelda: Twilight Princess", "ZelTri": "Zelda: Tri Force Heroes",
	"ZelWndHD": "Zelda: Wind Waker HD", "Zelda30th": "Zelda 30th Anniversary",
}

UPDATE_CHECK_INTERVAL = 86400  # seconds between automatic update checks

SERVER_EVENTS = re.compile(r"Ready|Login from|SpotPass|ChangeMeta|meta changes|Sent data ID|disconnected|ERROR|Traceback|Error")
# "does not trust": the 3DS rejected the proxy's certificate, i.e. the NoSSL patch isn't active
PROXY_EVENTS = re.compile(r"Redirecting|Answering|Passing|listening|does not trust|Traceback|Error|error")


def series_name(code: str) -> str:
	return SERIES_NAMES.get(code, code)


def badge_name(prize: str) -> str:
	"""Pr_MH_Chara_Rathalos00 -> "Chara Rathalos00"."""
	parts = prize.split("_")[2:] or prize.split("_")[1:]
	return " ".join(p for p in parts if p != "Sep")


# ----- background services -----

class Service:
	"""The server or the proxy, started by the manager (no console window) or found running."""

	def __init__(self, name: str, args, port: int):
		self.name = name
		self.args = args  # called at each start: the arguments depend on the connection mode
		self.port = port
		self.log = LOG_DIR / f"{name}.log"
		self.process: subprocess.Popen | None = None

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


# ----- the window -----

class Manager(tk.Tk):
	def __init__(self):
		super().__init__()
		self.title(f"Badge Arcade Manager {update.current_version()}")
		self.geometry("1000x780")
		self.minsize(860, 560)
		try:
			self.key = serve.boss_key()
		except (OSError, ValueError):
			self.key = None  # no boot9.bin yet; the SpotPass tabs explain
		install.create_config()  # first run: config.json with a random kerberos_password
		config = load_config(serve.SERVER_CONFIG)
		self.settings = load_settings()
		self.server = Service("server", lambda: ["-m", "badge_arcade", "config.json"]
			+ (["--public-host", self.live_hotspot_ip()] if self.live_hotspot_ip() else []), config.http_port)
		self.proxy = Service("proxy", lambda: ["mitm/start_mitm.py"]
			+ (["--hotspot", self.live_hotspot_ip()] if self.live_hotspot_ip() else []), 8083)
		self.hotspot_info: dict = {}
		self.hotspot_busy = False
		self.builder: Builder | None = None
		self.weeks: list[serve.Week] = []
		self.log_offsets: dict[Path, int] = {}

		style = ttk.Style(self)
		if "vista" in style.theme_names():
			style.theme_use("vista")
		style.configure("Big.TLabel", font=("Segoe UI", 11, "bold"))
		style.configure("Hint.TLabel", foreground="#666")

		notebook = ttk.Notebook(self)
		notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))
		for title, build in (("Setup", self.build_setup_tab), ("Server", self.build_server_tab), ("Machines", self.build_machines_tab),
				("Build a week", self.build_builder_tab), ("Free plays", self.build_plays_tab), ("Letters", self.build_letters_tab), ("Saves", self.build_saves_tab)):
			frame = ttk.Frame(notebook, padding=10)
			notebook.add(frame, text=title)
			build(frame)
		notebook.bind("<<NotebookTabChanged>>", lambda e: self.on_tab(notebook.tab(notebook.select(), "text")))
		if all(check.ok for check in install.checklist()):
			notebook.select(1)  # set up already: open on the Server tab

		self.status = tk.StringVar(value="Ready.")
		ttk.Label(self, textvariable=self.status, anchor="w", padding=(10, 4)).pack(fill="x")

		self.protocol("WM_DELETE_WINDOW", self.on_close)
		self.refresh_services()
		self.refresh_status()
		self.after(1000, self.poll_logs)
		self.after(500, self.check_leftover_hosts)
		self.after(2000, self.check_for_update)

	# --- helpers ---

	def background(self, message: str, work, done=None) -> None:
		"""Runs work() off the UI thread, then done(result) on it. Errors are shown in a dialog."""
		self.status.set(message)
		self.config(cursor="watch")

		def run():
			try:
				result, error = work(), None
			except Exception as e:  # shown to the user
				result, error = None, e
			self.after(0, finish, result, error)

		def finish(result, error):
			self.config(cursor="")
			if error:
				self.status.set("Something went wrong.")
				messagebox.showerror("Badge Arcade Manager", str(error))
			else:
				self.status.set("Done.")
				if done:
					done(result)

		threading.Thread(target=run, daemon=True).start()

	def on_tab(self, title: str) -> None:
		if title in ("Machines", "Build a week", "Free plays"):
			problem = self.missing_files()
			if problem:
				self.status.set(f"Not set up yet: {problem} See README.md.")
				return
		if title == "Letters":
			self.refresh_letters()
			if self.key is None:
				self.status.set(f"Sending letters needs {self.missing_files()} See README.md.")
		elif title == "Machines":
			self.refresh_weeks()
		elif title == "Build a week" and self.builder is None:
			self.load_builder()
		elif title == "Free plays":
			self.refresh_plays()
		elif title == "Saves":
			self.refresh_saves()
		elif title == "Setup":
			self.refresh_checklist()

	# --- Setup tab ---

	def build_setup_tab(self, tab: ttk.Frame) -> None:
		self.update_banner = ttk.Frame(tab, padding=(0, 0, 0, 8))
		self.update_text = ttk.Label(self.update_banner, style="Big.TLabel")
		self.update_text.pack(side="left")
		ttk.Button(self.update_banner, text="Update now", command=lambda: self.install_update(ask=True)).pack(side="left", padx=6)
		ttk.Button(self.update_banner, text="What's new", command=lambda: webbrowser.open(
			(self.settings.get("update_release") or {}).get("page_url") or f"https://github.com/{update.REPOSITORY}/releases")
		).pack(side="left")

		checks = ttk.LabelFrame(tab, text="Checklist", padding=10)
		checks.pack(fill="x")
		self.checks_frame = checks
		self.checklist_frame = ttk.Frame(checks)
		self.checklist_frame.pack(fill="x")
		buttons = ttk.Frame(checks)
		buttons.pack(anchor="w", pady=(6, 0))
		ttk.Button(buttons, text="Refresh", command=self.refresh_checklist).pack(side="left")
		ttk.Button(buttons, text="Open project folder", command=lambda: os.startfile(ROOT)).pack(side="left", padx=6)
		ttk.Label(buttons, text="README.md explains where each file comes from.", style="Hint.TLabel").pack(side="left", padx=6)
		versions = ttk.Frame(checks)
		versions.pack(anchor="w", pady=(6, 0))
		ttk.Label(versions, text=f"Version {update.current_version()}").pack(side="left")
		ttk.Button(versions, text="Check for updates", command=lambda: self.check_for_update(manual=True)).pack(side="left", padx=6)
		self.auto_update = tk.BooleanVar(value=bool(self.settings.get("auto_update")))
		ttk.Checkbutton(versions, text="Install updates automatically when the manager opens", variable=self.auto_update,
			command=self.change_auto_update).pack(side="left", padx=6)

		connect = ttk.LabelFrame(tab, text="Connect the 3DS", padding=10)
		connect.pack(fill="both", expand=True, pady=(8, 0))
		self.connection = tk.StringVar(value=self.settings["connection"])
		ttk.Radiobutton(connect, text="Through this PC's Wi-Fi hotspot: no proxy settings on the 3DS (recommended)",
			variable=self.connection, value="hotspot", command=self.change_connection).pack(anchor="w")
		ttk.Radiobutton(connect, text="Through a proxy: the 3DS stays on your Wi-Fi and uses this PC as its proxy",
			variable=self.connection, value="proxy", command=self.change_connection).pack(anchor="w")

		# Hotspot
		self.hotspot_panel = ttk.Frame(connect, padding=(0, 8, 0, 0))
		row = ttk.Frame(self.hotspot_panel)
		row.pack(anchor="w")
		self.hotspot_button = ttk.Button(row, text="Turn on hotspot", command=self.toggle_hotspot)
		self.hotspot_button.pack(side="left")
		ttk.Button(row, text="Windows hotspot settings",
			command=lambda: os.startfile("ms-settings:network-mobilehotspot")).pack(side="left", padx=6)
		self.hotspot_state = ttk.Label(row)
		self.hotspot_state.pack(side="left", padx=6)
		values = ttk.Frame(self.hotspot_panel)
		values.pack(anchor="w", pady=(6, 0))
		ttk.Label(values, text="Network name:").grid(row=0, column=0, sticky="w")
		self.hotspot_ssid = ttk.Label(values, style="Big.TLabel")
		self.hotspot_ssid.grid(row=0, column=1, sticky="w", padx=(6, 24))
		ttk.Label(values, text="Password:").grid(row=0, column=2, sticky="w")
		self.hotspot_password = ttk.Label(values, style="Big.TLabel")
		self.hotspot_password.grid(row=0, column=3, sticky="w", padx=6)
		ttk.Label(self.hotspot_panel, text="Turning the hotspot on points Nintendo's servers at this PC for devices on "
			"the hotspot, using the hosts file, and allows the server through the firewall. Windows asks for admin "
			"rights for that. Turning it off removes the entries again.\n\nOn the 3DS: System Settings > Internet "
			"Settings > Connection Settings > New Connection > Manual Setup > Search for an Access Point. Pick the "
			"network above, enter the password, and leave Proxy Settings on No. Save, run the connection test, "
			"then open Badge Arcade. (Once only: copy the luma folder from server/mitm/sd-card to the SD card.)",
			style="Hint.TLabel", wraplength=940, justify="left").pack(anchor="w", pady=(6, 0))
		self.hotspot_warning = ttk.Label(self.hotspot_panel, foreground="#b35c00", wraplength=940, justify="left")
		self.hotspot_warning.pack(anchor="w", pady=(4, 0))

		# Proxy
		self.proxy_panel = ttk.Frame(connect, padding=(0, 8, 0, 0))
		values = ttk.Frame(self.proxy_panel)
		values.pack(anchor="w")
		ttk.Label(values, text="Proxy server:").grid(row=0, column=0, sticky="w")
		self.ip_label = ttk.Label(values, style="Big.TLabel")
		self.ip_label.grid(row=0, column=1, sticky="w", padx=(6, 24))
		ttk.Label(values, text="Port:").grid(row=0, column=2, sticky="w")
		ttk.Label(values, text=str(self.proxy.port), style="Big.TLabel").grid(row=0, column=3, sticky="w", padx=6)
		ttk.Label(self.proxy_panel, text="On the 3DS: System Settings > Internet Settings > Connection Settings > your "
			"connection > Change Settings > Proxy Settings > Yes > Detailed Setup. Enter the proxy server and port above, "
			"save, and run the connection test. (Once only: copy the luma folder from server/mitm/sd-card to the SD card.)",
			style="Hint.TLabel", wraplength=940, justify="left").pack(anchor="w", pady=(4, 0))
		self.ip_warning = ttk.Label(self.proxy_panel, foreground="#b35c00", wraplength=940)
		self.ip_warning.pack(anchor="w")

		self.show_connection_panel()
		self.refresh_checklist()

	# --- updates ---

	def check_for_update(self, manual: bool = False) -> None:
		"""Looks for a new release (at most once a day unless asked), then offers or installs it."""
		known = self.settings.get("update_release")
		if known and not update.is_newer(known["version"], update.current_version()):
			known = self.settings["update_release"] = None  # installed since
			save_settings(self.settings)
		if not manual and time.time() - self.settings.get("update_checked", 0) < UPDATE_CHECK_INTERVAL:
			self.show_update(known, install=True)
			return

		def work():
			try:
				return update.check()
			except update.UpdateError as e:
				if manual:
					raise RuntimeError(f"Couldn't check for updates: {e}.") from e
				return known  # offline: keep what the last check found

		def done(release):
			self.settings["update_checked"] = time.time()
			self.settings["update_release"] = release
			save_settings(self.settings)
			self.show_update(release, install=not manual)
			if release is None and manual:
				self.status.set(f"Version {update.current_version()} is the newest version.")

		self.background("Checking for updates...", work, done)

	def show_update(self, release: dict | None, install: bool) -> None:
		"""Shows the update banner; install: install it now if updates are set to install automatically."""
		if release is None:
			self.update_banner.pack_forget()
			return
		self.update_text.config(text=f"Version {release['version']} is available (you have {update.current_version()}).")
		self.update_banner.pack(fill="x", before=self.checks_frame)
		if install and self.settings.get("auto_update"):
			self.install_update(ask=False)

	def change_auto_update(self) -> None:
		self.settings["auto_update"] = self.auto_update.get()
		save_settings(self.settings)

	def install_update(self, ask: bool) -> None:
		release = self.settings.get("update_release")
		if not release:
			return
		ours = [s for s in (self.server, self.proxy) if s.pid() and s.listening()]
		if ask and not messagebox.askyesno("Badge Arcade Manager", f"Install version {release['version']}?\n\n"
				+ ("The server and proxy will be stopped. " if ours else "")
				+ "Your settings, saves, SpotPass files and keys are kept, and the replaced files are backed up. "
				"The manager closes afterwards; open it again to use the new version."):
			return

		def work():
			for service in ours:
				service.stop()
			for _ in range(50):  # give them a moment to let go of their ports
				if not update.running_services():
					break
				time.sleep(0.1)
			return update.apply(release)

		def done(summary):
			self.settings["update_release"] = None
			save_settings(self.settings)
			messagebox.showinfo("Badge Arcade Manager", f"{summary}\n\nThe manager will close now. Open it again "
				"to use the new version.")
			self.destroy()

		self.background(f"Installing version {release['version']}...", work, done)

	def refresh_checklist(self) -> None:
		for widget in self.checklist_frame.winfo_children():
			widget.destroy()
		for row, check in enumerate(install.checklist()):
			ttk.Label(self.checklist_frame, text="✔" if check.ok else "✘",
				foreground="#1a7f37" if check.ok else "#b3261e").grid(row=row, column=0, sticky="nw", padx=(0, 6))
			ttk.Label(self.checklist_frame, text=check.name, width=46).grid(row=row, column=1, sticky="nw")
			ttk.Label(self.checklist_frame, text=check.hint, style="Hint.TLabel", wraplength=420,
				justify="left").grid(row=row, column=2, sticky="nw", pady=1)
			if check.fix and not check.ok:
				ttk.Button(self.checklist_frame, text="Do it", command=lambda c=check: self.fix(c)).grid(row=row, column=3, padx=6)
			elif check.folder:
				ttk.Button(self.checklist_frame, text="Open folder",
					command=lambda c=check: self.open_folder(c.folder)).grid(row=row, column=3, padx=6)

	def fix(self, check: install.Check) -> None:
		work, message = {
			"packages": (install.install_packages, "Installing the Python packages..."),
			"config": (install.create_config, "Creating server/config.json..."),
			"proxy": (install.setup_proxy, "Setting up the proxy (downloads about 100 MB, a minute or two)..."),
		}[check.fix]
		self.background(message, work, lambda _: self.refresh_checklist())

	@staticmethod
	def open_folder(folder: Path) -> None:
		folder.mkdir(parents=True, exist_ok=True)
		os.startfile(folder)

	def show_connection_panel(self) -> None:
		hotspot_mode = self.settings["connection"] == "hotspot"
		(self.proxy_panel if hotspot_mode else self.hotspot_panel).pack_forget()
		(self.hotspot_panel if hotspot_mode else self.proxy_panel).pack(fill="x", anchor="w")
		if hotspot_mode:
			self.refresh_hotspot()

	def change_connection(self) -> None:
		mode = self.connection.get()
		if mode == self.settings["connection"]:
			return
		self.settings["connection"] = mode
		save_settings(self.settings)
		self.show_connection_panel()
		if mode == "proxy" and hotspot.hosts_ip():
			if messagebox.askyesno("Badge Arcade Manager", "Turn the hotspot off too, and remove its hosts entries?"):
				self.toggle_hotspot(turning_on=False)
				return  # restarts the services once it's off
		self.restart_services()

	# --- the hotspot ---

	def live_hotspot_ip(self) -> str | None:
		"""The hotspot's address, if the connection mode is hotspot and the hotspot is on."""
		return hotspot_address() if self.settings["connection"] == "hotspot" else None

	def refresh_hotspot(self) -> None:
		"""Reads the hotspot's state in the background (PowerShell takes a second)."""
		if self.hotspot_busy:
			return
		self.hotspot_busy = True

		def run():
			try:
				info = hotspot.status()
			except Exception as e:  # shown in the panel
				info = {"state": "Unknown", "error": str(e)}
			self.after(0, self.show_hotspot, info)

		threading.Thread(target=run, daemon=True).start()

	def show_hotspot(self, info: dict) -> None:
		self.hotspot_busy = False
		self.hotspot_info = info
		on = info.get("state") == "On" and info.get("hosts_ip")
		clients = info.get("clients", 0)
		self.hotspot_button.configure(text="Turn off hotspot" if on else "Turn on hotspot")
		self.hotspot_state.configure(text=f"On, {clients} device{'s' if clients != 1 else ''} connected" if on
			else "On, but the hosts entries are missing: turn it off and on again" if info.get("state") == "On"
			else "Off")
		self.hotspot_ssid.configure(text=info.get("ssid", ""))
		self.hotspot_password.configure(text=info.get("passphrase", ""))
		problems = [info[key] for key in ("error", "dns_problem", "warning") if info.get(key)]
		self.hotspot_warning.configure(text="\n".join(problems))

	def toggle_hotspot(self, turning_on: bool | None = None) -> None:
		if turning_on is None:
			turning_on = self.hotspot_button.cget("text") == "Turn on hotspot"

		def work():
			return hotspot.turn_on() if turning_on else hotspot.turn_off()

		def done(info):
			self.show_hotspot(info)
			self.restart_services(start=turning_on)
			if turning_on and info.get("dns_problem"):
				messagebox.showwarning("Badge Arcade Manager", info["dns_problem"])

		self.background("Turning the hotspot on (Windows asks for admin rights)..." if turning_on
			else "Turning the hotspot off...", work, done)

	def check_leftover_hosts(self) -> None:
		"""Hotspot mode's hosts entries left behind (e.g. the PC restarted) while the hotspot is off."""
		if hotspot.hosts_ip() and not hotspot_address():
			if messagebox.askyesno("Badge Arcade Manager", "The hosts file still points Nintendo's servers at this PC's "
					"hotspot, which is off. Remove those entries now? (Windows asks for admin rights.)"):
				self.background("Removing the hosts entries...", lambda: hotspot.turn_off(stop_hotspot=False),
					self.show_hotspot)
		self.after(10_000, self.poll_hotspot)

	def poll_hotspot(self) -> None:
		if self.settings["connection"] == "hotspot":
			self.refresh_hotspot()
		self.after(10_000, self.poll_hotspot)

	def restart_services(self, start: bool = False) -> None:
		"""Restarts the server and proxy started from this window, so they pick up the
		connection mode (start=True also starts them if they aren't running)."""
		services = [s for s in (self.server, self.proxy) if s.pid() or (start and not s.listening())]
		elsewhere = [s.name for s in (self.server, self.proxy) if s.listening() and not s.pid()]
		if elsewhere:
			messagebox.showinfo("Badge Arcade Manager", f"The {' and '.join(elsewhere)} was started outside this window. "
				"Close its window and start it here, so it uses the new connection settings.")
		if not services:
			return

		def work():
			for service in services:
				service.stop()
			for service in services:
				for _ in range(30):
					if not service.listening():
						break
					threading.Event().wait(0.2)
				service.start()
				self.log_offsets[service.log] = service.log.stat().st_size if service.log.exists() else 0

		self.background("Restarting the server and proxy...", work,
			lambda _: self.status.set(f"Started the {' and '.join(s.name for s in services)}."))

	# --- Server tab ---

	def build_server_tab(self, tab: ttk.Frame) -> None:
		services = ttk.LabelFrame(tab, text="Services", padding=10)
		services.pack(fill="x")
		self.service_labels = {}
		for row, (service, what) in enumerate(((self.server, "Game server (logins, saves, SpotPass files)"),
				(self.proxy, "3DS proxy (sends Badge Arcade's traffic to the server)"))):
			ttk.Label(services, text=what, width=48).grid(row=row, column=0, sticky="w", pady=3)
			label = ttk.Label(services, width=34)
			label.grid(row=row, column=1, sticky="w")
			self.service_labels[service.name] = label
			ttk.Button(services, text="Start", command=lambda s=service: self.start_service(s)).grid(row=row, column=2, padx=3)
			ttk.Button(services, text="Stop", command=lambda s=service: self.stop_service(s)).grid(row=row, column=3, padx=3)
		ttk.Button(services, text="Restart server", command=self.restart_server).grid(row=0, column=4, padx=3)
		self.mode_label = ttk.Label(services, style="Hint.TLabel")
		self.mode_label.grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))

		date = ttk.LabelFrame(tab, text="Game date", padding=10)
		date.pack(fill="x", pady=8)
		ttk.Label(date, text="The machines and free plays are chosen for this date. \"current\" follows the real "
			"date; any week you serve is moved to it automatically.", style="Hint.TLabel", wraplength=900).pack(anchor="w")
		row = ttk.Frame(date)
		row.pack(anchor="w", pady=(6, 0))
		self.date_var = tk.StringVar(value=str(serve.game_date() or "current"))
		ttk.Entry(row, textvariable=self.date_var, width=14).pack(side="left")
		ttk.Label(row, text="current, or YYYY-MM-DD", style="Hint.TLabel").pack(side="left", padx=6)
		ttk.Button(row, text="Current date", command=lambda: self.date_var.set("current")).pack(side="left", padx=6)
		ttk.Button(row, text="Apply and restart server", command=self.apply_date).pack(side="left")

		live = ttk.LabelFrame(tab, text="What the 3DS gets next time Badge Arcade opens", padding=10)
		live.pack(fill="x")
		self.live_text = tk.StringVar()
		ttk.Label(live, textvariable=self.live_text, justify="left").pack(anchor="w")

		activity = ttk.LabelFrame(tab, text="Activity", padding=6)
		activity.pack(fill="both", expand=True, pady=(8, 0))
		top = ttk.Frame(activity)
		top.pack(fill="x")
		self.show_all_logs = tk.BooleanVar(value=False)
		ttk.Checkbutton(top, text="Show every log line", variable=self.show_all_logs).pack(side="left")
		ttk.Label(top, text="(only for services started from this window)", style="Hint.TLabel").pack(side="left", padx=6)
		self.log_view = tk.Text(activity, height=10, wrap="none", font=("Consolas", 9), state="disabled")
		scroll = ttk.Scrollbar(activity, command=self.log_view.yview)
		self.log_view.configure(yscrollcommand=scroll.set)
		scroll.pack(side="right", fill="y")
		self.log_view.pack(fill="both", expand=True)

	def refresh_services(self) -> None:
		for service in (self.server, self.proxy):
			if service.listening():
				text = "Running" if service.pid() else "Running (started elsewhere)"
			else:
				text = "Stopped"
			self.service_labels[service.name].configure(text=text)

		hotspot_mode = self.settings["connection"] == "hotspot"
		self.mode_label.configure(text="The 3DS connects through this PC's hotspot (see the Setup tab)." if hotspot_mode
			else "The 3DS connects through the proxy on your Wi-Fi (see the Setup tab).")

		# The PC's IP can change (no fixed address); the server picks it up when it starts
		ip = lan_address() or "not connected to a network"
		self.ip_label.configure(text=ip)
		serving = self.server_ip() if self.server.listening() else None
		expected = self.live_hotspot_ip() or ip
		if serving and serving != expected:
			warning = (f"The server gives the 3DS {serving}, but it should be {expected} now. Restart the server"
				+ ("." if hotspot_mode else f", and change the proxy server on the 3DS to {ip}."))
			self.ip_warning.configure(text=warning)
			self.status.set(warning)
		else:
			self.ip_warning.configure(text="")
		self.after(3000, self.refresh_services)

	def server_ip(self) -> str | None:
		"""The IP the running server gives the 3DS, from its start-up line (servers started here only)."""
		if not self.server.log.exists() or not self.server.pid():
			return None
		with open(self.server.log, "rb") as f:
			f.seek(max(0, self.server.log.stat().st_size - 200_000))
			tail = f.read().decode("utf-8", "replace")
		found = re.findall(r"Ready\. Auth: ([\d.]+):", tail)
		return found[-1] if found else None

	def start_service(self, service: Service) -> None:
		try:
			service.start()
			self.log_offsets[service.log] = service.log.stat().st_size if service.log.exists() else 0
			self.status.set(f"Started the {service.name}.")
		except Exception as e:
			messagebox.showerror("Badge Arcade Manager", str(e))

	def stop_service(self, service: Service) -> None:
		if not service.listening() and not service.pid():
			self.status.set(f"The {service.name} isn't running.")
		elif service.stop():
			self.status.set(f"Stopped the {service.name}.")
		else:
			messagebox.showinfo("Badge Arcade Manager", f"The {service.name} was started outside this window "
				"(e.g. from a command prompt). Close its window to stop it.")

	def restart_server(self) -> bool:
		if self.server.listening() and not self.server.pid():
			messagebox.showinfo("Badge Arcade Manager", "The server was started outside this window. Close its "
				"window, then press Start here (or start it again the way you did before).")
			return False

		def work():
			self.server.stop()
			for _ in range(30):
				if not self.server.listening():
					break
				threading.Event().wait(0.2)
			self.server.start()

		self.background("Restarting the server...", work, lambda _: self.status.set("Server restarted."))
		return True

	def apply_date(self) -> None:
		text = self.date_var.get().strip().lower()
		try:
			date = None if text in ("", "current") else datetime.date.fromisoformat(text)
		except ValueError:
			messagebox.showerror("Badge Arcade Manager", "Enter \"current\" or a date as YYYY-MM-DD, e.g. 2022-12-30.")
			return
		serve.set_game_date(date)
		self.plays_date.set(str(serve.current_game_date()))
		self.refresh_status()  # also moves the served week to the new date
		if self.server.listening():
			self.restart_server()
		else:
			self.status.set(f"Game date set to {date}. It applies when the server starts.")

	def missing_files(self) -> str | None:
		"""What the user still has to provide (see README) before the SpotPass features work."""
		if self.key is None:
			return "spotpass-letter/boot9.bin (dumped from your 3DS) is missing or isn't a valid boot9.bin."
		missing = [p.name for p in (serve.LIVE_WEEK, serve.LIVE_PLAYINFO, serve.PLAYINFO_BASE) if not p.exists()]
		if missing:
			return f"SpotPass files missing from the other/ folder: {', '.join(missing)}."
		return None

	def refresh_status(self) -> None:
		problem = self.missing_files()
		if problem:
			self.live_text.set(f"Not set up yet: {problem}\nSee \"What you need to provide\" in README.md.")
			return

		def work():
			moved = serve.keep_week_current(self.key)
			return moved, serve.status(self.key)

		def done(result):
			moved, s = result
			if moved:
				self.status.set("Moved the served week to the game date. Reopen Badge Arcade to get it.")
			start, end = s["week_dates"]
			today = s["current_date"]
			plays = next((p for _, b, e, p in s["campaigns"] if today and b.date() <= today < e.date()), None)
			lines = [
				f"Machines:   {s['week']}  ({s['week_machines']} machines, {start} to {end})",
				f"Free plays: {plays if plays is not None else 'none'} on the game date"
				+ ("" if plays is not None else "  (use the Free plays tab)"),
				f"Game date:  {today}" + ("  (current date)" if not s["game_date"] else ""),
				f"Letter:     \"{s['letter'].title}\"" + ("  (downloaded)" if s["letter"].downloaded else "  (waiting for the 3DS)")
				if s["letter"] else "Letter:     none  (use the Letters tab)",
			]
			self.live_text.set("\n".join(lines))

		self.background("Checking what's live...", work, done)

	def poll_logs(self) -> None:
		for service, pattern in ((self.server, SERVER_EVENTS), (self.proxy, PROXY_EVENTS)):
			if not service.log.exists():
				continue
			offset = self.log_offsets.setdefault(service.log, service.log.stat().st_size)
			with open(service.log, "rb") as f:
				f.seek(offset)
				chunk = f.read()
			self.log_offsets[service.log] = offset + len(chunk)
			lines = chunk.decode("utf-8", "replace").splitlines()
			if service is self.server:
				for line in lines:
					self.check_letter_downloaded(line)
			if not self.show_all_logs.get():
				lines = [tidy(line) for line in lines if pattern.search(line)]
			if lines:
				self.log_view.configure(state="normal")
				for line in lines:
					self.log_view.insert("end", f"[{service.name}] {line}\n")
				if int(self.log_view.index("end-1c").split(".")[0]) > 3000:
					self.log_view.delete("1.0", "1000.0")
				self.log_view.see("end")
				self.log_view.configure(state="disabled")
		self.after(1000, self.poll_logs)

	# --- Machines tab ---

	def build_machines_tab(self, tab: ttk.Frame) -> None:
		ttk.Label(tab, text="Pick a week of machines and press Serve. Then fully close and reopen Badge Arcade "
			"on the 3DS; the new week downloads during \"Downloading Data\".", style="Hint.TLabel", wraplength=940).pack(anchor="w")
		body = ttk.Frame(tab)
		body.pack(fill="both", expand=True, pady=6)
		self.week_tree = ttk.Treeview(body, columns=("dates", "machines"), show="tree headings", selectmode="browse", height=12)
		self.week_tree.heading("#0", text="Week")
		self.week_tree.heading("dates", text="Original dates")
		self.week_tree.heading("machines", text="Machines")
		self.week_tree.column("#0", width=330)
		self.week_tree.column("dates", width=190)
		self.week_tree.column("machines", width=80, anchor="e")
		self.week_tree.pack(side="left", fill="both", expand=True)
		self.week_tree.bind("<<TreeviewSelect>>", lambda e: self.show_week())
		self.week_details = tk.Text(body, width=44, wrap="word", font=("Segoe UI", 9), state="disabled")
		self.week_details.pack(side="left", fill="both", padx=(8, 0))
		buttons = ttk.Frame(tab)
		buttons.pack(fill="x")
		ttk.Button(buttons, text="Serve this week", command=self.serve_selected_week).pack(side="left")
		ttk.Button(buttons, text="Delete custom week", command=self.delete_selected_week).pack(side="left", padx=6)
		ttk.Button(buttons, text="Refresh", command=self.refresh_weeks).pack(side="left")

	def refresh_weeks(self) -> None:
		def done(weeks):
			self.weeks = weeks
			self.week_tree.delete(*self.week_tree.get_children())
			for index, week in enumerate(weeks):
				dates = f"{week.start} to {week.end - datetime.timedelta(days=1)}" if week.start else ""
				self.week_tree.insert("", "end", iid=str(index), text=week.label, values=(dates, week.machines))

		self.background("Reading weeks...", lambda: serve.list_weeks(self.key), done)

	def selected_week(self) -> serve.Week | None:
		selection = self.week_tree.selection()
		return self.weeks[int(selection[0])] if selection else None

	def show_week(self) -> None:
		week = self.selected_week()
		if not week:
			return
		names = week.setups or serve.week_machine_names(week.path, self.key)
		counts = Counter(series_of(n) for n in names)
		text = [week.label, ""]
		text += [f"{series_name(code)}: {count}" for code, count in counts.most_common()]
		self.set_text(self.week_details, "\n".join(text))

	def serve_selected_week(self) -> None:
		week = self.selected_week()
		if not week:
			messagebox.showinfo("Badge Arcade Manager", "Pick a week first.")
			return
		def done(message):
			self.refresh_status()
			self.show_week()
			messagebox.showinfo("Badge Arcade Manager", message + "\n\nNow fully close and reopen Badge Arcade.")

		self.background("Serving the week...", lambda: serve.serve_week(week, self.key), done)

	def delete_selected_week(self) -> None:
		week = self.selected_week()
		if not week or not week.custom:
			messagebox.showinfo("Badge Arcade Manager", "Pick a custom week to delete (Nintendo's weeks can't be deleted here).")
			return
		if messagebox.askyesno("Badge Arcade Manager", f"Delete {week.label}?"):
			serve.delete_custom_week(week)
			self.refresh_weeks()

	# --- Build a week tab ---

	def build_builder_tab(self, tab: ttk.Frame) -> None:
		ttk.Label(tab, text="Choose machine setups from every archived week. Select a series or single "
			"machines on the left and press Add. By default every machine is on the floor every day; Nintendo's "
			"own weeks had about 30 a day, rotating.", style="Hint.TLabel", wraplength=940).pack(anchor="w")
		body = ttk.Frame(tab)
		body.pack(fill="both", expand=True, pady=6)

		left = ttk.Frame(body)
		left.pack(side="left", fill="both", expand=True)
		ttk.Label(left, text="Available machines").pack(anchor="w")
		self.catalog = ttk.Treeview(left, show="tree", selectmode="extended")
		self.catalog.pack(fill="both", expand=True)
		self.catalog.bind("<<TreeviewSelect>>", lambda e: self.show_badges(self.catalog))

		middle = ttk.Frame(body, padding=6)
		middle.pack(side="left", fill="y")
		ttk.Button(middle, text="Add  >", command=self.add_to_week).pack(pady=(80, 4))
		ttk.Button(middle, text="<  Remove", command=self.remove_from_week).pack()
		ttk.Button(middle, text="Clear", command=lambda: self.chosen.delete(*self.chosen.get_children())).pack(pady=4)

		right = ttk.Frame(body)
		right.pack(side="left", fill="both", expand=True)
		ttk.Label(right, text="Your week").pack(anchor="w")
		self.chosen = ttk.Treeview(right, show="tree", selectmode="extended")
		self.chosen.pack(fill="both", expand=True)
		self.chosen.bind("<<TreeviewSelect>>", lambda e: self.show_badges(self.chosen))

		self.badge_view = tk.Text(body, width=34, wrap="word", font=("Segoe UI", 9), state="disabled")
		self.badge_view.pack(side="left", fill="both", padx=(8, 0))

		options = ttk.Frame(tab)
		options.pack(fill="x")
		ttk.Label(options, text="Name:").pack(side="left")
		self.week_name = tk.StringVar(value="My week")
		ttk.Entry(options, textvariable=self.week_name, width=24).pack(side="left", padx=(4, 12))
		self.every_day = tk.BooleanVar(value=True)
		ttk.Checkbutton(options, text="Every machine, every day", variable=self.every_day,
			command=lambda: per_series_box.configure(state="disabled" if self.every_day.get() else "normal")).pack(side="left")
		ttk.Label(options, text="or per series each day:").pack(side="left", padx=(8, 0))
		self.per_series = tk.IntVar(value=3)
		per_series_box = ttk.Spinbox(options, from_=1, to=8, textvariable=self.per_series, width=4, state="disabled")
		per_series_box.pack(side="left", padx=(4, 12))
		ttk.Button(options, text="Preview today", command=self.preview_week).pack(side="left")
		ttk.Button(options, text="Build", command=self.build_week).pack(side="left", padx=6)
		ttk.Button(options, text="Build and serve", command=lambda: self.build_week(serve_after=True)).pack(side="left")

	def load_builder(self) -> None:
		def done(builder):
			self.builder = builder
			by_series: dict[str, list[str]] = {}
			for setup in builder.buildable:
				by_series.setdefault(series_of(setup), []).append(setup)
			for code in sorted(by_series, key=series_name):
				parent = self.catalog.insert("", "end", iid=f"series:{code}", text=f"{series_name(code)} ({len(by_series[code])})")
				for setup in by_series[code]:
					self.catalog.insert(parent, "end", iid=setup, text=f"{setup}  ({len(builder.badges(setup))} badges)")
			self.status.set(f"{len(builder.buildable)} machine setups available.")

		self.background("Reading every archived machine (a few seconds)...", lambda: Builder(self.key), done)

	def show_badges(self, tree: ttk.Treeview) -> None:
		selection = [i for i in tree.selection() if not i.startswith("series:")]
		if not selection or not self.builder:
			return
		setup = selection[-1]
		badges = self.builder.badges(setup)
		self.set_text(self.badge_view, f"{setup}\n{series_name(series_of(setup))}\n\n{len(badges)} badges:\n"
			+ "\n".join(badge_name(b) for b in badges))

	def add_to_week(self) -> None:
		for item in self.catalog.selection():
			setups = self.catalog.get_children(item) if item.startswith("series:") else (item,)
			for setup in setups:
				if not self.chosen.exists(setup):
					self.chosen.insert("", "end", iid=setup, text=f"{series_name(series_of(setup))}: {setup}")

	def remove_from_week(self) -> None:
		for item in self.chosen.selection():
			self.chosen.delete(item)

	def chosen_setups(self) -> list[str]:
		# Keep series together, in the order they were added
		setups = list(self.chosen.get_children())
		order = list(dict.fromkeys(series_of(s) for s in setups))
		return sorted(setups, key=lambda s: order.index(series_of(s)))

	def chosen_per_series(self) -> int:
		return 0 if self.every_day.get() else self.per_series.get()

	def preview_week(self) -> None:
		setups = self.chosen_setups()
		if not setups:
			messagebox.showinfo("Badge Arcade Manager", "Add some machines first.")
			return
		lineup = daily_lineup(setups, 7, self.chosen_per_series())
		# Served weeks start the day before the game date, so the game date is day 2
		shown = lineup[1]
		self.set_text(self.badge_view, f"On {serve.current_game_date()} the floor has {len(shown)} "
			f"machines (bonus machine: {shown[0]}):\n\n" + "\n".join(f"{series_name(series_of(s))}: {s}" for s in shown))

	def build_week(self, serve_after: bool = False) -> None:
		setups = self.chosen_setups()
		name = self.week_name.get().strip()
		if not setups or not name:
			messagebox.showinfo("Badge Arcade Manager", "Add some machines and give the week a name.")
			return
		per_series = self.chosen_per_series()

		def work():
			content = self.builder.build(setups, per_series)
			path = serve.save_custom_week(name, setups, per_series, content)
			message = f"Built \"{name}\": {len(setups)} machines, {len(content) / 1e6:.1f} MB."
			if serve_after:
				week = next(w for w in serve.list_weeks(self.key) if w.path == path)
				message += "\n" + serve.serve_week(week, self.key) + "\n\nNow fully close and reopen Badge Arcade."
			return message

		def done(message):
			self.refresh_status()
			messagebox.showinfo("Badge Arcade Manager", message)

		self.background("Building the week...", work, done)

	# --- Free plays tab ---

	def build_plays_tab(self, tab: ttk.Frame) -> None:
		ttk.Label(tab, text="Free plays are handed out once per daily campaign. Giving free plays makes new "
			"campaigns, so the game pays out again today. Reopen Badge Arcade to collect them.",
			style="Hint.TLabel", wraplength=940).pack(anchor="w")
		row = ttk.Frame(tab)
		row.pack(anchor="w", pady=10)
		ttk.Label(row, text="Free plays:").pack(side="left")
		self.plays = tk.IntVar(value=10)
		ttk.Spinbox(row, from_=1, to=99, textvariable=self.plays, width=5).pack(side="left", padx=6)
		ttk.Label(row, text="on").pack(side="left", padx=(6, 0))
		self.plays_date = tk.StringVar(value=str(serve.default_free_play_date()))
		ttk.Entry(row, textvariable=self.plays_date, width=12).pack(side="left", padx=6)
		ttk.Button(row, text="Game date", command=lambda: self.plays_date.set(str(serve.default_free_play_date()))).pack(side="left")
		ttk.Button(row, text="Real today", command=lambda: self.plays_date.set(str(datetime.date.today()))).pack(side="left", padx=4)
		ttk.Button(row, text="Give free plays", command=self.give_plays).pack(side="left", padx=(12, 0))
		self.campaign_tree = ttk.Treeview(tab, columns=("dates", "plays", "state"), show="headings", height=9)
		for column, title, width in (("dates", "Campaign (UTC)", 320), ("plays", "Plays", 70), ("state", "On your save", 220)):
			self.campaign_tree.heading(column, text=title)
			self.campaign_tree.column(column, width=width)
		self.campaign_tree.pack(fill="x")
		ttk.Button(tab, text="Refresh", command=self.refresh_plays).pack(anchor="w", pady=6)

	def refresh_plays(self) -> None:
		def work():
			claims = {}
			for save in self.freeplay_records():
				claims.update(save["campaigns"])
			return serve.playinfo_campaigns(self.key), claims, serve.game_date()

		def done(result):
			campaigns, claims, today = result
			self.campaign_tree.delete(*self.campaign_tree.get_children())
			for cid, begin, end, plays in campaigns:
				state = {1: "not collected yet", 0: "collected"}.get(claims.get(cid), "not seen by the game yet")
				marker = "  <- game date" if today and begin.date() <= today < end.date() else ""
				self.campaign_tree.insert("", "end", values=(f"{begin:%Y-%m-%d %H:%M} to {end:%m-%d %H:%M}{marker}", plays, state))

		self.background("Reading free plays...", work, done)

	def give_plays(self) -> None:
		plays = self.plays.get()
		try:
			date = datetime.date.fromisoformat(self.plays_date.get().strip())
		except ValueError:
			messagebox.showerror("Badge Arcade Manager", "Enter the day as YYYY-MM-DD, e.g. 2022-12-30.")
			return

		def done(message):
			self.refresh_plays()
			self.refresh_status()
			messagebox.showinfo("Badge Arcade Manager", message + "\n\nNow fully close and reopen Badge Arcade.")

		self.background("Making free plays...",
			lambda: serve.give_free_plays(plays, self.key, date).splitlines()[0], done)

	# --- Letters tab ---

	def build_letters_tab(self, tab: ttk.Frame) -> None:
		ttk.Label(tab, text="Send a letter to the 3DS's Notifications applet, like the ones Nintendo sent from Badge "
			"Arcade. The 3DS fetches it in the background with SpotPass, which can take hours: leave it in sleep mode "
			"with Wi-Fi on and the server and proxy running.", style="Hint.TLabel", wraplength=940, justify="left").pack(anchor="w")
		ttk.Label(tab, text="Experimental: this hasn't been confirmed on a console yet. Back up the 3DS's news save "
			"first (spotpass-letter/README.md, \"Delivering it\").", foreground="#b35c00", wraplength=940,
			justify="left").pack(anchor="w", pady=(2, 0))

		body = ttk.Frame(tab)
		body.pack(fill="both", expand=True, pady=6)

		history = ttk.LabelFrame(body, text="Your letters", padding=6)
		history.pack(side="left", fill="y")
		self.letter_tree = ttk.Treeview(history, columns=("state",), show="tree headings", selectmode="browse", height=14)
		self.letter_tree.heading("#0", text="Title")
		self.letter_tree.heading("state", text="Sent")
		self.letter_tree.column("#0", width=210)
		self.letter_tree.column("state", width=150)
		self.letter_tree.pack(fill="y", expand=True)
		self.letter_tree.bind("<<TreeviewSelect>>", lambda e: self.open_selected_letter())
		buttons = ttk.Frame(history)
		buttons.pack(fill="x", pady=(6, 0))
		ttk.Button(buttons, text="New letter", command=self.new_letter).pack(side="left")
		ttk.Button(buttons, text="Delete", command=self.delete_selected_letter).pack(side="left", padx=6)

		editor = ttk.LabelFrame(body, text="Letter", padding=8)
		editor.pack(side="left", fill="both", expand=True, padx=(8, 0))
		editor.columnconfigure(1, weight=1)
		self.letter_title = tk.StringVar()
		self.letter_url = tk.StringVar()
		self.letter_region = tk.StringVar(value="USA")
		ttk.Label(editor, text="Title:").grid(row=0, column=0, sticky="w")
		ttk.Entry(editor, textvariable=self.letter_title).grid(row=0, column=1, sticky="ew", padx=6)
		self.title_count = ttk.Label(editor, style="Hint.TLabel", width=8)
		self.title_count.grid(row=0, column=2, sticky="w")
		ttk.Label(editor, text="Message:").grid(row=1, column=0, sticky="nw", pady=(6, 0))
		self.letter_message = tk.Text(editor, height=9, width=50, wrap="word", font=("Segoe UI", 10), undo=True)
		self.letter_message.grid(row=1, column=1, sticky="nsew", padx=6, pady=(6, 0))
		editor.rowconfigure(1, weight=1)
		self.message_count = ttk.Label(editor, style="Hint.TLabel", width=8)
		self.message_count.grid(row=1, column=2, sticky="nw", pady=(6, 0))
		ttk.Label(editor, text="Link:").grid(row=2, column=0, sticky="w", pady=(6, 0))
		ttk.Entry(editor, textvariable=self.letter_url).grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))
		ttk.Label(editor, text="optional", style="Hint.TLabel").grid(row=2, column=2, sticky="w", pady=(6, 0))
		ttk.Label(editor, text="Region:").grid(row=3, column=0, sticky="w", pady=(6, 0))
		regions = ttk.Frame(editor)
		regions.grid(row=3, column=1, sticky="w", padx=6, pady=(6, 0))
		for region, label in (("USA", "Americas (USA)"), ("EUR", "Europe (EUR)")):
			ttk.Radiobutton(regions, text=label, variable=self.letter_region, value=region).pack(side="left", padx=(0, 12))

		ttk.Label(editor, text="Picture:").grid(row=4, column=0, sticky="nw", pady=(6, 0))
		pictures = ttk.Frame(editor)
		pictures.grid(row=4, column=1, columnspan=2, sticky="w", padx=6, pady=(6, 0))
		self.picture_preview = ttk.Label(pictures, relief="solid", width=26, anchor="center", text="No picture")
		self.picture_preview.pack(side="left")
		picture_buttons = ttk.Frame(pictures)
		picture_buttons.pack(side="left", padx=8, anchor="n")
		self.choose_picture_button = ttk.Button(picture_buttons, text="Choose picture...", command=self.choose_letter_picture)
		self.choose_picture_button.pack(anchor="w")
		ttk.Button(picture_buttons, text="No picture", command=lambda: self.set_letter_picture(None)).pack(anchor="w", pady=4)
		self.picture_info = ttk.Label(picture_buttons, style="Hint.TLabel", wraplength=280, justify="left")
		self.picture_info.pack(anchor="w")
		if not letters.pillow_available():
			self.choose_picture_button.state(["disabled"])
			self.picture_info.config(text="Pictures need the Pillow package: run Setup.bat again.")

		self.letter_warning = ttk.Label(editor, foreground="#b35c00", wraplength=620, justify="left")
		self.letter_warning.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
		actions = ttk.Frame(editor)
		actions.grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
		ttk.Button(actions, text="Send to 3DS", command=self.send_letter).pack(side="left")
		ttk.Button(actions, text="Save draft", command=self.save_letter_draft).pack(side="left", padx=6)
		ttk.Button(actions, text="Take letter down", command=self.take_letter_down).pack(side="left")

		self.letter_live = tk.StringVar()
		ttk.Label(tab, textvariable=self.letter_live, style="Big.TLabel").pack(anchor="w")

		self.editing_letter: letters.SavedLetter | None = None
		self.letter_picture: bytes | None = None
		self.letter_list: list[letters.SavedLetter] = []
		self.letter_title.trace_add("write", lambda *a: self.update_letter_hints())
		self.letter_url.trace_add("write", lambda *a: self.update_letter_hints())
		self.letter_message.bind("<<Modified>>", self.on_message_modified)
		self.set_letter_picture(None)

	def on_message_modified(self, event=None) -> None:
		if self.letter_message.edit_modified():
			self.letter_message.edit_modified(False)
			self.update_letter_hints()

	def letter_from_editor(self) -> letters.SavedLetter:
		"""The letter in the editor. A letter that was already sent becomes a new letter
		when it's changed, so the history keeps what was sent."""
		current = letters.SavedLetter("", self.letter_title.get().strip(), self.letter_message.get("1.0", "end-1c").strip(),
			self.letter_url.get().strip(), self.letter_region.get(), image=self.letter_picture)
		old = self.editing_letter
		if old is None:
			return current
		same = (old.title, old.message, old.url, old.region, old.image) == \
			(current.title, current.message, current.url, current.region, current.image)
		if old.sent and not same:
			return current
		current.id, current.created, current.sent, current.ns_data_id, current.downloaded = \
			old.id, old.created, old.sent, old.ns_data_id, old.downloaded
		return current

	def update_letter_hints(self) -> None:
		title = self.letter_title.get()
		message = self.letter_message.get("1.0", "end-1c")
		self.title_count.config(text=f"{len(title)}/{make_letter.TITLE_MAX}",
			foreground="#b00020" if len(title) > make_letter.TITLE_MAX else "")
		self.message_count.config(text=f"{len(message)}/{make_letter.MESSAGE_MAX}",
			foreground="#b00020" if len(message) > make_letter.MESSAGE_MAX else "")
		if not title.strip() and not message.strip():
			self.letter_warning.config(text="")
			return
		errors, warnings = make_letter.validate_letter(self.letter_from_editor().to_letter())
		# An empty title or message is only worth mentioning once the other one is filled in
		self.letter_warning.config(text="  ".join(errors + warnings))

	def show_letter(self, letter: letters.SavedLetter | None) -> None:
		self.editing_letter = letter
		self.letter_title.set(letter.title if letter else "")
		self.letter_url.set(letter.url if letter else "")
		self.letter_region.set(letter.region if letter else "USA")
		self.letter_message.delete("1.0", "end")
		if letter:
			self.letter_message.insert("1.0", letter.message)
		self.set_letter_picture(letter.image if letter else None)
		self.update_letter_hints()

	def set_letter_picture(self, jpeg: bytes | None) -> None:
		self.letter_picture = jpeg
		if not jpeg:
			self.letter_preview_image = None
			self.picture_preview.config(image="", text="No picture")
			if letters.pillow_available():
				self.picture_info.config(text="Any picture works: it's cropped to 400x240 and made into a JPEG under 50 KB.")
		else:
			try:
				self.letter_preview_image = tk.PhotoImage(data=letters.preview_png(jpeg))
				self.picture_preview.config(image=self.letter_preview_image, text="")
			except (ValueError, tk.TclError):
				self.picture_preview.config(image="", text="(no preview)")
			self.picture_info.config(text=f"400x240 JPEG, {len(jpeg) / 1024:.0f} KB")
		self.update_letter_hints()

	def choose_letter_picture(self) -> None:
		path = filedialog.askopenfilename(parent=self, title="Choose a picture", filetypes=letters.IMAGE_TYPES)
		if path:
			self.background("Preparing the picture...", lambda: letters.prepare_image(Path(path)), self.set_letter_picture)

	def refresh_letters(self, select: str | None = None) -> None:
		def work():
			return letters.list_letters(), serve.live_letter()

		def done(result):
			self.letter_list, live = result
			self.letter_tree.delete(*self.letter_tree.get_children())
			for letter in self.letter_list:
				if live and live.id == letter.id:
					state = "live, downloaded" if live.downloaded else "live, waiting for the 3DS"
				else:
					state = letter.sent.replace("T", " ")[:16] if letter.sent else "draft"
				self.letter_tree.insert("", "end", iid=letter.id, text=letter.title, values=(state,))
			if live:
				fetched = f"downloaded by the 3DS {live.downloaded.replace('T', ' ')[:16]}" if live.downloaded else \
					"not downloaded by the 3DS yet"
				self.letter_live.set(f"Live now: \"{live.title}\", sent {live.sent.replace('T', ' ')[:16]}, {fetched}.")
			else:
				self.letter_live.set("No letter is live.")
			if select and self.letter_tree.exists(select):
				self.letter_tree.selection_set(select)
				self.letter_tree.see(select)

		self.background("Reading letters...", work, done)

	def open_selected_letter(self) -> None:
		selection = self.letter_tree.selection()
		letter = next((item for item in self.letter_list if selection and item.id == selection[0]), None)
		if letter and (self.editing_letter is None or letter.id != self.editing_letter.id):
			self.show_letter(letter)

	def new_letter(self) -> None:
		self.letter_tree.selection_set(())
		self.show_letter(None)

	def delete_selected_letter(self) -> None:
		selection = self.letter_tree.selection()
		if not selection:
			self.status.set("Pick a letter to delete first.")
			return
		letter = next(item for item in self.letter_list if item.id == selection[0])
		live = serve.live_letter()
		is_live = live is not None and live.id == letter.id
		if not messagebox.askyesno("Badge Arcade Manager", f"Delete \"{letter.title}\"?"
				+ ("\n\nIt's live now, so it will be taken down too." if is_live else "")):
			return

		def work():
			if is_live:
				serve.remove_letter()
			letters.delete_letter(letter.id)

		def done(_):
			self.show_letter(None)
			self.refresh_letters()

		self.background("Deleting the letter...", work, done)

	def save_letter_draft(self) -> None:
		letter = self.letter_from_editor()
		if not letter.title and not letter.message:
			self.status.set("There's nothing to save yet.")
			return

		def done(saved):
			self.editing_letter = saved
			self.status.set(f"Saved \"{saved.title or 'untitled'}\".")
			self.refresh_letters(select=saved.id)

		self.background("Saving the letter...", lambda: letters.save_letter(letter), done)

	def send_letter(self) -> None:
		if self.key is None:
			messagebox.showerror("Badge Arcade Manager", self.missing_files())
			return
		letter = self.letter_from_editor()
		errors, _ = make_letter.validate_letter(letter.to_letter())
		if errors:
			messagebox.showerror("Badge Arcade Manager", "\n".join(errors))
			return
		live = serve.live_letter()
		question = f"Send \"{letter.title}\" to the 3DS?"
		if live and live.id != letter.id:
			question += f"\n\nIt replaces \"{live.title}\", which is live now."
		if not self.settings.get("letters_warned"):
			question += ("\n\nLetters are experimental. Back up the 3DS's news save with GodMode9 first (see "
				"spotpass-letter/README.md, \"Delivering it\").")
		if not messagebox.askyesno("Badge Arcade Manager", question):
			return
		self.settings["letters_warned"] = True
		save_settings(self.settings)

		def done(message):
			self.editing_letter = letter
			self.refresh_letters(select=letter.id)
			self.refresh_status()
			ready = self.server.listening() and self.proxy.listening()
			messagebox.showinfo("Badge Arcade Manager", message + ("" if ready else
				"\n\nStart the server and proxy on the Server tab so the 3DS can fetch it."))

		self.background("Sending the letter...", lambda: serve.serve_letter(letter, self.key), done)

	def take_letter_down(self) -> None:
		def done(message):
			self.status.set(message)
			self.refresh_letters()
			self.refresh_status()

		self.background("Taking the letter down...", serve.remove_letter, done)

	def check_letter_downloaded(self, line: str) -> None:
		"""Called with each new server log line: marks the live letter as downloaded."""
		if "Sent SpotPass file news_v131" in line and serve.mark_letter_downloaded():
			self.status.set("The 3DS downloaded the letter. It should now be in the Notifications applet.")
			self.refresh_letters()

	# --- Saves tab ---

	def build_saves_tab(self, tab: ttk.Frame) -> None:
		ttk.Label(tab, text="Saves stored on this server. Backups are zip files in server/backups; to restore one, "
			"stop the server and unzip it over server/data.", style="Hint.TLabel", wraplength=940).pack(anchor="w")
		self.save_tree = ttk.Treeview(tab, columns=("pid", "what", "version", "size", "updated"), show="headings", height=8)
		for column, title, width in (("pid", "Player (PID)", 120), ("what", "Record", 260), ("version", "Version", 70),
				("size", "Size", 90), ("updated", "Updated", 150)):
			self.save_tree.heading(column, text=title)
			self.save_tree.column(column, width=width)
		self.save_tree.pack(fill="x", pady=6)
		buttons = ttk.Frame(tab)
		buttons.pack(fill="x")
		ttk.Button(buttons, text="Back up now", command=self.backup_saves).pack(side="left")
		ttk.Button(buttons, text="Reset selected player...", command=self.reset_player).pack(side="left", padx=6)
		ttk.Button(buttons, text="Open backups folder", command=lambda: os.startfile(SERVER_DIR / "backups")).pack(side="left")
		ttk.Button(buttons, text="Refresh", command=self.refresh_saves).pack(side="left", padx=6)
		ttk.Label(tab, text="Free-play record", style="Big.TLabel").pack(anchor="w", pady=(10, 0))
		self.freeplay_view = tk.Text(tab, height=12, font=("Consolas", 9), state="disabled")
		self.freeplay_view.pack(fill="both", expand=True)

	def open_storage(self):
		config = load_config(serve.SERVER_CONFIG)
		return config, Storage(config.data_path)

	def freeplay_records(self) -> list[dict]:
		"""Each player's FreePlayData: the practice-catcher time and the free-play campaigns seen."""
		_, storage = self.open_storage()
		try:
			records = []
			for obj in storage.list_objects():
				if obj["data_type"] != 100:
					continue
				fields = dict(admin.meta_fields(obj["meta_binary"]))
				count = fields.get(0x20, 0)
				campaigns = {fields[0x24 + 8 * i]: fields[0x28 + 8 * i] for i in range(count) if 0x28 + 8 * i in fields}
				records.append({"pid": obj["owner_id"], "fields": fields, "campaigns": campaigns})
			return records
		finally:
			storage.close()

	def refresh_saves(self) -> None:
		def work():
			_, storage = self.open_storage()
			try:
				return storage.list_objects(), self.freeplay_records()
			finally:
				storage.close()

		def done(result):
			objects, records = result
			self.save_tree.delete(*self.save_tree.get_children())
			for obj in objects:
				what = {100: "Free plays + badges collected"}.get(obj["data_type"], f"Type {obj['data_type']}")
				updated = datetime.datetime.fromtimestamp(obj["updated"]).strftime("%Y-%m-%d %H:%M")
				size = f"{obj['size']} bytes" if obj["version"] else "empty"
				self.save_tree.insert("", "end", iid=str(obj["data_id"]),
					values=(obj["owner_id"], what, obj["version"], size, updated))
			text = []
			for record in records:
				practice = record["fields"].get(0x18)
				when = datetime.datetime.fromtimestamp(practice).strftime("%Y-%m-%d %H:%M") if practice else "never"
				text.append(f"PID {record['pid']}: practice catcher last played {when}")
				for cid, flag in record["campaigns"].items():
					text.append(f"  campaign {cid}: {'not collected yet' if flag else 'collected'}")
			self.set_text(self.freeplay_view, "\n".join(text) or "No saves yet.")

		self.background("Reading saves...", work, done)

	def backup_saves(self) -> None:
		def work():
			config, storage = self.open_storage()
			try:
				return admin.backup(config, storage)
			finally:
				storage.close()

		self.background("Backing up...", work, lambda path: messagebox.showinfo("Badge Arcade Manager", f"Saved {path}"))

	def reset_player(self) -> None:
		selection = self.save_tree.selection()
		if not selection:
			messagebox.showinfo("Badge Arcade Manager", "Pick a save first.")
			return
		pid = int(self.save_tree.item(selection[0], "values")[0])
		if not messagebox.askyesno("Badge Arcade Manager", f"Forget every save of player {pid}? A backup is made "
				"first. Do this while Badge Arcade is closed; it starts fresh next time.", icon="warning"):
			return

		def work():
			config, storage = self.open_storage()
			try:
				return admin.reset(config, storage, pid)
			finally:
				storage.close()

		def done(result):
			removed, path = result
			self.refresh_saves()
			messagebox.showinfo("Badge Arcade Manager", f"Removed {removed} record(s). Backup: {path}")

		self.background("Resetting...", work, done)

	# --- misc ---

	@staticmethod
	def set_text(widget: tk.Text, text: str) -> None:
		widget.configure(state="normal")
		widget.delete("1.0", "end")
		widget.insert("1.0", text)
		widget.configure(state="disabled")

	def on_close(self) -> None:
		ours = [s for s in (self.server, self.proxy) if s.pid() and s.listening()]
		hotspot_on = bool(hotspot.hosts_ip())
		if ours or hotspot_on:
			what = " and ".join(filter(None, ("the server and proxy" if ours else "", "the hotspot" if hotspot_on else "")))
			answer = messagebox.askyesnocancel("Badge Arcade Manager", f"Stop {what} too?\n\n"
				"Yes: stop them.  No: leave them running in the background (this window can stop them next time).")
			if answer is None:
				return
			if answer:
				for service in ours:
					service.stop()
				if hotspot_on:
					self.background("Turning the hotspot off...", hotspot.turn_off, lambda _: self.destroy())
					return
		self.destroy()


def hotspot_address() -> str | None:
	"""The hotspot's address while the hotspot is on (whatever the connection mode)."""
	ip = hotspot.hotspot_ip()
	with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
		try:
			s.bind((ip, 0))  # only works while the hotspot has the address
			return ip
		except OSError:
			return None


def load_settings() -> dict:
	settings = {"connection": "hotspot"}
	try:
		settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
	except (FileNotFoundError, ValueError):
		pass
	return settings


def save_settings(settings: dict) -> None:
	SETTINGS_FILE.write_text(json.dumps(settings, indent="\t") + "\n", encoding="utf-8")


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


if __name__ == "__main__":
	Manager().mainloop()

"""Setup tab: the checklist, updates, the helper's settings, and connecting the 3DS (hotspot or proxy)."""

from pathlib import Path
import os
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import hotspot
import install
import update

from . import ROOT, TITLE, WINDOWS, open_path, save_settings
from .helper_tabs import HELPER_ERROR, HelperSettings
from .services import hotspot_address
from .widgets import BAD, GOOD, auto_wrap, hint, scrollable

UPDATE_CHECK_INTERVAL = 86400  # seconds between automatic update checks


class SetupTab:
	def build_setup_tab(self, tab: ttk.Frame) -> None:
		tab = scrollable(tab)
		self.update_banner = ttk.Frame(tab, padding=(0, 0, 0, 8))
		self.update_text = ttk.Label(self.update_banner, style="Big.TLabel")
		self.update_text.pack(side="left")
		ttk.Button(self.update_banner, text="Update now", style="Accent.TButton",
			command=lambda: self.install_update(ask=True)).pack(side="left", padx=6)
		ttk.Button(self.update_banner, text="What's new", command=lambda: webbrowser.open(
			(self.settings.get("update_release") or {}).get("page_url") or update.CHANGES_URL)
		).pack(side="left")

		checks = ttk.LabelFrame(tab, text="Checklist", padding=10)
		checks.pack(fill="x")
		self.checks_frame = checks
		self.checklist_frame = ttk.Frame(checks)
		self.checklist_frame.pack(fill="x")
		buttons = ttk.Frame(checks)
		buttons.pack(anchor="w", pady=(8, 0))
		ttk.Button(buttons, text="Refresh", command=self.refresh_checklist).pack(side="left")
		ttk.Button(buttons, text="Open project folder", command=lambda: self.open_folder(ROOT)).pack(side="left", padx=6)
		ttk.Label(buttons, text="README.md explains where each file comes from.", style="Hint.TLabel").pack(side="left", padx=6)
		versions = ttk.Frame(checks)
		versions.pack(anchor="w", pady=(6, 0))
		ttk.Label(versions, text=f"Version {update.current_version()}").pack(side="left")
		ttk.Button(versions, text="Check for updates", command=lambda: self.check_for_update(manual=True)).pack(side="left", padx=6)
		self.auto_update = tk.BooleanVar(value=bool(self.settings.get("auto_update")))
		ttk.Checkbutton(versions, text="Install updates automatically when the manager opens", variable=self.auto_update,
			command=self.change_auto_update).pack(side="left", padx=6)

		custom = ttk.LabelFrame(tab, text="Your own badges and machines (the Badges, Machine editor and Custom weeks tabs)",
			padding=10)
		custom.pack(fill="x", pady=(8, 0))
		if self.helper:
			self.helper.tabs["Setup"] = HelperSettings(custom, self.helper)
			self.helper.tabs["Setup"].pack(fill="x")
		else:
			self.build_helper_missing(custom)

		connect = ttk.LabelFrame(tab, text="Connect the 3DS", padding=10)
		connect.pack(fill="both", expand=True, pady=(8, 0))
		self.connection = tk.StringVar(value=self.settings["connection"])
		ttk.Radiobutton(connect, text="Through this PC's Wi-Fi hotspot: no proxy settings on the 3DS (recommended)"
			if WINDOWS else "Through this PC's Wi-Fi hotspot (Windows only)", variable=self.connection, value="hotspot",
			command=self.change_connection, state="normal" if WINDOWS else "disabled").pack(anchor="w")
		ttk.Radiobutton(connect, text="Through a proxy: the 3DS stays on your Wi-Fi and uses this PC as its proxy",
			variable=self.connection, value="proxy", command=self.change_connection).pack(anchor="w", pady=(2, 0))

		# Hotspot
		self.hotspot_panel = ttk.Frame(connect, padding=(0, 10, 0, 0))
		row = ttk.Frame(self.hotspot_panel)
		row.pack(anchor="w")
		self.hotspot_button = ttk.Button(row, text="Turn on hotspot", style="Accent.TButton", command=self.toggle_hotspot)
		self.hotspot_button.pack(side="left")
		ttk.Button(row, text="Windows hotspot settings",
			command=lambda: os.startfile("ms-settings:network-mobilehotspot")).pack(side="left", padx=6)
		self.hotspot_state = ttk.Label(row)
		self.hotspot_state.pack(side="left", padx=6)
		values = ttk.Frame(self.hotspot_panel)
		values.pack(anchor="w", pady=(8, 0))
		ttk.Label(values, text="Network name:").grid(row=0, column=0, sticky="w")
		self.hotspot_ssid = ttk.Label(values, style="Big.TLabel")
		self.hotspot_ssid.grid(row=0, column=1, sticky="w", padx=(6, 24))
		ttk.Label(values, text="Password:").grid(row=0, column=2, sticky="w")
		self.hotspot_password = ttk.Label(values, style="Big.TLabel")
		self.hotspot_password.grid(row=0, column=3, sticky="w", padx=6)
		hint(self.hotspot_panel, "Turning the hotspot on points Nintendo's servers at this PC for devices on "
			"the hotspot, using the hosts file, and allows the server through the firewall. Windows asks for admin "
			"rights for that. Turning it off removes the entries again.\n\nOn the 3DS: System Settings > Internet "
			"Settings > Connection Settings > New Connection > Manual Setup > Search for an Access Point. Pick the "
			"network above, enter the password, and leave Proxy Settings on No. Save, run the connection test, "
			"then open Badge Arcade. (Once only: copy the luma folder from server/mitm/sd-card to the SD card.)"
		).pack(fill="x", pady=(8, 0))
		self.hotspot_warning = auto_wrap(ttk.Label(self.hotspot_panel, style="Warn.TLabel", justify="left"))
		self.hotspot_warning.pack(fill="x", pady=(4, 0))

		# Proxy
		self.proxy_panel = ttk.Frame(connect, padding=(0, 10, 0, 0))
		values = ttk.Frame(self.proxy_panel)
		values.pack(anchor="w")
		ttk.Label(values, text="Proxy server:").grid(row=0, column=0, sticky="w")
		self.ip_label = ttk.Label(values, style="Big.TLabel")
		self.ip_label.grid(row=0, column=1, sticky="w", padx=(6, 24))
		ttk.Label(values, text="Port:").grid(row=0, column=2, sticky="w")
		ttk.Label(values, text=str(self.proxy.port), style="Big.TLabel").grid(row=0, column=3, sticky="w", padx=6)
		hint(self.proxy_panel, "On the 3DS: System Settings > Internet Settings > Connection Settings > your "
			"connection > Change Settings > Proxy Settings > Yes > Detailed Setup. Enter the proxy server and port above, "
			"save, and run the connection test. (Once only: copy the luma folder from server/mitm/sd-card to the SD card.)"
		).pack(fill="x", pady=(6, 0))
		self.ip_warning = auto_wrap(ttk.Label(self.proxy_panel, style="Warn.TLabel", justify="left"))
		self.ip_warning.pack(fill="x", pady=(4, 0))

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
		"""Shows the update banner. install (the check when the window opens): install it now if updates
		are set to install automatically, and otherwise offer to bring over a standalone helper's work."""
		if release is None:
			self.update_banner.pack_forget()
		else:
			self.update_text.config(text=f"Version {release['version']} is available (you have {update.current_version()}).")
			self.update_banner.pack(fill="x", before=self.checks_frame)
			if install and self.settings.get("auto_update") and self.install_update(ask=False):
				return  # the manager closes once it's installed
		if install:
			self.offer_helper_import()

	def change_auto_update(self) -> None:
		self.settings["auto_update"] = self.auto_update.get()
		save_settings(self.settings)

	def install_update(self, ask: bool) -> bool:
		"""Installs the version the last check found. True once it's installing (the manager closes afterwards)."""
		release = self.settings.get("update_release")
		if not release:
			return False
		unsaved = self.helper.unsaved_machine() if self.helper else None
		if self.helper and self.helper.busy:
			if ask:
				messagebox.showinfo(TITLE, "Wait until the custom badge work in the status bar is done, then update.")
			return False
		if unsaved and not ask:
			return False  # not while you're editing: the banner stays, for later
		ours = [s for s in (self.server, self.proxy) if s.pid() and s.listening()]
		if ask and not messagebox.askyesno(TITLE, f"Install version {release['version']}?\n\n"
				+ ("The server and proxy will be stopped. " if ours else "")
				+ (f"The machine {unsaved} has changes you haven't saved, which will be lost. " if unsaved else "")
				+ "Your settings, saves, SpotPass files and keys are kept, and the replaced files are backed up. "
				"The manager closes afterwards; open it again to use the new version."):
			return False

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
			messagebox.showinfo(TITLE, f"{summary}\n\nThe manager will close now. Open it again to use the new version.")
			self.destroy()

		self.background(f"Installing version {release['version']}...", work, done)
		return True

	def offer_helper_import(self) -> None:
		"""Offers once to bring over the work of a standalone Badge Arcade Helper next to this folder."""
		if not self.helper or self.settings.get("helper_import_offered"):
			return
		found = self.helper.importable()
		if not found:
			return
		self.settings["helper_import_offered"] = True
		save_settings(self.settings)
		if messagebox.askyesno(TITLE, "The Badge Arcade Helper is part of the manager now: its Badges, "
				"Machine editor and Custom weeks tabs are here.\n\nCopy your badges, machines, weeks and settings from "
				f"{found[0]}? That folder isn't changed. (Later: Setup tab, Import from Badge Arcade Helper.)", parent=self):
			self.helper.import_from(found[0])

	# --- checklist ---

	def refresh_checklist(self) -> None:
		for widget in self.checklist_frame.winfo_children():
			widget.destroy()
		checks = install.checklist()
		for row, check in enumerate(checks):
			ttk.Label(self.checklist_frame, text="✔" if check.ok else "✘",
				style="Good.TLabel" if check.ok else "Bad.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 6), pady=2)
			ttk.Label(self.checklist_frame, text=check.name, width=46).grid(row=row, column=1, sticky="nw", pady=2)
			ttk.Label(self.checklist_frame, text=check.hint, style="Hint.TLabel", wraplength=420,
				justify="left").grid(row=row, column=2, sticky="nw", pady=2)
			if check.fix and not check.ok:
				ttk.Button(self.checklist_frame, text="Do it", command=lambda c=check: self.fix(c)).grid(row=row, column=3, padx=6)
			elif check.folder:
				ttk.Button(self.checklist_frame, text="Open folder",
					command=lambda c=check: self.open_folder(c.folder)).grid(row=row, column=3, padx=6)
		missing = sum(not check.ok for check in checks)
		self.sidebar.set_mark("Setup", f"✘ {missing}" if missing else "✔", BAD if missing else GOOD)
		if self.helper:
			self.helper.refresh_setup()

	def fix(self, check: install.Check) -> None:
		work, message = {
			"packages": (install.install_packages, "Installing the Python packages..."),
			"helper-packages": (install.install_helper_packages, "Installing numpy and pymunk for your own badges and machines..."),
			"config": (install.create_config, "Creating server/config.json..."),
			"proxy": (install.setup_proxy, "Setting up the proxy (downloads about 100 MB, a minute or two)..."),
		}[check.fix]

		def done(_):
			self.refresh_checklist()
			if check.fix == "helper-packages" and HELPER_ERROR is not None:
				messagebox.showinfo(TITLE, "Installed. Close the manager and open it again to use the "
					"Badges, Machine editor and Custom weeks tabs.")

		self.background(message, work, done)

	@staticmethod
	def open_folder(folder: Path) -> None:
		folder.mkdir(parents=True, exist_ok=True)
		try:
			open_path(folder)
		except OSError:
			messagebox.showinfo(TITLE, f"Open this folder in your file browser:\n\n{folder}")

	# --- connecting the 3DS ---

	def show_connection_panel(self) -> None:
		hotspot_mode = self.settings["connection"] == "hotspot"
		(self.proxy_panel if hotspot_mode else self.hotspot_panel).pack_forget()
		(self.hotspot_panel if hotspot_mode else self.proxy_panel).pack(fill="x", anchor="w")
		if hotspot_mode:
			# Once the window is running: the other tabs are still being built at start, and the
			# answer from refresh_hotspot's thread can only arrive while the window's loop runs
			self.after_idle(self.refresh_hotspot)

	def change_connection(self) -> None:
		mode = self.connection.get()
		if mode == self.settings["connection"]:
			return
		self.settings["connection"] = mode
		save_settings(self.settings)
		self.show_connection_panel()
		if mode == "proxy" and hotspot.hosts_ip():
			if messagebox.askyesno(TITLE, "Turn the hotspot off too, and remove its hosts entries?"):
				self.toggle_hotspot(turning_on=False)
				return  # restarts the services once it's off
		self.restart_services()

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
		if on:
			self.hotspot_state.configure(text=f"● On, {clients} device{'s' if clients != 1 else ''} connected", style="Good.TLabel")
		elif info.get("state") == "On":
			self.hotspot_state.configure(text="● On, but the hosts entries are missing: turn it off and on again",
				style="Warn.TLabel")
		else:
			self.hotspot_state.configure(text="○ Off", style="Hint.TLabel")
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
				messagebox.showwarning(TITLE, info["dns_problem"])

		self.background("Turning the hotspot on (Windows asks for admin rights)..." if turning_on
			else "Turning the hotspot off...", work, done)

	def check_leftover_hosts(self) -> None:
		"""Hotspot mode's hosts entries left behind (e.g. the PC restarted) while the hotspot is off."""
		if hotspot.hosts_ip() and not hotspot_address():
			if messagebox.askyesno(TITLE, "The hosts file still points Nintendo's servers at this PC's "
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
			messagebox.showinfo(TITLE, f"The {' and '.join(elsewhere)} was started outside this window. "
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

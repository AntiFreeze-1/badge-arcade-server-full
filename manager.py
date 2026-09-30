"""Badge Arcade Manager: one window for setup, the server, the proxy or hotspot, the
machines, your own badges and machines, free plays, letters and saves.

  python manager.py        (or double-click "Badge Arcade Manager.bat")

This file makes the window: the tabs down its left side, the status bar, and what the tabs
share. Each tab is a module in manager_ui/. Everything they do can also be done with the
command-line tools they use: install.py, server/mitm/hotspot.py, spotpass-letter/serve.py,
spotpass-letter/custom_week.py and server's "python -m badge_arcade.admin". The Badges,
Machine editor and Custom weeks tabs are the Badge Arcade Helper (helper/), which used to
be a program of its own.
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from manager_ui import TITLE, load_settings, save_settings  # first: puts the server and the tools on sys.path
import hotspot
import install
import serve
import update
from badge_arcade.config import load_config
from manager_ui.free_plays_tab import FreePlaysTab
from manager_ui.helper_tabs import HELPER_ERROR, HELPER_TABS, HelperContext, HelperTabs
from manager_ui.letters_tab import LettersTab
from manager_ui.maintenance_tab import MaintenanceTab
from manager_ui.saves_tab import SavesTab
from manager_ui.serve_week_tab import ServeWeekTab
from manager_ui.server_tab import ServerTab
from manager_ui.services import Service
from manager_ui.setup_tab import SetupTab
from manager_ui.widgets import SIDEBAR_LINE, Sidebar, add_styles, app_icon

# The tabs down the sidebar, under their headings
TABS = (
	(None, ("Setup", "Server")),
	("Play", ("Serve a week", "Free plays", "Letters")),
	("Make your own", ("Badges", "Machine editor", "Custom weeks")),
	("Admin", ("Saves", "Maintenance")),
)


class Manager(SetupTab, ServerTab, ServeWeekTab, FreePlaysTab, LettersTab, HelperTabs, SavesTab, MaintenanceTab, tk.Tk):
	def __init__(self):
		super().__init__()
		self.title(f"{TITLE} {update.current_version()}")
		screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
		width, height = min(1500, screen_width - 40), min(920, screen_height - 80)
		self.geometry(f"{width}x{height}+{(screen_width - width) // 2}+{max(0, (screen_height - height) // 2 - 20)}")
		self.minsize(1200, 720)
		app_icon(self)
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
		self.proxy_restarting = False
		self.proxy_healed: tuple[str, float] | None = None  # (hotspot IP, proxy start) of the last automatic restart
		self.weeks: list[serve.Week] = []
		self.live_week: str | None = None  # the label of the week the 3DS gets (see refresh_status)
		self.log_offsets = {}
		add_styles(self)

		self.status = tk.StringVar(value="Ready.")
		status_bar = ttk.Frame(self)
		status_bar.pack(side="bottom", fill="x")
		ttk.Separator(self).pack(side="bottom", fill="x")
		ttk.Label(status_bar, textvariable=self.status, anchor="w", padding=(12, 5)).pack(side="left", fill="x", expand=True)
		progress = ttk.Progressbar(status_bar, mode="determinate", length=160)  # the helper runs it
		progress.pack(side="right", padx=12)
		self.helper = HelperContext(self, self.status, progress) if HELPER_ERROR is None else None

		body = ttk.Frame(self)
		body.pack(fill="both", expand=True)
		self.sidebar = Sidebar(body, self.open_tab, "Badge Arcade", f"Manager {update.current_version()}")
		self.sidebar.pack(side="left", fill="y")
		tk.Frame(body, width=1, background=SIDEBAR_LINE).pack(side="left", fill="y")
		stack = ttk.Frame(body)
		stack.pack(side="left", fill="both", expand=True)
		stack.rowconfigure(0, weight=1)
		stack.columnconfigure(0, weight=1)
		for heading, names in TABS:
			if heading:
				self.sidebar.heading(heading)
			for name in names:
				self.sidebar.add(name)
		builders = {"Setup": self.build_setup_tab, "Server": self.build_server_tab, "Serve a week": self.build_serve_week_tab,
			"Free plays": self.build_free_plays_tab, "Letters": self.build_letters_tab, "Saves": self.build_saves_tab,
			"Maintenance": self.build_maintenance_tab}
		self.pages: dict[str, ttk.Frame] = {}
		for name in self.sidebar.names:
			page = self.pages[name] = ttk.Frame(stack, padding=0 if name in HELPER_TABS else 12)  # the helper's tabs have their own
			page.grid(row=0, column=0, sticky="nsew")
			if name in HELPER_TABS:
				self.build_helper_tab(page, HELPER_TABS[name])
			else:
				builders[name](page)
		for sequence, step in (("<Control-Tab>", 1), ("<Control-Shift-Tab>", -1), ("<Control-ISO_Left_Tab>", -1),
				("<Control-Next>", 1), ("<Control-Prior>", -1)):
			try:
				self.bind_all(sequence, lambda e, step=step: self.sidebar.step(step))
			except tk.TclError:
				pass  # a key this platform doesn't have
		start = self.settings.get("last_tab")
		if start not in self.pages:  # first time: Setup until everything's there, then the Server tab
			start = "Server" if all(check.ok for check in install.checklist()) else "Setup"
		self.sidebar.select(start)

		self.protocol("WM_DELETE_WINDOW", self.on_close)
		self.refresh_services()
		self.refresh_status()
		self.after(1000, self.poll_logs)
		self.after(500, self.check_leftover_hosts)
		self.after(2000, self.check_for_update)

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
				messagebox.showerror(TITLE, str(error))
			else:
				self.status.set("Done.")
				if done:
					done(result)

		threading.Thread(target=run, daemon=True).start()

	def open_tab(self, title: str) -> None:
		"""Shows a tab (the sidebar calls this when another one is picked)."""
		self.pages[title].tkraise()
		if self.settings.get("last_tab") != title:
			self.settings["last_tab"] = title  # the manager opens on it next time
			save_settings(self.settings)
		self.on_tab(title)

	def on_tab(self, title: str) -> None:
		if title in HELPER_TABS:
			if self.helper:
				self.helper.start()  # reads the archive the first time
				page = self.helper.tabs.get(HELPER_TABS[title])
				if hasattr(page, "on_show"):
					page.on_show()
			return
		if title in ("Serve a week", "Free plays"):
			problem = self.missing_files()
			if problem:
				self.status.set(f"Not set up yet: {problem} See README.md.")
				return
		if title == "Letters":
			self.refresh_letters()
			if self.key is None:
				self.status.set(f"Sending letters needs {self.missing_files()} See README.md.")
		elif title == "Serve a week":
			self.refresh_weeks()
		elif title == "Free plays":
			self.refresh_plays()
		elif title == "Saves":
			self.refresh_saves()
		elif title == "Setup":
			self.refresh_checklist()

	def on_close(self) -> None:
		unsaved = self.helper.unsaved_machine() if self.helper else None
		if unsaved and not messagebox.askyesno(TITLE, f"The machine {unsaved} has changes you haven't "
				"saved (Machine editor tab). Close anyway, and lose them?"):
			return
		if self.helper and self.helper.busy and not messagebox.askyesno(TITLE, "Your own badges and "
				"machines are still being worked on (see the status bar). Close anyway, and stop it?"):
			return
		ours = [s for s in (self.server, self.proxy) if s.pid() and s.listening()]
		hotspot_on = bool(hotspot.hosts_ip())
		if ours or hotspot_on:
			what = " and ".join(filter(None, ("the server and proxy" if ours else "", "the hotspot" if hotspot_on else "")))
			answer = messagebox.askyesnocancel(TITLE, f"Stop {what} too?\n\n"
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


if __name__ == "__main__":
	Manager().mainloop()

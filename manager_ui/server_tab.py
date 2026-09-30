"""Server tab: start and stop the server and proxy, the game date, what's live, and their activity."""

import datetime
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import serve
from badge_arcade import maintenance
from badge_arcade.config import lan_address

from . import LOG_DIR, TITLE
from .services import PROXY_EVENTS, SERVER_EVENTS, Service, port_open, public_host_fixed, tidy
from .widgets import BAD, GOOD, IDLE, WARN, hint, text_box

LIVE_ROWS = ("Machines", "Free plays", "Game date", "Maintenance", "Letter")
ERROR_LINE = re.compile(r"ERROR|Traceback|Error|error|does not trust")


class ServerTab:
	def build_server_tab(self, tab: ttk.Frame) -> None:
		services = ttk.LabelFrame(tab, text="Services", padding=10)
		services.pack(fill="x")
		self.service_labels = {}
		for row, (service, what) in enumerate(((self.server, "Game server (logins, saves, SpotPass files)"),
				(self.proxy, "3DS proxy (sends Badge Arcade's traffic to the server)"))):
			ttk.Label(services, text=what, width=48).grid(row=row, column=0, sticky="w", pady=3)
			label = ttk.Label(services, width=30)
			label.grid(row=row, column=1, sticky="w")
			self.service_labels[service.name] = label
			ttk.Button(services, text="Start", command=lambda s=service: self.start_service(s)).grid(row=row, column=2, padx=3)
			ttk.Button(services, text="Stop", command=lambda s=service: self.stop_service(s)).grid(row=row, column=3, padx=3)
		ttk.Button(services, text="Restart server", command=self.restart_server).grid(row=0, column=4, padx=3)
		self.mode_label = ttk.Label(services, style="Hint.TLabel")
		self.mode_label.grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))

		date = ttk.LabelFrame(tab, text="Game date", padding=10)
		date.pack(fill="x", pady=8)
		hint(date, "The machines and free plays are chosen for this date. \"current\" follows the real "
			"date; any week you serve is moved to it automatically.").pack(fill="x")
		row = ttk.Frame(date)
		row.pack(anchor="w", pady=(6, 0))
		self.date_var = tk.StringVar(value=str(serve.game_date() or "current"))
		ttk.Entry(row, textvariable=self.date_var, width=14).pack(side="left")
		ttk.Label(row, text="current, or YYYY-MM-DD", style="Hint.TLabel").pack(side="left", padx=6)
		ttk.Button(row, text="Current date", command=lambda: self.date_var.set("current")).pack(side="left", padx=6)
		ttk.Button(row, text="Apply and restart server", command=self.apply_date).pack(side="left")

		live = ttk.LabelFrame(tab, text="What the 3DS gets next time Badge Arcade opens", padding=10)
		live.pack(fill="x")
		self.live_grid = ttk.Frame(live)
		self.live_values = {}
		for row, key in enumerate(LIVE_ROWS):
			ttk.Label(self.live_grid, text=f"{key}:", style="Hint.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 16), pady=1)
			self.live_values[key] = tk.StringVar()
			ttk.Label(self.live_grid, textvariable=self.live_values[key]).grid(row=row, column=1, sticky="w", pady=1)
		self.live_problem = hint(live, style="Warn.TLabel")
		self.live_problem.pack(fill="x")

		activity = ttk.LabelFrame(tab, text="Activity", padding=8)
		activity.pack(fill="both", expand=True, pady=(8, 0))
		top = ttk.Frame(activity)
		top.pack(fill="x", pady=(0, 6))
		self.show_all_logs = tk.BooleanVar(value=False)
		ttk.Checkbutton(top, text="Show every log line", variable=self.show_all_logs).pack(side="left")
		ttk.Label(top, text="(only for services started from this window)", style="Hint.TLabel").pack(side="left", padx=6)
		ttk.Button(top, text="Open logs folder", command=lambda: self.open_folder(LOG_DIR)).pack(side="right")
		ttk.Button(top, text="Clear", command=self.clear_log_view).pack(side="right", padx=6)
		box, self.log_view = text_box(activity, height=10, wrap="none", font=("Consolas", 9))
		self.log_view.tag_configure("error", foreground=BAD)
		self.log_view.tag_configure("source", foreground="#8a8f98")
		box.pack(fill="both", expand=True)

	def refresh_services(self) -> None:
		running = []
		for service in (self.server, self.proxy):
			label = self.service_labels[service.name]
			if service.listening():
				running.append(service)
				label.configure(text="● Running" if service.pid() else "● Running (started elsewhere)", style="Good.TLabel")
			else:
				label.configure(text="○ Stopped", style="Hint.TLabel")
		self.sidebar.set_mark("Server", "●", GOOD if len(running) == 2 else WARN if running else IDLE)

		hotspot_mode = self.settings["connection"] == "hotspot"
		self.check_proxy_hotspot()
		self.mode_label.configure(text="The 3DS connects through this PC's hotspot (see the Setup tab)." if hotspot_mode
			else "The 3DS connects through the proxy on your Wi-Fi (see the Setup tab).")

		# The PC's IP can change (no fixed address); the server picks it up when it starts
		ip = lan_address() or "not connected to a network"
		self.ip_label.configure(text=ip)
		serving = self.server_ip() if self.server.listening() else None
		expected = self.live_hotspot_ip() or ip
		warning = ""
		if serving and serving != expected:
			if public_host_fixed():
				warning = (f"The server gives the 3DS {serving}, but it should be {expected} now. Restarting the server is recommended"
					+ ("." if hotspot_mode else f", and change the proxy server on the 3DS to {ip}."))
			elif not hotspot_mode:
				# The server gives each 3DS the address it can reach; only the 3DS's proxy setting is out of date
				warning = f"This PC's address is now {ip}: change the proxy server on the 3DS to {ip}."
		self.ip_warning.configure(text=warning)
		if warning:
			self.status.set(warning)
		self.after(3000, self.refresh_services)

	def check_proxy_hotspot(self) -> None:
		"""A proxy started before the hotspot came on only listens for 3DSs with a proxy set, so
		3DSs on the hotspot (no proxy) get no answer on port 443 and Badge Arcade's NNID login
		stalls. Restart such a proxy (started here) so it answers on the hotspot too."""
		ip = self.live_hotspot_ip()
		if (not ip or self.proxy_restarting or not self.proxy.pid() or not self.proxy.listening()
				or time.time() - self.proxy.started_at < 20 or port_open(ip, 443)):
			return
		if self.proxy_healed == (ip, self.proxy.started_at):
			# Restarted once already and still nothing on 443: don't keep restarting
			self.status.set(f"The proxy isn't answering on the hotspot ({ip}, port 443), so 3DSs on the hotspot "
				"can't log in. Another program may be using port 443; see server/logs/proxy.log.")
			return
		self.proxy_restarting = True
		self.status.set("The proxy started before the hotspot, so 3DSs on the hotspot can't log in. Restarting it...")

		def work():
			self.proxy.stop()
			for _ in range(30):
				if not self.proxy.listening():
					break
				threading.Event().wait(0.2)
			self.proxy.start()
			self.log_offsets[self.proxy.log] = self.proxy.log.stat().st_size if self.proxy.log.exists() else 0

		def done(_=None):
			self.proxy_restarting = False
			self.proxy_healed = (ip, self.proxy.started_at)
			self.status.set("Restarted the proxy for the hotspot.")

		def run():
			try:
				work()
			finally:
				self.after(0, done)

		threading.Thread(target=run, daemon=True).start()

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
			messagebox.showerror(TITLE, str(e))

	def stop_service(self, service: Service) -> None:
		if not service.listening() and not service.pid():
			self.status.set(f"The {service.name} isn't running.")
		elif service.stop():
			self.status.set(f"Stopped the {service.name}.")
		else:
			messagebox.showinfo(TITLE, f"The {service.name} was started outside this window "
				"(e.g. from a command prompt). Close its window to stop it.")

	def restart_server(self) -> bool:
		if self.server.listening() and not self.server.pid():
			messagebox.showinfo(TITLE, "The server was started outside this window. Close its "
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
			messagebox.showerror(TITLE, "Enter \"current\" or a date as YYYY-MM-DD, e.g. 2022-12-30.")
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

	def show_live(self, values: dict[str, str] | None, problem: str = "") -> None:
		"""Fills "What the 3DS gets": one line per LIVE_ROWS key, or the problem instead."""
		self.live_problem.configure(text=problem)
		if values is None:
			self.live_grid.pack_forget()
			return
		for key, var in self.live_values.items():
			var.set(values.get(key, ""))
		self.live_grid.pack(fill="x", before=self.live_problem)

	def refresh_status(self) -> None:
		problem = self.missing_files()
		if problem:
			self.show_live(None, f"Not set up yet: {problem}\nSee \"What you need to provide\" in README.md.")
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
			letter = s["letter"]
			self.live_week = s["week"]
			self.mark_live_week()
			self.show_live({
				"Machines": f"{s['week']}  ({s['week_machines']} machines, {start} to {end})",
				"Free plays": f"{plays if plays is not None else 'none'} on the game date"
					+ ("" if plays is not None else "  (use the Free plays tab)"),
				"Game date": f"{today}" + ("  (current date)" if not s["game_date"] else ""),
				"Maintenance": maintenance.load(self.maintenance_path()).describe(),
				"Letter": f"\"{letter.title}\"" + ("  (downloaded)" if letter.downloaded else "  (waiting for the 3DS)")
					if letter else "none  (use the Letters tab)",
			})

		self.background("Checking what's live...", work, done)

	def clear_log_view(self) -> None:
		self.log_view.configure(state="normal")
		self.log_view.delete("1.0", "end")
		self.log_view.configure(state="disabled")

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
					self.log_view.insert("end", f"[{service.name}] ", "source")
					self.log_view.insert("end", f"{line}\n", "error" if ERROR_LINE.search(line) else ())
				if int(self.log_view.index("end-1c").split(".")[0]) > 3000:
					self.log_view.delete("1.0", "1000.0")
				self.log_view.see("end")
				self.log_view.configure(state="disabled")
		self.after(1000, self.poll_logs)

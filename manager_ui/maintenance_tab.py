"""Maintenance tab: turn logins away now or for a scheduled window, without restarting the server."""

import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import serve
from badge_arcade import maintenance
from badge_arcade.config import load_config

from . import SERVER_DIR, TITLE
from .widgets import HINT, WARN, hint


class MaintenanceTab:
	def build_maintenance_tab(self, tab: ttk.Frame) -> None:
		hint(tab, "While maintenance is on, the 3DS can't log in to Badge Arcade and shows the system's "
			"\"under maintenance\" error. It takes effect at the next login; no restart needed. Players already in "
			"the game carry on until they reconnect.").pack(fill="x")
		self.maintenance_text = tk.StringVar()
		self.maintenance_label = ttk.Label(tab, textvariable=self.maintenance_text, style="Big.TLabel")
		self.maintenance_label.pack(anchor="w", pady=(10, 8))

		mode = ttk.LabelFrame(tab, text="Maintenance", padding=10)
		mode.pack(fill="x")
		self.maintenance_mode = tk.StringVar(value="off")
		self.maintenance_until = tk.StringVar()
		self.maintenance_start = tk.StringVar()
		self.maintenance_end = tk.StringVar()
		ttk.Radiobutton(mode, text="Off", variable=self.maintenance_mode, value="off").grid(row=0, column=0, sticky="w")
		ttk.Radiobutton(mode, text="On now, until", variable=self.maintenance_mode, value="on").grid(row=1, column=0, sticky="w", pady=4)
		ttk.Entry(mode, textvariable=self.maintenance_until, width=18).grid(row=1, column=1, sticky="w", padx=6)
		ttk.Label(mode, text="optional; empty = until you turn it off", style="Hint.TLabel").grid(row=1, column=2, columnspan=3, sticky="w")
		ttk.Radiobutton(mode, text="Scheduled, from", variable=self.maintenance_mode, value="scheduled").grid(row=2, column=0, sticky="w")
		ttk.Entry(mode, textvariable=self.maintenance_start, width=18).grid(row=2, column=1, sticky="w", padx=6)
		ttk.Label(mode, text="to").grid(row=2, column=2, sticky="w")
		ttk.Entry(mode, textvariable=self.maintenance_end, width=18).grid(row=2, column=3, sticky="w", padx=6)
		ttk.Label(mode, text="Dates and times as YYYY-MM-DD HH:MM (this PC's time zone).", style="Hint.TLabel").grid(
			row=3, column=0, columnspan=5, sticky="w", pady=(6, 0))

		self.maintenance_advanced_shown = tk.BooleanVar(value=False)
		ttk.Checkbutton(tab, text="Advanced: the game's own maintenance status", variable=self.maintenance_advanced_shown,
			command=self.show_maintenance_advanced).pack(anchor="w", pady=(10, 0))
		self.maintenance_advanced = ttk.Frame(tab, padding=(20, 4, 0, 0))
		self.maintenance_advanced.columnconfigure(3, weight=1)  # so the explanation gets the whole width
		hint(self.maintenance_advanced, "Values Badge Arcade reads from GetMaintenanceStatus when it connects. What "
			"the game does with them is unknown (0xFFFF, 0, true is what Pretendo's server sends); empty = the value in "
			"server/config.json.").grid(row=0, column=0, columnspan=4, sticky="ew")
		self.maintenance_status = tk.StringVar()
		self.maintenance_time = tk.StringVar()
		self.maintenance_success = tk.StringVar(value="default")
		ttk.Label(self.maintenance_advanced, text="Status:").grid(row=1, column=0, sticky="w", pady=4)
		ttk.Entry(self.maintenance_advanced, textvariable=self.maintenance_status, width=10).grid(row=1, column=1, sticky="w", padx=6)
		ttk.Label(self.maintenance_advanced, text="e.g. 0xFFFF", style="Hint.TLabel").grid(row=1, column=2, sticky="w")
		ttk.Label(self.maintenance_advanced, text="Time:").grid(row=2, column=0, sticky="w")
		ttk.Entry(self.maintenance_advanced, textvariable=self.maintenance_time, width=10).grid(row=2, column=1, sticky="w", padx=6)
		ttk.Label(self.maintenance_advanced, text="Success:").grid(row=3, column=0, sticky="w", pady=4)
		ttk.Combobox(self.maintenance_advanced, textvariable=self.maintenance_success, values=("default", "true", "false"),
			width=8, state="readonly").grid(row=3, column=1, sticky="w", padx=6)
		ttk.Button(self.maintenance_advanced, text="Reset to defaults", command=self.reset_maintenance_advanced).grid(
			row=3, column=2, sticky="w")

		buttons = self.maintenance_buttons = ttk.Frame(tab)
		buttons.pack(anchor="w", pady=(12, 0))
		ttk.Button(buttons, text="Apply", style="Accent.TButton", command=self.apply_maintenance).pack(side="left")
		ttk.Button(buttons, text="Reload", command=self.load_maintenance).pack(side="left", padx=6)

		self.load_maintenance()
		self.after(5000, self.refresh_maintenance_text)

	def maintenance_path(self) -> Path | None:
		try:
			return load_config(serve.SERVER_CONFIG).maintenance_path
		except (OSError, ValueError):
			return SERVER_DIR / "maintenance.json"

	def show_maintenance_advanced(self) -> None:
		if self.maintenance_advanced_shown.get():
			self.maintenance_advanced.pack(anchor="w", fill="x", before=self.maintenance_buttons)
		else:
			self.maintenance_advanced.pack_forget()

	def show_maintenance_state(self, state: maintenance.MaintenanceState) -> None:
		"""The status line, and a dot next to the tab's name: amber while the 3DS is turned away."""
		self.maintenance_text.set(f"Maintenance: {state.describe()}")
		active = state.active()
		self.maintenance_label.configure(style="BigWarn.TLabel" if active else "Big.TLabel")
		self.sidebar.set_mark("Maintenance", "●" if active else "○" if state.enabled else "", WARN if active else HINT)

	def load_maintenance(self) -> None:
		"""Fills the tab from the saved state."""
		state = maintenance.load(self.maintenance_path())
		shown = lambda moment: moment.astimezone().strftime("%Y-%m-%d %H:%M") if moment else ""  # noqa: E731
		self.maintenance_mode.set("off" if not state.enabled else "scheduled" if state.start else "on")
		self.maintenance_until.set(shown(state.end) if state.enabled and not state.start else "")
		self.maintenance_start.set(shown(state.start))
		self.maintenance_end.set(shown(state.end) if state.start else "")
		self.maintenance_status.set("" if state.status is None else f"{state.status:#06x}")
		self.maintenance_time.set("" if state.time is None else str(state.time))
		self.maintenance_success.set("default" if state.is_success is None else str(state.is_success).lower())
		if state.status is not None or state.time is not None or state.is_success is not None:
			self.maintenance_advanced_shown.set(True)
			self.show_maintenance_advanced()
		self.show_maintenance_state(state)

	def refresh_maintenance_text(self) -> None:
		"""Keeps the status line current, e.g. when a scheduled window starts or ends."""
		try:
			self.show_maintenance_state(maintenance.load(self.maintenance_path()))
		finally:
			self.after(5000, self.refresh_maintenance_text)

	def reset_maintenance_advanced(self) -> None:
		self.maintenance_status.set("")
		self.maintenance_time.set("")
		self.maintenance_success.set("default")

	def maintenance_from_tab(self) -> maintenance.MaintenanceState:
		def moment(text: str, what: str) -> datetime.datetime | None:
			text = text.strip()
			if not text:
				return None
			try:
				return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M").astimezone()
			except ValueError:
				raise ValueError(f"Enter the {what} as YYYY-MM-DD HH:MM, e.g. 2026-10-01 18:00.") from None

		mode = self.maintenance_mode.get()
		state = maintenance.MaintenanceState(enabled=mode != "off")
		if mode == "on":
			state.end = moment(self.maintenance_until.get(), "end")
			if state.end and state.end <= maintenance.now():
				raise ValueError("That end time has already passed.")
		elif mode == "scheduled":
			state.start = moment(self.maintenance_start.get(), "start")
			state.end = moment(self.maintenance_end.get(), "end")
			if state.start is None:
				raise ValueError("A scheduled maintenance needs a start time (or choose \"On now\").")
		status, time_value = self.maintenance_status.get().strip(), self.maintenance_time.get().strip()
		try:
			state.status = int(status, 0) if status else None
			state.time = int(time_value, 0) if time_value else None
		except ValueError:
			raise ValueError("Status and time have to be numbers (e.g. 0xFFFF, 0).") from None
		success = self.maintenance_success.get()
		state.is_success = None if success == "default" else success == "true"
		maintenance.MaintenanceState.from_json(state.to_json())  # range checks
		return state

	def apply_maintenance(self) -> None:
		try:
			state = self.maintenance_from_tab()
			maintenance.save(self.maintenance_path(), state)
		except (ValueError, OSError) as e:
			messagebox.showerror(TITLE, str(e))
			return
		self.show_maintenance_state(state)
		self.status.set(f"Maintenance: {state.describe()}.")
		self.refresh_status()

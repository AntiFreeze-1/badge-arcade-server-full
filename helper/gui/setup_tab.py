"""The custom badges part of the manager's Setup tab: where the helper's files are, and
whether everything it needs is there."""

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from bahelper import boss, importer
from bahelper.archive import ALLBADGE_NAME, BASE_WEEK_NAME, PRISTINE_ALLBADGE
from bahelper.serving import Server, key_path

FIELDS = (
	("boot9", "boot9.bin (or the SpotPass key)", "file",
		"Empty: spotpass-letter/boot9.bin, the one the checklist above looks for."),
	("spotpass_dir", "Nintendo's SpotPass files", "folder",
		f"Empty: other/. The folder with {BASE_WEEK_NAME}, {ALLBADGE_NAME} and more archived weeks (more weeks, "
		"more machines to choose from)."),
	("extra_dir", "Extra SpotPass files", "folder",
		"Other weeks or regions' files, any name, to take badges, backgrounds and machines from. Empty: an \"extra\" "
		"folder in other/ or helper/ if there is one."),
)


class HelperSettings(ttk.Frame):
	def __init__(self, parent, app):
		super().__init__(parent)
		self.app = app
		form = ttk.Frame(self)
		form.pack(fill="x")
		self.vars = {}
		self.hints = []   # labels that wrap to the frame's width (see rewrap)
		for row, (key, label, kind, hint) in enumerate(FIELDS):
			ttk.Label(form, text=label + ":").grid(row=2 * row, column=0, sticky="w", pady=(4, 0))
			self.vars[key] = tk.StringVar()
			ttk.Entry(form, textvariable=self.vars[key], width=80).grid(row=2 * row, column=1, sticky="we", padx=6, pady=(4, 0))
			ttk.Button(form, text="Browse...", command=lambda k=key, kind=kind: self.browse(k, kind)).grid(
				row=2 * row, column=2, pady=(4, 0))
			self.hints.append(ttk.Label(form, text=hint, style="Hint.TLabel", wraplength=700))
			self.hints[-1].grid(row=2 * row + 1, column=1, columnspan=2, sticky="w", padx=6)
		form.columnconfigure(1, weight=1)
		self.bind("<Configure>", lambda e: self.rewrap())
		self.show_settings()

		buttons = ttk.Frame(self)
		buttons.pack(fill="x", pady=(8, 4))
		ttk.Button(buttons, text="Save and reload", command=self.apply).pack(side="left")
		ttk.Button(buttons, text="Open workspace folder", command=lambda: self.open_folder(app.workspace.root)).pack(
			side="left", padx=6)
		ttk.Button(buttons, text="Import from Badge Arcade Helper...", command=self.import_old).pack(side="left")
		ttk.Label(buttons, text="Your badges, machines and weeks are kept in helper/workspace.", style="Hint.TLabel").pack(
			side="left", padx=6)
		self.checks = ttk.Frame(self)
		self.checks.pack(fill="x")

	def show_settings(self) -> None:
		for key, var in self.vars.items():
			var.set(getattr(self.app.settings, key))

	def rewrap(self, force: bool = False) -> None:
		"""Wraps the hints and checks to the width there is (the window can be narrow)."""
		width = self.winfo_width()
		if width <= 1 or (width == getattr(self, "wrapped_at", 0) and not force):
			return
		self.wrapped_at = width
		for label in self.hints:
			label.configure(wraplength=max(300, width - 230))
		for label in self.checks.winfo_children():
			for text in label.winfo_children()[1:]:
				text.configure(wraplength=max(300, width - 40))

	def server(self) -> Server:
		return self.app.server or Server(self.app.root_dir, self.app.settings, b"", self.app.install_dir)

	@staticmethod
	def open_folder(folder: Path) -> None:
		folder.mkdir(parents=True, exist_ok=True)
		os.startfile(folder)  # Windows

	def browse(self, key: str, kind: str) -> None:
		path = (filedialog.askdirectory(parent=self) if kind == "folder"
			else filedialog.askopenfilename(parent=self, filetypes=[("All files", "*.*")]))
		if path:
			self.vars[key].set(path)

	def apply(self) -> None:
		for key, var in self.vars.items():
			setattr(self.app.settings, key, var.get().strip())
		self.app.save_settings()
		self.app.started = True
		self.app.load_archive()
		self.refresh()

	def import_old(self) -> None:
		found = importer.find_old_helpers(self.app.install_dir)
		folder = filedialog.askdirectory(parent=self, title="The Badge Arcade Helper folder to copy your work from",
			initialdir=str(found[0] if found else self.app.install_dir.parent), mustexist=True)
		if folder:
			self.app.import_from(Path(folder))

	def refresh(self) -> None:
		for child in self.checks.winfo_children():
			child.destroy()
		settings = self.app.settings
		server = self.server()
		checks = []
		path = key_path(settings, self.app.install_dir)
		if path is None:
			checks.append((False, "SpotPass key: put boot9.bin in spotpass-letter (see the checklist above) or choose it here."))
		elif settings.boot9:  # the server's boot9.bin is on the checklist above already
			try:
				boss.load_key(path)
				checks.append((True, f"SpotPass key: from {path}"))
			except (OSError, ValueError) as e:
				checks.append((False, f"SpotPass key: {e}"))
		other = server.archive_dir
		if settings.spotpass_dir:
			checks.append(((other / BASE_WEEK_NAME).exists(), f"{BASE_WEEK_NAME} in {other} (the base for every week)"))
			has_allbadge = (other / PRISTINE_ALLBADGE).exists() or (other / ALLBADGE_NAME).exists()
			checks.append((has_allbadge, f"{ALLBADGE_NAME} in {other} (every badge's picture)"))
		weeks = [p for p in other.glob("data_v131*") if p.name != "data_v131.dat.boss"]
		extra = server.extra_dirs
		checks.append((len(weeks) > 1, f"{len(weeks)} archived weeks in {other} (more weeks = more machines to choose "
			"from). Extra SpotPass files: " + (", ".join(str(p) for p in extra) if extra else "none (optional)") + "."))
		archive = self.app.archive
		if archive is not None:
			missing = archive.missing_series()
			checks.append((True, f"Archive: {len(archive.badges)} badges, {len(archive.machines)} machines, "
				f"{len(archive.cabinets())} backgrounds."
				+ (f" Series with no badges in your files: {', '.join(t for t, _, _ in missing)}." if missing else "")))
			checks.append((True, self.app.server.status().replace("\n", "  ")))
		elif not self.app.started:
			checks.append((True, "The archive is read the first time you open Badges, Machine editor or Custom weeks."))
		for ok, text in checks:
			row = ttk.Frame(self.checks)
			row.pack(fill="x", anchor="w")
			ttk.Label(row, text="✔" if ok else "✘", style="Good.TLabel" if ok else "Bad.TLabel", width=3).pack(side="left")
			ttk.Label(row, text=text, wraplength=900, justify="left").pack(side="left")
		self.rewrap(force=True)

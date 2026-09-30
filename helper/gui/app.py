"""The helper inside the manager's window: its settings, the archive, and its tabs.

manager.py makes one HelperContext and puts the helper's tabs (Badges, Machine editor,
Custom weeks) in its own notebook, and its settings (setup_tab.HelperSettings) on its Setup
tab. The tabs reach everything through it: it's their `app`.
"""

import threading
import traceback
from pathlib import Path
from tkinter import messagebox, ttk

from bahelper import boss, importer
from bahelper.archive import Archive
from bahelper.serving import Server, Settings, key_path
from bahelper.workspace import Workspace

from .common import TITLE, TREE_STYLE, Pictures

ROOT = Path(__file__).resolve().parent.parent   # helper/
INSTALL_DIR = ROOT.parent                        # the server: manager.py, other/, spotpass-letter/
SETTINGS_FILE = ROOT / importer.SETTINGS_NAME


def add_styles(window) -> None:
	"""The helper's styles, next to the manager's (manager_ui/widgets.py: the same colours)."""
	style = ttk.Style(window)
	style.configure("Bad.TLabel", foreground="#b3261e")
	style.configure("Good.TLabel", foreground="#1a7f37")
	style.configure(TREE_STYLE, rowheight=36)


class HelperContext:
	def __init__(self, window, status, progress: ttk.Progressbar):
		self.window = window        # the manager: dialogs' parent; runs callbacks on the UI thread
		self.status = status        # the manager's status bar text
		self.progress = progress    # and its progress bar
		self.root_dir = ROOT
		self.install_dir = INSTALL_DIR
		self.settings = Settings.load(SETTINGS_FILE)
		self.workspace = Workspace(ROOT / "workspace")
		self.key: bytes | None = None
		self.archive: Archive | None = None
		self.pictures: Pictures | None = None
		self.server: Server | None = None
		self.archive_listeners = []
		self.tabs = {}   # "Setup" (HelperSettings), "Badges", "Machines", "Weeks"
		self.started = False
		self.jobs = 0    # background work running
		# Reading the archive and importing an old helper's workspace take turns: both write to it
		self.loading = False
		self.importing = False
		self.pending_import: Path | None = None
		add_styles(window)

	# ----- background work -----

	@property
	def busy(self) -> bool:
		"""Whether work is running that closing the window would cut off (copying, building, allbadge)."""
		return self.jobs > 0

	def background(self, message: str, work, done=None, quiet: bool = False, after=None) -> None:
		"""Runs work(progress) off the UI thread, then after() and done(result) on it. progress(text)
		updates the status bar. Errors are shown in a dialog (and done isn't called)."""
		self.status.set(message)
		self.window.config(cursor="watch")
		self.progress.configure(mode="indeterminate")
		self.progress.start(12)
		self.jobs += 1

		def progress(text: str) -> None:
			self.window.after(0, self.status.set, text)

		def run():
			try:
				result, error = work(progress), None
			except Exception as e:  # shown to the user
				traceback.print_exc()
				result, error = None, e
			self.window.after(0, finish, result, error)

		def finish(result, error):
			self.jobs -= 1
			self.window.config(cursor="")
			if not self.jobs:
				self.progress.stop()
				self.progress.configure(mode="determinate", value=0)  # an empty bar while idle
			if after:
				after()
			if error:
				self.status.set("Something went wrong.")
				messagebox.showerror(TITLE, str(error) or type(error).__name__, parent=self.window)
			else:
				self.status.set("Ready." if quiet else "Done.")
				if done:
					done(result)

		threading.Thread(target=run, daemon=True).start()

	# ----- archive -----

	def start(self) -> None:
		"""Reads the archive the first time one of the helper's tabs is opened."""
		if not self.started:
			self.started = True
			self.load_archive()

	def load_archive(self) -> None:
		if self.importing:
			return  # the import reads it again when it's done
		self.archive = None
		path = key_path(self.settings, INSTALL_DIR)
		try:
			self.key = boss.load_key(path) if path else None
		except (OSError, ValueError) as e:
			self.key = None
			self.status.set(f"The SpotPass key for your own badges couldn't be read: {e}")
		if self.key is None:
			self.refresh_setup()
			if path is None:
				self.status.set("Your own badges and machines need boot9.bin: see the Setup tab.")
			return
		self.server = Server(ROOT, self.settings, self.key, INSTALL_DIR)
		other = self.server.archive_dir
		extra = self.server.extra_dirs

		def work(progress):
			return Archive(other, self.key, self.workspace.root / "cache", progress, extra)

		def done(archive):
			self.archive = archive
			moved = self.workspace.renumber_machines(archive.ids["machine"])
			shortened = self.workspace.shorten_names()
			renamed = self.workspace.align_machine_names()
			self.pictures = Pictures(archive, self.workspace)
			self.pictures.make_custom = lambda badge: self.week_builder().make_custom_badge(badge)
			self.status.set(f"Ready: {len(archive.badges)} Nintendo badges and {len(archive.machines)} machines "
				f"in the archive, {len(self.workspace.badges())} badges and {len(self.workspace.machines())} machines of your own.")
			if moved:
				messagebox.showinfo(TITLE, "Your machines got new IDs, next to Nintendo's (older versions of the helper "
					"gave them IDs the game's collection may not group into sets): " + ", ".join(moved) + ".\n\nBuild and "
					"serve your week again to send them to the 3DS.", parent=self.window)
			if shortened:
				messagebox.showinfo(TITLE, "Some of your sets had names longer than any of Nintendo's, which can stop "
					"their machines from loading in the game. They now have shorter ones (their badges too):\n\n"
					+ "\n".join(f"{old} -> {new}" for old, new in shortened)
					+ "\n\nTheir titles in the game don't change.", parent=self.window)
			if renamed:
				messagebox.showinfo(TITLE, "Your machines are now named after their set, the way Nintendo's are named "
					"after theirs (Amiibo_000 in the amiibo collection), in at most 15 characters:\n\n"
					+ "\n".join(f"{old} -> {new}" for old, new in renamed)
					+ "\n\nYour weeks were updated. Build and serve again to send them to the 3DS.", parent=self.window)
			for listener in self.archive_listeners:
				listener()
			self.refresh_setup()

		self.loading = True
		self.background("Reading the archived SpotPass data...", work, done, quiet=True, after=self.loaded)

	def loaded(self) -> None:
		self.loading = False
		if self.pending_import:
			folder, self.pending_import = self.pending_import, None
			self.import_from(folder)

	def week_builder(self):
		"""The week builder for the loaded archive (made on first use)."""
		from bahelper.week import WeekBuilder
		if getattr(self, "_builder", None) is None or self._builder.archive is not self.archive:
			self._builder = WeekBuilder(self.archive, self.workspace)
		return self._builder

	def when_ready(self, listener) -> None:
		"""Calls listener() whenever the archive has (re)loaded."""
		self.archive_listeners.append(listener)
		if self.archive:
			listener()

	def require_archive(self) -> bool:
		if self.archive is None:
			messagebox.showinfo(TITLE, "The archive isn't loaded yet. Check the Setup tab's part about your own badges and machines.",
				parent=self.window)
			return False
		return True

	def save_settings(self) -> None:
		self.settings.save(SETTINGS_FILE)

	def refresh_setup(self) -> None:
		if "Setup" in self.tabs:
			self.tabs["Setup"].refresh()

	def unsaved_machine(self) -> str | None:
		"""The title of a machine with unsaved changes in the Machine editor, if there is one."""
		tab = self.tabs.get("Machines")
		if tab is not None and tab.dirty and tab.cm:
			return tab.cm.title
		return None

	# ----- the standalone helper's work -----

	def importable(self) -> list[Path]:
		"""Old standalone helpers next to the server, if this helper has nothing of its own yet."""
		if importer.has_content(self.workspace.root) or SETTINGS_FILE.exists():
			return []
		return importer.find_old_helpers(INSTALL_DIR)

	def import_from(self, folder: Path) -> None:
		"""Copies an old helper's work here, then reloads (after the archive, if it's being read)."""
		if self.importing:
			return
		if self.loading:
			self.pending_import = folder
			self.status.set(f"Copying your work from {folder} once the archive is read...")
			return
		self.importing = True

		def imported() -> None:
			self.importing = False

		def work(progress):
			return importer.import_helper(folder, ROOT, INSTALL_DIR)

		def done(summary):
			self.settings = Settings.load(SETTINGS_FILE)
			self.workspace = Workspace(ROOT / "workspace")
			if "Setup" in self.tabs:
				self.tabs["Setup"].show_settings()
			messagebox.showinfo(TITLE, summary, parent=self.window)
			if self.started:
				self.load_archive()
			else:
				self.refresh_setup()

		self.background(f"Copying your work from {folder}...", work, done, after=imported)

"""Badges, Machine editor and Custom weeks tabs: the Badge Arcade Helper (helper/gui), which used
to be a program of its own."""

from tkinter import ttk

import install

from .widgets import hint

# The custom badge tabs need numpy and pymunk, which an update may not have been able to
# install: without them the rest of the manager still works, and those tabs offer to install them
try:
	import numpy, pymunk  # noqa: E401, F401 (pymunk: the physics check imports it only when it runs)
	from gui.app import HelperContext
	from gui.badges_tab import BadgesTab
	from gui.machines_tab import MachinesTab
	from gui.setup_tab import HelperSettings
	from gui.weeks_tab import WeeksTab
	HELPER_ERROR: ImportError | None = None
except ImportError as e:
	HELPER_ERROR = e
	HelperContext = BadgesTab = MachinesTab = HelperSettings = WeeksTab = None
# Tab title -> the helper's own name for it
HELPER_TABS = {"Badges": "Badges", "Machine editor": "Machines", "Custom weeks": "Weeks"}


class HelperTabs:
	def build_helper_tab(self, tab: ttk.Frame, name: str) -> None:
		if self.helper is None:
			missing = ttk.Frame(tab, padding=10)
			missing.pack(fill="both", expand=True)
			self.build_helper_missing(missing)
			return
		page = {"Badges": BadgesTab, "Machines": MachinesTab, "Weeks": WeeksTab}[name](tab, self.helper)
		page.pack(fill="both", expand=True)
		self.helper.tabs[name] = page

	def build_helper_missing(self, frame: ttk.Frame) -> None:
		"""In place of the helper's tabs and settings while its packages aren't installed."""
		hint(frame, f"Making your own badges and machines needs Python packages that aren't installed yet "
			f"({HELPER_ERROR}).", style="TLabel").pack(fill="x")
		ttk.Button(frame, text="Install them", style="Accent.TButton",
			command=lambda: self.fix(install.Check("", False, "", "helper-packages"))).pack(anchor="w", pady=6)

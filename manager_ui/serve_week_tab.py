"""Serve a week tab: every archived Nintendo week and your custom weeks; pick one and serve it."""

from collections import Counter
import datetime
from tkinter import messagebox, ttk

import serve
from custom_week import series as series_of

from . import TITLE
from .widgets import FONT, GOOD, hint, scrolled, set_text, stripe, text_box

# Internal series codes -> names (from the badge files; a few are best guesses). The helper's
# bahelper.archive has the same table, but importing it needs numpy
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
LIVE_MARK = "   ● live"


def series_name(code: str) -> str:
	return SERIES_NAMES.get(code, code)


class ServeWeekTab:
	def build_serve_week_tab(self, tab: ttk.Frame) -> None:
		hint(tab, "Pick a week of machines and press Serve this week. Then fully close and reopen Badge Arcade "
			"on the 3DS; the new week downloads during \"Downloading Data\". Your custom weeks (from the Custom weeks "
			"tab) are listed here too.").pack(fill="x")
		body = ttk.Frame(tab)
		body.pack(fill="both", expand=True, pady=8)
		box, self.week_tree = scrolled(body, ttk.Treeview, columns=("dates", "machines"), show="tree headings",
			selectmode="browse", height=12)
		self.week_tree.heading("#0", text="Week", anchor="w")
		self.week_tree.heading("dates", text="Original dates", anchor="w")
		self.week_tree.heading("machines", text="Machines", anchor="e")
		self.week_tree.column("#0", width=360)
		self.week_tree.column("dates", width=190)
		self.week_tree.column("machines", width=80, anchor="e")
		self.week_tree.tag_configure("live", foreground=GOOD, font=(FONT, 9, "bold"))
		box.pack(side="left", fill="both", expand=True)
		self.week_tree.bind("<<TreeviewSelect>>", lambda e: self.show_week())
		box, self.week_details = text_box(body, width=44)
		box.pack(side="left", fill="both", padx=(8, 0))
		buttons = ttk.Frame(tab)
		buttons.pack(fill="x")
		ttk.Button(buttons, text="Serve this week", style="Accent.TButton", command=self.serve_selected_week).pack(side="left")
		ttk.Button(buttons, text="Delete custom week", command=self.delete_selected_week).pack(side="left", padx=6)
		ttk.Button(buttons, text="Refresh", command=self.refresh_weeks).pack(side="left")

	def refresh_weeks(self) -> None:
		def done(weeks):
			self.weeks = weeks
			self.week_tree.delete(*self.week_tree.get_children())
			for index, week in enumerate(weeks):
				dates = f"{week.start} to {week.end - datetime.timedelta(days=1)}" if week.start else ""
				self.week_tree.insert("", "end", iid=str(index), text=week.label, values=(dates, week.machines))
			self.mark_live_week()

		self.background("Reading weeks...", lambda: serve.list_weeks(self.key), done)

	def mark_live_week(self) -> None:
		"""Shows which week the 3DS gets (self.live_week, from the Server tab's refresh_status)."""
		for index, week in enumerate(self.weeks):
			iid = str(index)
			if not self.week_tree.exists(iid):
				continue
			live = week.label == self.live_week
			self.week_tree.item(iid, text=week.label + (LIVE_MARK if live else ""), tags=["live"] if live else [])
		stripe(self.week_tree)

	def selected_week(self) -> serve.Week | None:
		selection = self.week_tree.selection()
		return self.weeks[int(selection[0])] if selection else None

	def show_week(self) -> None:
		week = self.selected_week()
		if not week:
			return
		names = week.setups or serve.week_machine_names(week.path, self.key)
		counts = Counter(series_of(n) for n in names)
		text = [week.label + ("  (live now)" if week.label == self.live_week else ""), ""]
		text += [f"{series_name(code)}: {count}" for code, count in counts.most_common()]
		set_text(self.week_details, "\n".join(text))

	def serve_selected_week(self) -> None:
		week = self.selected_week()
		if not week:
			messagebox.showinfo(TITLE, "Pick a week first.")
			return

		def done(message):
			self.refresh_status()
			self.show_week()
			messagebox.showinfo(TITLE, message + "\n\nNow fully close and reopen Badge Arcade.")

		self.background("Serving the week...", lambda: serve.serve_week(week, self.key), done)

	def delete_selected_week(self) -> None:
		week = self.selected_week()
		if not week or not week.custom:
			messagebox.showinfo(TITLE, "Pick a custom week to delete (Nintendo's weeks can't be deleted here).")
			return
		if messagebox.askyesno(TITLE, f"Delete {week.label}?"):
			serve.delete_custom_week(week)
			self.refresh_weeks()

"""Free plays tab: give free plays for a day, and see which campaigns the saves have collected."""

import datetime
import tkinter as tk
from tkinter import messagebox, ttk

import serve

from . import TITLE
from .widgets import FONT, GOOD, hint, scrolled, stripe


class FreePlaysTab:
	def build_free_plays_tab(self, tab: ttk.Frame) -> None:
		hint(tab, "Free plays are handed out once per daily campaign. Giving free plays makes new "
			"campaigns, so the game pays out again today. Reopen Badge Arcade to collect them.").pack(fill="x")
		give = ttk.LabelFrame(tab, text="Give free plays", padding=10)
		give.pack(fill="x", pady=8)
		row = ttk.Frame(give)
		row.pack(anchor="w")
		ttk.Label(row, text="Free plays:").pack(side="left")
		self.plays = tk.IntVar(value=10)
		ttk.Spinbox(row, from_=1, to=99, textvariable=self.plays, width=5).pack(side="left", padx=6)
		ttk.Label(row, text="on").pack(side="left", padx=(6, 0))
		self.plays_date = tk.StringVar(value=str(serve.default_free_play_date()))
		ttk.Entry(row, textvariable=self.plays_date, width=12).pack(side="left", padx=6)
		ttk.Button(row, text="Game date", command=lambda: self.plays_date.set(str(serve.default_free_play_date()))).pack(side="left")
		ttk.Button(row, text="Real today", command=lambda: self.plays_date.set(str(datetime.date.today()))).pack(side="left", padx=4)
		ttk.Button(row, text="Give free plays", style="Accent.TButton", command=self.give_plays).pack(side="left", padx=(12, 0))

		campaigns = ttk.LabelFrame(tab, text="Campaigns", padding=10)
		campaigns.pack(fill="both", expand=True)
		box, self.campaign_tree = scrolled(campaigns, ttk.Treeview, columns=("dates", "plays", "state"), show="headings", height=9)
		for column, title, width in (("dates", "Campaign (UTC)", 340), ("plays", "Plays", 70), ("state", "On your save", 220)):
			self.campaign_tree.heading(column, text=title, anchor="w")
			self.campaign_tree.column(column, width=width)
		self.campaign_tree.tag_configure("today", foreground=GOOD, font=(FONT, 9, "bold"))
		box.pack(fill="both", expand=True)
		ttk.Button(campaigns, text="Refresh", command=self.refresh_plays).pack(anchor="w", pady=(8, 0))

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
				current = bool(today and begin.date() <= today < end.date())
				self.campaign_tree.insert("", "end", tags=["today"] if current else [], values=(
					f"{begin:%Y-%m-%d %H:%M} to {end:%m-%d %H:%M}" + ("   ● game date" if current else ""), plays, state))
			stripe(self.campaign_tree)

		self.background("Reading free plays...", work, done)

	def give_plays(self) -> None:
		plays = self.plays.get()
		try:
			date = datetime.date.fromisoformat(self.plays_date.get().strip())
		except ValueError:
			messagebox.showerror(TITLE, "Enter the day as YYYY-MM-DD, e.g. 2022-12-30.")
			return

		def done(message):
			self.refresh_plays()
			self.refresh_status()
			messagebox.showinfo(TITLE, message + "\n\nNow fully close and reopen Badge Arcade.")

		self.background("Making free plays...",
			lambda: serve.give_free_plays(plays, self.key, date, self.console_region).splitlines()[0], done)

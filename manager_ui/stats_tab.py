"""Stats tab: how much each console has played, and the play reports of the last two weeks."""

import datetime
import math
import tkinter as tk
from tkinter import ttk

from badge_arcade import admin

from .widgets import ACCENT, FONT, HINT, SIDEBAR_LINE, STRIPE, TEXT, hint, scrolled, stripe

CHART_DAYS = 14
GRID = "#e6e8ec"  # gridlines: one step off the white surface
BAR_WIDTH = 24  # at most: the rest of each day's slot stays empty
CORNER = 4  # the bars' rounded tops
HOVER_HINT = "Point at a day to see its play reports."


def nice_step(top: int, ticks: int = 4) -> int:
	"""The smallest 1, 2 or 5 times a power of ten that marks 0 to top in at most `ticks` steps."""
	magnitude = 10 ** max(0, int(math.log10(max(top, 1))) - 1)
	while True:
		for step in (1, 2, 5):
			if math.ceil(max(top, 1) / (step * magnitude)) <= ticks:
				return step * magnitude
		magnitude *= 10


class StatsTab:
	def build_stats_tab(self, tab: ttk.Frame) -> None:
		hint(tab, "How much each console has played. Badge Arcade sends the server a play report while it's played "
			"(what's in them isn't known yet, so they're counted) and uploads its save. Days are in this PC's time "
			"zone; a streak is days played in a row, up to today or yesterday.").pack(fill="x")

		chart = ttk.LabelFrame(tab, text=f"Play reports per day, last {CHART_DAYS} days", padding=10)
		chart.pack(fill="x", pady=8)
		self.stats_hover = ttk.Label(chart, text=HOVER_HINT, style="Hint.TLabel")
		self.stats_hover.pack(anchor="w")
		self.stats_chart = tk.Canvas(chart, height=190, background="white", highlightthickness=0)
		self.stats_chart.pack(fill="x", pady=(4, 0))
		self.stats_chart.bind("<Configure>", lambda e: self.draw_stats_chart())
		self.stats_chart.bind("<Motion>", self.hover_stats_chart)
		self.stats_chart.bind("<Leave>", lambda e: self.hover_stats_chart(None))
		self.stats_days: list[tuple[datetime.date, int]] = []

		consoles = ttk.LabelFrame(tab, text="Consoles", padding=10)
		consoles.pack(fill="both", expand=True)
		columns = (("pid", "Player (PID)", 120), ("days", "Days played", 90), ("streak", "Streak", 70),
			("best", "Best streak", 90), ("reports", "Play reports", 90), ("saves", "Saves uploaded", 100),
			("first", "First played", 130), ("last", "Last played", 130))
		box, self.stats_tree = scrolled(consoles, ttk.Treeview, columns=[c[0] for c in columns], show="headings", height=6)
		for column, title, width in columns:
			# numbers on the right, dates in the middle (so they don't run into the numbers)
			anchor = "w" if column == "pid" else "center" if column in ("first", "last") else "e"
			self.stats_tree.heading(column, text=title, anchor=anchor)
			self.stats_tree.column(column, width=width, anchor=anchor)
		box.pack(fill="both", expand=True)
		ttk.Button(consoles, text="Refresh", command=self.refresh_stats).pack(anchor="w", pady=(8, 0))

	def refresh_stats(self) -> None:
		def work():
			_, storage = self.open_storage()
			try:
				return admin.play_stats(storage, days=CHART_DAYS)
			finally:
				storage.close()

		def done(result):
			players, self.stats_days = result
			when = lambda moment: f"{moment:%Y-%m-%d %H:%M}" if moment else "never"
			self.stats_tree.delete(*self.stats_tree.get_children())
			for p in players:
				self.stats_tree.insert("", "end", values=(p.pid if p.pid is not None else "unknown", p.days, p.streak,
					p.best_streak, p.reports, p.saves, when(p.first), when(p.last)))
			stripe(self.stats_tree)
			self.draw_stats_chart()

		self.background("Reading the play reports...", work, done)

	def stats_layout(self) -> tuple[int, int, int, int, float]:
		"""(left, top, right, baseline, width of each day) of the chart's plot area."""
		width = max(self.stats_chart.winfo_width(), 300)
		left, top, right, baseline = 40, 24, width - 12, int(self.stats_chart.cget("height")) - 30
		return left, top, right, baseline, (right - left) / max(len(self.stats_days), 1)

	def draw_stats_chart(self) -> None:
		canvas = self.stats_chart
		canvas.delete("all")
		if not self.stats_days:
			return
		left, top, right, baseline, slot = self.stats_layout()
		counts = [count for _, count in self.stats_days]
		if not any(counts):
			canvas.create_text((left + right) / 2, (top + baseline) / 2, fill=HINT, font=(FONT, 9),
				text="No play reports in the last two weeks. They come in while Badge Arcade is played with the server running.")
			return

		# Hairline gridlines at clean numbers, labelled on the left
		step = nice_step(max(counts))
		ceiling = step * math.ceil(max(counts) / step)
		y = lambda value: baseline - (baseline - top) * value / ceiling
		for value in range(step, ceiling + 1, step):
			canvas.create_line(left, y(value), right, y(value), fill=GRID)
			canvas.create_text(left - 8, y(value), text=str(value), anchor="e", fill=HINT, font=(FONT, 8))
		canvas.create_line(left, baseline, right, baseline, fill=SIDEBAR_LINE)
		canvas.create_text(left - 8, baseline, text="0", anchor="e", fill=HINT, font=(FONT, 8))

		# A bar per day, rounded at the top and square on the baseline; the busiest day and today get their number
		width = min(BAR_WIDTH, slot * 0.6)
		peak = counts.index(max(counts))
		for index, (day, count) in enumerate(self.stats_days):
			middle = left + slot * (index + 0.5)
			label = "today" if index == len(self.stats_days) - 1 else f"{day:%d}"
			canvas.create_text(middle, baseline + 12, text=label, fill=TEXT if label == "today" else HINT, font=(FONT, 8))
			if not count:
				continue
			x0, x1, bar_top = middle - width / 2, middle + width / 2, y(count)
			radius = min(CORNER, (baseline - bar_top) / 2, width / 2)
			canvas.create_rectangle(x0, bar_top + radius, x1, baseline, fill=ACCENT, width=0, tags="bar")
			canvas.create_rectangle(x0 + radius, bar_top, x1 - radius, bar_top + radius, fill=ACCENT, width=0, tags="bar")
			for corner in (x0, x1 - 2 * radius):
				canvas.create_oval(corner, bar_top, corner + 2 * radius, bar_top + 2 * radius, fill=ACCENT, width=0, tags="bar")
			if index in (peak, len(counts) - 1):
				canvas.create_text(middle, bar_top - 8, text=str(count), fill=TEXT, font=(FONT, 8, "bold"))

	def hover_stats_chart(self, event) -> None:
		"""Shades the day under the pointer and names its count above the chart."""
		canvas = self.stats_chart
		canvas.delete("hover")
		if not any(count for _, count in self.stats_days):
			self.stats_hover.config(text="")  # nothing to point at
			return
		if event is None:
			self.stats_hover.config(text=HOVER_HINT)
			return
		left, top, right, baseline, slot = self.stats_layout()
		index = int((event.x - left) // slot)
		if not left <= event.x < right or not 0 <= index < len(self.stats_days):
			self.stats_hover.config(text=HOVER_HINT)
			return
		day, count = self.stats_days[index]
		canvas.create_rectangle(left + slot * index, top, left + slot * (index + 1), baseline, fill=STRIPE, width=0, tags="hover")
		canvas.tag_lower("hover")
		self.stats_hover.config(text=f"{day:%A %d %B}: {count} play report{'' if count == 1 else 's'}")

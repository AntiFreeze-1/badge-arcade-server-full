"""Weeks: which machines are on the floor, then build and serve."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from bahelper.archive import series, series_name
from bahelper.serving import EXPORT_NS_DATA_ID
from bahelper.week import daily_lineup
from bahelper.workspace import WeekPlan

from .common import TITLE, TREE_STYLE


class WeeksTab(ttk.Frame):
	def __init__(self, parent, app):
		super().__init__(parent, padding=10)
		self.app = app
		self.extras: dict = {}
		ttk.Label(self, text="Pick the machines for the week: yours and any of Nintendo's. Then Build and serve, and "
			"fully close and reopen Badge Arcade on the 3DS. Nintendo's weeks had about 30 machines a day.",
			style="Hint.TLabel", wraplength=1200).pack(anchor="w")

		plans = ttk.Frame(self)
		plans.pack(fill="x", pady=6)
		ttk.Label(plans, text="Week:").pack(side="left")
		self.plan_var = tk.StringVar()
		self.plan_box = ttk.Combobox(plans, textvariable=self.plan_var, width=30)
		self.plan_box.pack(side="left", padx=4)
		self.plan_box.bind("<<ComboboxSelected>>", lambda e: self.load_plan(self.plan_var.get()))
		ttk.Button(plans, text="Save week", command=self.save_plan).pack(side="left")
		ttk.Button(plans, text="Delete week", command=self.delete_plan).pack(side="left", padx=4)

		body = ttk.Frame(self)
		body.pack(fill="both", expand=True)
		left = ttk.Frame(body)
		left.pack(side="left", fill="both", expand=True)
		ttk.Label(left, text="Available machines").pack(anchor="w")
		self.catalog = ttk.Treeview(left, style=TREE_STYLE, show="tree", selectmode="extended")
		scroll = ttk.Scrollbar(left, orient="vertical", command=self.catalog.yview)
		self.catalog.configure(yscrollcommand=scroll.set)
		self.catalog.pack(side="left", fill="both", expand=True)
		scroll.pack(side="left", fill="y")
		self.catalog.bind("<Double-1>", lambda e: self.add())

		middle = ttk.Frame(body, padding=6)
		middle.pack(side="left", fill="y")
		ttk.Button(middle, text="Add  >", command=self.add).pack(pady=(120, 4))
		ttk.Button(middle, text="<  Remove", command=self.remove).pack()
		ttk.Button(middle, text="Clear", command=lambda: self.chosen.delete(*self.chosen.get_children())).pack(pady=4)

		right = ttk.Frame(body)
		right.pack(side="left", fill="both", expand=True)
		ttk.Label(right, text="In the week").pack(anchor="w")
		self.chosen = ttk.Treeview(right, style=TREE_STYLE, show="tree", selectmode="extended")
		self.chosen.pack(fill="both", expand=True)
		self.chosen.bind("<Double-1>", lambda e: self.remove())

		self.report = tk.Text(body, width=46, wrap="word", font=("Segoe UI", 9), state="disabled")
		self.report.pack(side="left", fill="both", padx=(8, 0))

		options = ttk.Frame(self)
		options.pack(fill="x", pady=(8, 0))
		self.every_day = tk.BooleanVar(value=False)
		ttk.Checkbutton(options, text="Every machine, every day", variable=self.every_day,
			command=lambda: self.per_series_box.configure(state="disabled" if self.every_day.get() else "normal")).pack(side="left")
		ttk.Label(options, text="or per series each day:").pack(side="left", padx=(8, 0))
		self.per_series = tk.IntVar(value=3)
		self.per_series_box = ttk.Spinbox(options, from_=1, to=10, textvariable=self.per_series, width=4)
		self.per_series_box.pack(side="left", padx=(4, 12))
		ttk.Button(options, text="Preview the game date", command=self.preview).pack(side="left")
		ttk.Button(options, text="Week extras...", command=self.open_extras).pack(side="left", padx=6)
		self.extras_label = ttk.Label(options, style="Hint.TLabel")
		self.extras_label.pack(side="left")
		actions = ttk.Frame(self)
		actions.pack(fill="x", pady=(8, 0))
		ttk.Button(actions, text="Build and serve", command=self.serve).pack(side="left")
		ttk.Button(actions, text="Save to the server's weeks", command=self.save_to_server).pack(side="left", padx=6)
		ttk.Button(actions, text="Export .boss file...", command=self.export).pack(side="left")
		ttk.Button(actions, text="Put Nintendo's allbadge back", command=self.restore_allbadge).pack(side="right")
		ttk.Button(actions, text="Update allbadge now", command=self.update_allbadge).pack(side="right", padx=6)
		self.live = ttk.Label(self, style="Hint.TLabel", wraplength=1200, justify="left")
		self.live.pack(anchor="w", pady=(6, 0))

		self.show_extras()
		app.when_ready(self.on_archive)

	# ----- lists -----

	def on_archive(self) -> None:
		self.fill_catalog()
		self.refresh_plans()
		self.refresh_live()

	def fill_catalog(self) -> None:
		self.catalog.delete(*self.catalog.get_children())
		mine = self.catalog.insert("", "end", iid="mine", text="Your machines", open=True)
		for cm in self.app.workspace.machines():
			self.catalog.insert(mine, "end", iid=f"custom:{cm.name}", text=f"{cm.title}  ({cm.name})")
		archive = self.app.archive
		by_series = {}
		for name in archive.buildable_machines():
			by_series.setdefault(series(name), []).append(name)
		nintendo = self.catalog.insert("", "end", iid="nintendo", text=f"Nintendo's machines ({sum(map(len, by_series.values()))})")
		for code in sorted(by_series, key=lambda c: series_name(c).lower()):
			node = self.catalog.insert(nintendo, "end", iid=f"series:{code}", text=f"{series_name(code)} ({len(by_series[code])})")
			for name in by_series[code]:
				self.catalog.insert(node, "end", iid=f"nintendo:{name}",
					text=f"{name}  ({len(archive.machines[name].prize_placements)} badges)")

	def add(self) -> None:
		for item in self.catalog.selection():
			if item in ("mine", "nintendo") or item.startswith("series:"):
				children = self.catalog.get_children(item)
				items = [c for child in children for c in (self.catalog.get_children(child) or (child,))]
			else:
				items = [item]
			for entry in items:
				if entry.startswith(("custom:", "nintendo:")) and not self.chosen.exists(entry):
					self.chosen.insert("", "end", iid=entry, text=self.catalog.item(entry, "text"))

	def remove(self) -> None:
		for item in self.chosen.selection():
			self.chosen.delete(item)

	def current_plan(self) -> WeekPlan:
		items = list(self.chosen.get_children())
		nintendo = [i.split(":", 1)[1] for i in items if i.startswith("nintendo:")]
		custom = [i.split(":", 1)[1] for i in items if i.startswith("custom:")]
		order = list(dict.fromkeys(series(s) for s in nintendo))  # keep series together
		nintendo.sort(key=lambda s: order.index(series(s)))
		return WeekPlan(self.plan_var.get().strip() or "My week", nintendo, custom,
			0 if self.every_day.get() else max(1, int(self.per_series.get())), dict(self.extras))

	# ----- saved weeks -----

	def refresh_plans(self) -> None:
		names = [w.name for w in self.app.workspace.weeks()]
		self.plan_box.configure(values=names)
		if names and not self.plan_var.get():
			self.load_plan(names[0])

	def load_plan(self, name: str) -> None:
		plan = next((w for w in self.app.workspace.weeks() if w.name == name), None)
		if not plan:
			return
		self.plan_var.set(plan.name)
		self.chosen.delete(*self.chosen.get_children())
		for machine in plan.custom:
			cm = self.app.workspace.get_machine(machine)
			if cm:
				self.chosen.insert("", "end", iid=f"custom:{machine}", text=f"{cm.title}  ({machine})")
		for machine in plan.nintendo:
			if machine in self.app.archive.machines:
				self.chosen.insert("", "end", iid=f"nintendo:{machine}", text=machine)
		self.every_day.set(plan.per_series == 0)
		self.per_series.set(plan.per_series or 3)
		self.extras = dict(plan.extras or {})
		self.show_extras()
		self.per_series_box.configure(state="disabled" if plan.per_series == 0 else "normal")

	def save_plan(self) -> None:
		plan = self.current_plan()
		self.plan_var.set(plan.name)
		self.app.workspace.save_week(plan)
		self.refresh_plans()
		self.app.status.set(f"Saved the week {plan.name}.")

	def delete_plan(self) -> None:
		name = self.plan_var.get().strip()
		if name and messagebox.askyesno(TITLE, f"Delete the week {name}?", parent=self):
			self.app.workspace.delete_week(name)
			self.plan_var.set("")
			self.refresh_plans()

	# ----- building -----

	def write_report(self, text: str) -> None:
		self.report.configure(state="normal")
		self.report.delete("1.0", "end")
		self.report.insert("1.0", text)
		self.report.configure(state="disabled")

	def preview(self) -> None:
		if not self.app.require_archive():
			return
		plan = self.current_plan()
		if not plan.nintendo and not plan.custom:
			messagebox.showinfo(TITLE, "Add some machines first.", parent=self)
			return
		builder = self.app.week_builder()
		lineup = builder.lineup(plan)
		shown = lineup[1]  # served weeks start the day before the game date
		titles = {cm.name: cm.title for cm in self.app.workspace.machines()}
		wanted = len(daily_lineup(plan.nintendo + plan.custom, 1, plan.per_series)[0])
		capped = "" if wanted <= builder.max_per_day else (f"\n\nThe game handles at most {builder.max_per_day} machines "
			"a day (the most Nintendo put on the floor), so each day shows a different part of the week.")
		self.write_report(f"On {self.app.server.game_date()} the floor has {len(shown)} machines "
			f"(bonus machine: {titles.get(shown[0], shown[0])}):{capped}\n\n"
			+ "\n".join(f"{titles[s]} (yours)" if s in titles else f"{series_name(series(s))}: {s}" for s in shown))

	def build(self, then) -> None:
		"""Builds the week off the UI thread, then then(plan, ns_id, content, progress) there too.
		The week keeps the base week's dates: serve.py moves it to the game date."""
		if not self.app.require_archive():
			return
		plan = self.current_plan()
		if not plan.nintendo and not plan.custom:
			messagebox.showinfo(TITLE, "Add some machines first.", parent=self)
			return
		self.app.workspace.save_week(plan)
		self.refresh_plans()
		builder = self.app.week_builder()

		def work(progress):
			builder.progress = progress
			content = builder.build(plan, EXPORT_NS_DATA_ID)
			return then(plan, EXPORT_NS_DATA_ID, content, progress)

		def done(message):
			self.refresh_live()
			if message:
				self.write_report(message)

		self.app.background(f"Building {plan.name}...", work, done)

	def serve(self) -> None:
		server = self.app.server
		if server is None:
			return
		builder = self.app.week_builder()
		machines = lambda plan: plan.nintendo + plan.custom

		def with_server(plan, _ns_id, content, progress):
			progress("Saving it as one of the server's custom weeks...")
			slug = server.export_week(plan.name, machines(plan), plan.per_series, content)
			allbadge_note = server.update_allbadge(builder, progress=progress)
			progress("Serving it with the server's serve.py...")
			output = server.serve_week(slug)
			server.record_served(f"the week \"{plan.name}\"", server.live_id())
			return (f"{output}\n\n{allbadge_note}\n\nSaved as spotpass-letter/out/weeks/{slug}.boss, so the Serve a "
				"week tab lists it too. The server sends the new files by itself; no restart needed. Fully close and "
				"reopen Badge Arcade on the 3DS.")

		self.build(with_server)

	def save_to_server(self) -> None:
		server = self.app.server
		if server is None:
			return

		def export(plan, _ns_id, content, progress):
			slug = server.export_week(plan.name, plan.nintendo + plan.custom, plan.per_series, content)
			return (f"Saved as the server's custom week spotpass-letter/out/weeks/{slug}.boss. Serve it from the Serve a "
				"week tab, or with Build and serve here.")

		self.build(export)

	def export(self) -> None:
		path = filedialog.asksaveasfilename(parent=self, defaultextension=".boss",
			initialfile=f"{self.plan_var.get().strip() or 'My week'}.boss",
			filetypes=[("SpotPass file", "*.boss"), ("All files", "*.*")])
		if not path:
			return

		def save(plan, ns_id, content, progress):
			Path(path).write_bytes(content)
			return (f"Saved {path}. It has the base week's dates (Dec 29, 2022); the server's serve.py moves it to the game "
				"date when it serves it.")

		self.build(save)

	def update_allbadge(self, force: bool = True) -> None:
		if not self.app.require_archive():
			return
		server, builder = self.app.server, self.app.week_builder()
		self.app.background("Updating allbadge...", lambda progress: server.update_allbadge(builder, force, progress),
			lambda message: (self.write_report(message), self.refresh_live()))

	def restore_allbadge(self) -> None:
		if not self.app.server or not messagebox.askyesno(TITLE, "Put Nintendo's own allbadge back live (under a new "
				"SpotPass ID, so the 3DS downloads it again)? Your badges stay in your workspace.", parent=self):
			return
		self.app.background("Putting Nintendo's allbadge back...", lambda progress: self.app.server.restore_allbadge(),
			lambda message: (self.write_report(message), self.refresh_live()))

	# ----- extras -----

	def open_extras(self) -> None:
		if not self.app.require_archive():
			return
		from .extras_window import ExtrasWindow
		ExtrasWindow(self, self.app, self.plan_var.get().strip() or "My week", self.extras, self.extras_changed)

	def extras_changed(self, extras: dict) -> None:
		self.extras = extras
		self.show_extras()
		self.save_plan()

	def show_extras(self) -> None:
		e = self.extras
		parts = [f"{len(e.get(k) or {})} {label}" for k, label in (("talkpics", "hall pictures"), ("texts", "lines"),
			("posts", "gallery posts")) if e.get(k)]
		self.extras_label.configure(text=("Changed: " + ", ".join(parts)) if parts else "(Nintendo's hall pictures, lines and gallery)")

	def refresh_live(self) -> None:
		if self.app.server:
			self.live.configure(text=self.app.server.status())

	def on_show(self) -> None:
		if self.app.archive:
			self.fill_catalog()
			self.refresh_live()

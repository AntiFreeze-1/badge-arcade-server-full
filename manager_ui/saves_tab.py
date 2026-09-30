"""Saves tab: the saves stored on the server, backing them up, and resetting a player."""

import datetime
from tkinter import messagebox, ttk

import serve
from badge_arcade import admin
from badge_arcade.config import load_config
from badge_arcade.storage import Storage

from . import SERVER_DIR, TITLE
from .widgets import hint, scrolled, set_text, stripe, text_box


class SavesTab:
	def build_saves_tab(self, tab: ttk.Frame) -> None:
		hint(tab, "Saves stored on this server. Backups are zip files in server/backups; to restore one, "
			"stop the server and unzip it over server/data.").pack(fill="x")
		saves = ttk.LabelFrame(tab, text="Saves", padding=10)
		saves.pack(fill="x", pady=8)
		box, self.save_tree = scrolled(saves, ttk.Treeview, columns=("pid", "what", "version", "size", "updated"),
			show="headings", height=8)
		for column, title, width in (("pid", "Player (PID)", 120), ("what", "Record", 260), ("version", "Version", 70),
				("size", "Size", 90), ("updated", "Updated", 150)):
			self.save_tree.heading(column, text=title, anchor="w")
			self.save_tree.column(column, width=width)
		box.pack(fill="x")
		buttons = ttk.Frame(saves)
		buttons.pack(fill="x", pady=(8, 0))
		ttk.Button(buttons, text="Back up now", style="Accent.TButton", command=self.backup_saves).pack(side="left")
		ttk.Button(buttons, text="Reset selected player...", command=self.reset_player).pack(side="left", padx=6)
		ttk.Button(buttons, text="Open backups folder", command=lambda: self.open_folder(SERVER_DIR / "backups")).pack(side="left")
		ttk.Button(buttons, text="Refresh", command=self.refresh_saves).pack(side="left", padx=6)

		record = ttk.LabelFrame(tab, text="Free-play record", padding=10)
		record.pack(fill="both", expand=True)
		box, self.freeplay_view = text_box(record, height=12, font=("Consolas", 9))
		box.pack(fill="both", expand=True)

	def open_storage(self):
		config = load_config(serve.SERVER_CONFIG)
		return config, Storage(config.data_path)

	def freeplay_records(self) -> list[dict]:
		"""Each player's FreePlayData: the practice-catcher time and the free-play campaigns seen."""
		_, storage = self.open_storage()
		try:
			records = []
			for obj in storage.list_objects():
				if obj["data_type"] != 100:
					continue
				fields = dict(admin.meta_fields(obj["meta_binary"]))
				count = fields.get(0x20, 0)
				campaigns = {fields[0x24 + 8 * i]: fields[0x28 + 8 * i] for i in range(count) if 0x28 + 8 * i in fields}
				records.append({"pid": obj["owner_id"], "fields": fields, "campaigns": campaigns})
			return records
		finally:
			storage.close()

	def refresh_saves(self) -> None:
		def work():
			_, storage = self.open_storage()
			try:
				return storage.list_objects(), self.freeplay_records()
			finally:
				storage.close()

		def done(result):
			objects, records = result
			self.save_tree.delete(*self.save_tree.get_children())
			for obj in objects:
				what = {100: "Free plays + badges collected"}.get(obj["data_type"], f"Type {obj['data_type']}")
				updated = datetime.datetime.fromtimestamp(obj["updated"]).strftime("%Y-%m-%d %H:%M")
				size = f"{obj['size']} bytes" if obj["version"] else "empty"
				self.save_tree.insert("", "end", iid=str(obj["data_id"]),
					values=(obj["owner_id"], what, obj["version"], size, updated))
			stripe(self.save_tree)
			text = []
			for record in records:
				practice = record["fields"].get(0x18)
				when = datetime.datetime.fromtimestamp(practice).strftime("%Y-%m-%d %H:%M") if practice else "never"
				text.append(f"PID {record['pid']}: practice catcher last played {when}")
				for cid, flag in record["campaigns"].items():
					text.append(f"  campaign {cid}: {'not collected yet' if flag else 'collected'}")
			set_text(self.freeplay_view, "\n".join(text) or "No saves yet.")

		self.background("Reading saves...", work, done)

	def backup_saves(self) -> None:
		def work():
			config, storage = self.open_storage()
			try:
				return admin.backup(config, storage)
			finally:
				storage.close()

		self.background("Backing up...", work, lambda path: messagebox.showinfo(TITLE, f"Saved {path}"))

	def reset_player(self) -> None:
		selection = self.save_tree.selection()
		if not selection:
			messagebox.showinfo(TITLE, "Pick a save first.")
			return
		pid = int(self.save_tree.item(selection[0], "values")[0])
		if not messagebox.askyesno(TITLE, f"Forget every save of player {pid}? A backup is made "
				"first. Do this while Badge Arcade is closed; it starts fresh next time.", icon="warning"):
			return

		def work():
			config, storage = self.open_storage()
			try:
				return admin.reset(config, storage, pid)
			finally:
				storage.close()

		def done(result):
			removed, path = result
			self.refresh_saves()
			messagebox.showinfo(TITLE, f"Removed {removed} record(s). Backup: {path}")

		self.background("Resetting...", work, done)

"""Letters tab: write letters for the 3DS's Notifications applet and send them through SpotPass."""

from pathlib import Path
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import letters
import make_letter
import serve

from . import TITLE, save_settings
from .widgets import BOXED, FONT, GOOD, auto_wrap, hint, scrolled, stripe


class LettersTab:
	def build_letters_tab(self, tab: ttk.Frame) -> None:
		hint(tab, "Send a letter to the 3DS's Notifications applet, like the ones Nintendo sent from Badge "
			"Arcade. It comes with the data Badge Arcade downloads when it opens: send it, then open Badge Arcade on the "
			"3DS (with the server and proxy running) and check Notifications afterwards.").pack(fill="x")
		hint(tab, "Experimental. If Badge Arcade can't download its data after you send a letter, press "
			"\"Take letter down\". Back up the 3DS's news save first (spotpass-letter/README.md, \"Delivering it\").",
			style="Warn.TLabel").pack(fill="x", pady=(2, 0))

		body = ttk.Frame(tab)
		body.pack(fill="both", expand=True, pady=8)

		history = ttk.LabelFrame(body, text="Your letters", padding=8)
		history.pack(side="left", fill="y")
		box, self.letter_tree = scrolled(history, ttk.Treeview, columns=("state",), show="tree headings",
			selectmode="browse", height=14)
		self.letter_tree.heading("#0", text="Title", anchor="w")
		self.letter_tree.heading("state", text="Sent", anchor="w")
		self.letter_tree.column("#0", width=210)
		self.letter_tree.column("state", width=150)
		self.letter_tree.tag_configure("live", foreground=GOOD, font=(FONT, 9, "bold"))
		box.pack(fill="y", expand=True)
		self.letter_tree.bind("<<TreeviewSelect>>", lambda e: self.open_selected_letter())
		buttons = ttk.Frame(history)
		buttons.pack(fill="x", pady=(8, 0))
		ttk.Button(buttons, text="New letter", command=self.new_letter).pack(side="left")
		ttk.Button(buttons, text="Delete", command=self.delete_selected_letter).pack(side="left", padx=6)

		editor = ttk.LabelFrame(body, text="Letter", padding=10)
		editor.pack(side="left", fill="both", expand=True, padx=(8, 0))
		editor.columnconfigure(1, weight=1)
		self.letter_title = tk.StringVar()
		self.letter_url = tk.StringVar()
		self.letter_region = tk.StringVar(value="USA")
		ttk.Label(editor, text="Title:").grid(row=0, column=0, sticky="w")
		ttk.Entry(editor, textvariable=self.letter_title).grid(row=0, column=1, sticky="ew", padx=6)
		self.title_count = ttk.Label(editor, style="Hint.TLabel", width=8)
		self.title_count.grid(row=0, column=2, sticky="w")
		ttk.Label(editor, text="Message:").grid(row=1, column=0, sticky="nw", pady=(8, 0))
		self.letter_message = tk.Text(editor, height=9, width=50, wrap="word", font=(FONT, 10), undo=True, **BOXED)
		self.letter_message.grid(row=1, column=1, sticky="nsew", padx=6, pady=(8, 0))
		editor.rowconfigure(1, weight=1)
		self.message_count = ttk.Label(editor, style="Hint.TLabel", width=8)
		self.message_count.grid(row=1, column=2, sticky="nw", pady=(8, 0))
		ttk.Label(editor, text="Link:").grid(row=2, column=0, sticky="w", pady=(8, 0))
		ttk.Entry(editor, textvariable=self.letter_url).grid(row=2, column=1, sticky="ew", padx=6, pady=(8, 0))
		ttk.Label(editor, text="optional", style="Hint.TLabel").grid(row=2, column=2, sticky="w", pady=(8, 0))
		ttk.Label(editor, text="Region:").grid(row=3, column=0, sticky="w", pady=(8, 0))
		regions = ttk.Frame(editor)
		regions.grid(row=3, column=1, sticky="w", padx=6, pady=(8, 0))
		for region, label in (("USA", "Americas (USA)"), ("EUR", "Europe (EUR)")):
			ttk.Radiobutton(regions, text=label, variable=self.letter_region, value=region).pack(side="left", padx=(0, 12))

		ttk.Label(editor, text="Picture:").grid(row=4, column=0, sticky="nw", pady=(8, 0))
		pictures = ttk.Frame(editor)
		pictures.grid(row=4, column=1, columnspan=2, sticky="w", padx=6, pady=(8, 0))
		self.picture_preview = ttk.Label(pictures, relief="solid", width=26, anchor="center", text="No picture")
		self.picture_preview.pack(side="left")
		picture_buttons = ttk.Frame(pictures)
		picture_buttons.pack(side="left", padx=8, anchor="n")
		self.choose_picture_button = ttk.Button(picture_buttons, text="Choose picture...", command=self.choose_letter_picture)
		self.choose_picture_button.pack(anchor="w")
		ttk.Button(picture_buttons, text="No picture", command=lambda: self.set_letter_picture(None)).pack(anchor="w", pady=4)
		self.picture_info = ttk.Label(picture_buttons, style="Hint.TLabel", wraplength=280, justify="left")
		self.picture_info.pack(anchor="w")
		if not letters.pillow_available():
			self.choose_picture_button.state(["disabled"])
			self.picture_info.config(text="Pictures need the Pillow package: run Setup.bat again.")

		self.letter_warning = auto_wrap(ttk.Label(editor, style="Warn.TLabel", justify="left"))
		self.letter_warning.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 0))
		actions = ttk.Frame(editor)
		actions.grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
		ttk.Button(actions, text="Send to 3DS", style="Accent.TButton", command=self.send_letter).pack(side="left")
		ttk.Button(actions, text="Save draft", command=self.save_letter_draft).pack(side="left", padx=6)
		ttk.Button(actions, text="Take letter down", command=self.take_letter_down).pack(side="left")

		self.letter_live = tk.StringVar()
		ttk.Label(tab, textvariable=self.letter_live, style="Big.TLabel").pack(anchor="w")

		self.editing_letter: letters.SavedLetter | None = None
		self.letter_picture: bytes | None = None
		self.letter_list: list[letters.SavedLetter] = []
		self.letter_title.trace_add("write", lambda *a: self.update_letter_hints())
		self.letter_url.trace_add("write", lambda *a: self.update_letter_hints())
		self.letter_message.bind("<<Modified>>", self.on_message_modified)
		self.set_letter_picture(None)

	def on_message_modified(self, event=None) -> None:
		if self.letter_message.edit_modified():
			self.letter_message.edit_modified(False)
			self.update_letter_hints()

	def letter_from_editor(self) -> letters.SavedLetter:
		"""The letter in the editor. A letter that was already sent becomes a new letter
		when it's changed, so the history keeps what was sent."""
		current = letters.SavedLetter("", self.letter_title.get().strip(), self.letter_message.get("1.0", "end-1c").strip(),
			self.letter_url.get().strip(), self.letter_region.get(), image=self.letter_picture)
		old = self.editing_letter
		if old is None:
			return current
		same = (old.title, old.message, old.url, old.region, old.image) == \
			(current.title, current.message, current.url, current.region, current.image)
		if old.sent and not same:
			return current
		current.id, current.created, current.sent, current.ns_data_id, current.downloaded = \
			old.id, old.created, old.sent, old.ns_data_id, old.downloaded
		return current

	def update_letter_hints(self) -> None:
		title = self.letter_title.get()
		message = self.letter_message.get("1.0", "end-1c")
		self.title_count.config(text=f"{len(title)}/{make_letter.TITLE_MAX}",
			style="Bad.TLabel" if len(title) > make_letter.TITLE_MAX else "Hint.TLabel")
		self.message_count.config(text=f"{len(message)}/{make_letter.MESSAGE_MAX}",
			style="Bad.TLabel" if len(message) > make_letter.MESSAGE_MAX else "Hint.TLabel")
		if not title.strip() and not message.strip():
			self.letter_warning.config(text="")
			return
		errors, warnings = make_letter.validate_letter(self.letter_from_editor().to_letter())
		# An empty title or message is only worth mentioning once the other one is filled in
		self.letter_warning.config(text="  ".join(errors + warnings))

	def show_letter(self, letter: letters.SavedLetter | None) -> None:
		self.editing_letter = letter
		self.letter_title.set(letter.title if letter else "")
		self.letter_url.set(letter.url if letter else "")
		self.letter_region.set(letter.region if letter else "USA")
		self.letter_message.delete("1.0", "end")
		if letter:
			self.letter_message.insert("1.0", letter.message)
		self.set_letter_picture(letter.image if letter else None)
		self.update_letter_hints()

	def set_letter_picture(self, jpeg: bytes | None) -> None:
		self.letter_picture = jpeg
		if not jpeg:
			self.letter_preview_image = None
			self.picture_preview.config(image="", text="No picture")
			if letters.pillow_available():
				self.picture_info.config(text="Any picture works: it's cropped to 400x240 and made into a JPEG under 50 KB.")
		else:
			try:
				self.letter_preview_image = tk.PhotoImage(data=letters.preview_png(jpeg))
				self.picture_preview.config(image=self.letter_preview_image, text="")
			except (ValueError, tk.TclError):
				self.picture_preview.config(image="", text="(no preview)")
			self.picture_info.config(text=f"400x240 JPEG, {len(jpeg) / 1024:.0f} KB")
		self.update_letter_hints()

	def choose_letter_picture(self) -> None:
		path = filedialog.askopenfilename(parent=self, title="Choose a picture", filetypes=letters.IMAGE_TYPES)
		if path:
			self.background("Preparing the picture...", lambda: letters.prepare_image(Path(path)), self.set_letter_picture)

	def refresh_letters(self, select: str | None = None) -> None:
		def work():
			return letters.list_letters(), serve.live_letter()

		def done(result):
			self.letter_list, live = result
			self.letter_tree.delete(*self.letter_tree.get_children())
			for letter in self.letter_list:
				is_live = live and live.id == letter.id
				if is_live:
					state = "● live, downloaded" if live.downloaded else "● live, waiting for the 3DS"
				else:
					state = letter.sent.replace("T", " ")[:16] if letter.sent else "draft"
				self.letter_tree.insert("", "end", iid=letter.id, text=letter.title, values=(state,),
					tags=["live"] if is_live else [])
			stripe(self.letter_tree)
			if live:
				fetched = f"downloaded by the 3DS {live.downloaded.replace('T', ' ')[:16]}" if live.downloaded else \
					"not downloaded by the 3DS yet"
				self.letter_live.set(f"Live now: \"{live.title}\", sent {live.sent.replace('T', ' ')[:16]}, {fetched}.")
			else:
				self.letter_live.set("No letter is live.")
			if select and self.letter_tree.exists(select):
				self.letter_tree.selection_set(select)
				self.letter_tree.see(select)

		self.background("Reading letters...", work, done)

	def open_selected_letter(self) -> None:
		selection = self.letter_tree.selection()
		letter = next((item for item in self.letter_list if selection and item.id == selection[0]), None)
		if letter and (self.editing_letter is None or letter.id != self.editing_letter.id):
			self.show_letter(letter)

	def new_letter(self) -> None:
		self.letter_tree.selection_set(())
		self.show_letter(None)

	def delete_selected_letter(self) -> None:
		selection = self.letter_tree.selection()
		if not selection:
			self.status.set("Pick a letter to delete first.")
			return
		letter = next(item for item in self.letter_list if item.id == selection[0])
		live = serve.live_letter()
		is_live = live is not None and live.id == letter.id
		if not messagebox.askyesno(TITLE, f"Delete \"{letter.title}\"?"
				+ ("\n\nIt's live now, so it will be taken down too." if is_live else "")):
			return

		def work():
			if is_live:
				serve.remove_letter(self.key)
			letters.delete_letter(letter.id)

		def done(_):
			self.show_letter(None)
			self.refresh_letters()

		self.background("Deleting the letter...", work, done)

	def save_letter_draft(self) -> None:
		letter = self.letter_from_editor()
		if not letter.title and not letter.message:
			self.status.set("There's nothing to save yet.")
			return

		def done(saved):
			self.editing_letter = saved
			self.status.set(f"Saved \"{saved.title or 'untitled'}\".")
			self.refresh_letters(select=saved.id)

		self.background("Saving the letter...", lambda: letters.save_letter(letter), done)

	def send_letter(self) -> None:
		if self.key is None:
			messagebox.showerror(TITLE, self.missing_files())
			return
		letter = self.letter_from_editor()
		errors, _ = make_letter.validate_letter(letter.to_letter())
		if errors:
			messagebox.showerror(TITLE, "\n".join(errors))
			return
		live = serve.live_letter()
		question = f"Send \"{letter.title}\" to the 3DS?"
		if live and live.id != letter.id:
			question += f"\n\nIt replaces \"{live.title}\", which is live now."
		if not self.settings.get("letters_warned"):
			question += ("\n\nLetters are experimental. Back up the 3DS's news save with GodMode9 first (see "
				"spotpass-letter/README.md, \"Delivering it\").")
		if not messagebox.askyesno(TITLE, question):
			return
		self.settings["letters_warned"] = True
		save_settings(self.settings)

		def done(message):
			self.editing_letter = letter
			self.refresh_letters(select=letter.id)
			self.refresh_status()
			ready = self.server.listening() and self.proxy.listening()
			messagebox.showinfo(TITLE, message + ("" if ready else
				"\n\nStart the server and proxy on the Server tab so the 3DS can fetch it."))

		self.background("Sending the letter...", lambda: serve.serve_letter(letter, self.key), done)

	def take_letter_down(self) -> None:
		def done(message):
			self.status.set(message)
			self.refresh_letters()
			self.refresh_status()

		self.background("Taking the letter down...", lambda: serve.remove_letter(self.key), done)

	def check_letter_downloaded(self, line: str) -> None:
		"""Called with each new server log line: marks the live letter as downloaded."""
		sent = re.search(r"Sent SpotPass file (news(_v131)?|playinfo_v131)\.dat", line)
		if sent and (sent.group(1) != "playinfo_v131" or serve.letter_in_playinfo()) and serve.mark_letter_downloaded():
			self.status.set("The 3DS downloaded the letter. It should now be in the Notifications applet.")
			self.refresh_letters()

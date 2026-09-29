"""Letters for the manager's Letters tab: picture conversion and the history of
letters you've written.

Each letter is kept in out/letters/<id>/ as letter.json (plus image.jpg if it
has a picture). serve.py puts one live; make_letter.py builds the container.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
import datetime
import io
import json
import shutil

from make_letter import IMAGE_RECOMMENDED_MAX, IMAGE_SIZE, TITLE_IDS, Letter

HERE = Path(__file__).resolve().parent
LETTERS_DIR = HERE / "out" / "letters"

IMAGE_TYPES = [("Pictures", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")]


# ----- pictures -----

def pillow_available() -> bool:
	try:
		import PIL  # noqa: F401
	except ImportError:
		return False
	return True


def _pillow():
	try:
		from PIL import Image, ImageOps
	except ImportError as e:
		raise ValueError("Converting pictures needs the Pillow package: run Setup.bat (or install.py --packages) again.") from e
	return Image, ImageOps


def prepare_image(source: Path | bytes) -> bytes:
	"""Any picture Pillow can read -> a 400x240 baseline JPEG of at most 50 KB,
	scaled to fill the frame and cropped around the centre."""
	Image, ImageOps = _pillow()
	try:
		image = Image.open(io.BytesIO(source) if isinstance(source, bytes) else source)
		image.load()
	except (OSError, SyntaxError) as e:
		raise ValueError(f"That file isn't a picture that can be read ({e}).") from e
	image = ImageOps.exif_transpose(image)
	if image.mode in ("RGBA", "LA", "P"):
		# Transparent parts become white rather than black
		image = image.convert("RGBA")
		background = Image.new("RGB", image.size, "white")
		background.paste(image, mask=image.getchannel("A"))
		image = background
	image = ImageOps.fit(image.convert("RGB"), IMAGE_SIZE, Image.Resampling.LANCZOS)

	for quality in range(90, 25, -5):
		out = io.BytesIO()
		image.save(out, "JPEG", quality=quality, optimize=True, progressive=False, subsampling="4:2:0")
		if out.tell() <= IMAGE_RECOMMENDED_MAX:
			return out.getvalue()
	raise ValueError("The picture couldn't be made small enough (50 KB); try a simpler picture.")


def preview_png(jpeg: bytes, width: int = 200) -> bytes:
	"""A small PNG of a picture, for Tk's PhotoImage (which can't show JPEG)."""
	Image, _ = _pillow()
	image = Image.open(io.BytesIO(jpeg))
	image.thumbnail((width, width * image.height // max(image.width, 1)))
	out = io.BytesIO()
	image.save(out, "PNG")
	return out.getvalue()


# ----- history -----

@dataclass
class SavedLetter:
	id: str
	title: str
	message: str
	url: str = ""
	region: str = "USA"
	created: str = ""                 # ISO date-times (local time)
	sent: str | None = None           # when it was last put live
	ns_data_id: int | None = None     # its SpotPass ID when it was last put live
	downloaded: str | None = None     # when the server last sent it to a console
	image: bytes | None = field(default=None, repr=False)

	@property
	def folder(self) -> Path:
		return LETTERS_DIR / self.id

	def to_letter(self, ns_data_id: int = 0) -> Letter:
		return Letter(self.title, self.message, self.url or None, self.image,
			source_program_id=TITLE_IDS[self.region], ns_data_id=ns_data_id)


def _now() -> str:
	return datetime.datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
	base = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
	letter_id, n = base, 1
	while (LETTERS_DIR / letter_id).exists():
		n += 1
		letter_id = f"{base}-{n}"
	return letter_id


def save_letter(letter: SavedLetter) -> SavedLetter:
	"""Writes a letter (a new one gets an ID). Returns it."""
	if letter.region not in TITLE_IDS:
		raise ValueError(f"Unknown region {letter.region!r}.")
	if not letter.id:
		letter.id = new_id()
	if not letter.created:
		letter.created = _now()
	folder = letter.folder
	folder.mkdir(parents=True, exist_ok=True)
	data = asdict(letter)
	del data["image"]
	temp = folder / "letter.json.tmp"
	temp.write_text(json.dumps(data, indent="\t", ensure_ascii=False) + "\n", encoding="utf-8")
	temp.replace(folder / "letter.json")
	image_path = folder / "image.jpg"
	if letter.image:
		image_path.write_bytes(letter.image)
	else:
		image_path.unlink(missing_ok=True)
	return letter


def load_letter(letter_id: str) -> SavedLetter:
	folder = LETTERS_DIR / letter_id
	try:
		data = json.loads((folder / "letter.json").read_text(encoding="utf-8"))
	except FileNotFoundError as e:
		raise ValueError(f"There's no letter {letter_id}.") from e
	known = SavedLetter.__dataclass_fields__
	letter = SavedLetter(**{k: v for k, v in data.items() if k in known and k != "image"})
	image = folder / "image.jpg"
	letter.image = image.read_bytes() if image.exists() else None
	return letter


def list_letters() -> list[SavedLetter]:
	"""Every saved letter, newest first. Unreadable folders are skipped."""
	letters = []
	if LETTERS_DIR.is_dir():
		for folder in LETTERS_DIR.iterdir():
			try:
				letters.append(load_letter(folder.name))
			except (ValueError, TypeError, OSError):
				continue
	return sorted(letters, key=lambda letter: letter.created, reverse=True)


def delete_letter(letter_id: str) -> None:
	folder = LETTERS_DIR / letter_id
	if folder.parent != LETTERS_DIR or not (folder / "letter.json").exists():
		raise ValueError(f"There's no letter {letter_id}.")
	shutil.rmtree(folder)


def copy_of(letter: SavedLetter) -> SavedLetter:
	"""An unsaved copy to edit (a letter that was sent keeps what was sent)."""
	return SavedLetter("", letter.title, letter.message, letter.url, letter.region, image=letter.image)

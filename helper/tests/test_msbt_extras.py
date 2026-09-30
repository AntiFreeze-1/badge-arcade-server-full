"""Bunny's lines, hall pictures and gallery posts (need Nintendo's archived week)."""

import io

import numpy as np
from PIL import Image

from bahelper import extras, msbt, sarc
from bahelper.week import WeekBuilder
from bahelper.workspace import WeekPlan, Workspace

from .test_shapes_and_makers import star_picture


def picture(size=(400, 300), colour=(200, 40, 90)) -> Image.Image:
	im = Image.new("RGBA", size, colour + (255,))
	im.alpha_composite(star_picture((min(size), min(size))), ((size[0] - min(size)) // 2, 0))
	return im


def test_msbt_round_trip_and_edit(base_top):
	raw = base_top[extras.message_path(base_top)]
	messages = msbt.Messages(raw)
	assert len(messages.labels) == len(messages.strings) == 331
	assert messages.build() == raw  # unchanged: byte for byte
	index = messages.index_of("BossText_274")
	text, tags = messages.editable(index)
	assert text.count("⟨") == len(tags) > 0
	messages.set_editable(index, text.replace("two free", "THREE free"))
	again = msbt.Messages(messages.build())
	new_text, new_tags = again.editable(again.index_of("BossText_274"))
	assert "THREE free" in new_text and new_tags == tags
	assert again.labels == messages.labels
	# a removed marker drops its tag; the rest stay in order
	again.set_editable(index, new_text.replace("⟨1⟩", ""))
	assert len(again.editable(index)[1]) == len(tags) - 1


def test_talkpics(base_top):
	pics = extras.list_talkpics(base_top)
	assert len(pics) >= 10 and {p.format for p in pics} >= {"Etc1", "Etc1_a4", "Rgba4"}
	for fmt in ("Etc1", "Etc1_a4", "Rgba4"):
		pic = next(p for p in pics if p.format == fmt)
		original = extras.talkpic_data(base_top, pic)
		data = extras.encode_talkpic(pic, picture())
		assert len(data) == len(original)
		shown = extras.decode_talkpic(pic, data)
		assert shown.size == pic.area
		assert np.array(shown)[..., 3].min() == 255  # the picture fills the area
		if fmt != "Etc1":
			alpha = extras.decode_texture(pic, data)[..., 3].copy()
			ox, oy = pic.origin
			alpha[oy:oy + pic.area[1], ox:ox + pic.area[0]] = 0
			assert alpha.max() == 0  # nothing outside the visible area (it's centred in the texture)
	# Nintendo's own pictures sit inside the centred area
	for pic in pics:
		rgba = extras.decode_texture(pic, extras.talkpic_data(base_top, pic))
		mask = rgba[..., 3] > 8 if pic.format != "Etc1" else (rgba[..., :3] < 240).any(-1)
		ox, oy = pic.origin
		outside = mask.copy()
		outside[max(0, oy - 4):oy + pic.area[1] + 4, max(0, ox - 4):ox + pic.area[0] + 4] = False
		ys, xs = np.nonzero(mask)
		if (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1) == (0, 0, pic.width, pic.height):
			continue  # two start-up pictures paint their whole texture
		assert outside.sum() < 0.01 * mask.sum(), pic.key


def test_posts(base_top):
	posts = extras.list_posts(base_top)
	assert len(posts) == 20 and all(p.slot.startswith("Post") for p in posts)
	files = extras.post_files(base_top, posts[0])
	changed = extras.replace_post(files, name="Arcade <3 Fan", image=picture(), mii=star_picture((128, 128)))
	assert b"<Name>Arcade &lt;3</Name>" in changed["post.xml"]  # 10 characters, escaped
	image = Image.open(io.BytesIO(changed["Image.jpg"]))
	assert image.size == (320, 240) and len(changed["Image.jpg"]) <= extras.POST_JPEG_MAX
	assert len(changed["Mii.Etc1_a4"]) == len(files["Mii.Etc1_a4"])


def test_week_with_extras(archive, key, tmp_path, base_top):
	ws = Workspace(tmp_path / "workspace")
	image = ws.add_image(_png(picture()), "test")
	pic = extras.list_talkpics(base_top)[0]
	post = extras.list_posts(base_top)[0]
	plan = WeekPlan("Extras", nintendo=["Animal_055"], extras={
		"talkpics": {pic.key: image},
		"texts": {"BossText_274": "Hello from the helper!"},
		"posts": {post.path: {"name": "Helper", "image": image}},
	})
	from bahelper import boss
	out = WeekBuilder(archive, ws).build(plan, 0x5C0)
	top = sarc.sarc_read(boss.open_container(out, key)[1])
	messages = msbt.Messages(top[extras.message_path(top)])
	assert messages.editable(messages.index_of("BossText_274"))[0] == "Hello from the helper!"
	assert extras.talkpic_data(top, pic) != extras.talkpic_data(base_top, pic)
	assert b"<Name>Helper</Name>" in sarc.sarc_read(top[post.path])["post.xml"]


def _png(im: Image.Image) -> bytes:
	out = io.BytesIO()
	im.save(out, "PNG")
	return out.getvalue()

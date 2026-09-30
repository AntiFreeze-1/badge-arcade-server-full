"""Collision shapes and turning pictures into badges."""

import numpy as np
from PIL import Image, ImageDraw

from bahelper import formats as f, makers, shapes


def star_picture(size=(64, 64)) -> Image.Image:
	im = Image.new("RGBA", size, (0, 0, 0, 0))
	draw = ImageDraw.Draw(im)
	w, h = size
	points = []
	for i in range(10):
		r = (min(w, h) / 2 - 2) * (1 if i % 2 == 0 else 0.45)
		angle = np.pi / 5 * i - np.pi / 2
		points.append((w / 2 + r * np.cos(angle), h / 2 + r * np.sin(angle)))
	draw.polygon(points, fill=(230, 180, 40, 255), outline=(60, 30, 0, 255))
	return im


def check_shapes(polys, mask):
	assert 1 <= len(polys) <= shapes.MAX_SHAPES
	for poly in polys:
		assert 3 <= len(poly) <= shapes.MAX_CORNERS
		assert shapes.is_convex(poly)
		assert shapes.area(poly) > 0  # Nintendo's winding
	covered = shapes.rasterize(polys)
	assert (covered & mask).sum() / mask.sum() > 0.9


def test_shapes_for_simple_and_odd_masks():
	y, x = np.mgrid[0:128, 0:128]
	disc = (x - 64) ** 2 + (y - 64) ** 2 < 50 ** 2
	check_shapes(shapes.shapes_from_mask(disc), disc)
	ring_with_arm = disc & ~((x - 64) ** 2 + (y - 64) ** 2 < 30 ** 2) | ((abs(y - 64) < 6) & (x > 10))
	check_shapes(shapes.shapes_from_mask(ring_with_arm), ring_with_arm)
	two = ((x - 30) ** 2 + (y - 30) ** 2 < 20 ** 2) | ((x - 95) ** 2 + (y - 95) ** 2 < 25 ** 2)
	check_shapes(shapes.shapes_from_mask(two), two)
	hull = shapes.shapes_from_mask(ring_with_arm, simple=True)
	assert len(hull) == 1 and len(hull[0]) <= 8


def test_make_badge_1x1():
	badge = makers.make_badge(star_picture(), badge_id=90000001, name="Pr_CuTest_star", category="CuTest", title="Test badge")
	raw = badge.build()
	assert f.Badge.parse(raw).build() == raw
	assert (badge.width, badge.height) == (1, 1) and badge.tiles == []
	claw = badge.claw_rgba()
	assert claw[64, 64, 3] == 255 and claw[0, 0, 3] == 0
	check_shapes(badge.polygons, claw[..., 3] > 127)
	assert badge.shadow_l8().max() > 200


def test_make_badge_2x2():
	badge = makers.make_badge(star_picture((128, 128)), badge_id=90000002, name="Pr_CuTest_big", category="CuTest", title="Big")
	assert (badge.width, badge.height) == (2, 2) and len(badge.tiles) == 4
	raw = badge.build()
	parsed = f.Badge.parse(raw)
	assert parsed.build() == raw
	assert parsed.full_rgba().shape == (128, 128, 4)


def test_make_icon_cabinet_category():
	icon = makers.make_icon(star_picture(), "CraneIcon_Test", f.Icon("x", bytes(0x2000), bytes(0xB4)))
	assert f.Icon.parse(icon.build()).name == "CraneIcon_Test"
	trim = f.Cabinet("CrSt_T", bytes(512 * 256 // 2), bytes(0x34))
	cabinet = makers.make_cabinet(star_picture((300, 200)), "CrSt_Test", trim)
	assert f.Cabinet.parse(cabinet.build()).picture().shape == (244, 404, 3)
	category = makers.make_category("CuTest", "Test", 7001, 7000, 12, 2, star_picture())
	parsed = f.Category.parse(category.build())
	assert (parsed.id, parsed.name, parsed.titles[0]) == (7001, "CuTest", "Test")

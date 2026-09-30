"""Yaz0, SARC and texture codecs."""

import os

import numpy as np

from bahelper import sarc, textures as tx, yaz0


def test_yaz0_round_trip():
	rng = np.random.default_rng(1)
	samples = [b"", b"a", bytes(5000), os.urandom(3000), bytes(rng.integers(0, 4, 20000, dtype=np.uint8)),
		b"abcabcabc" * 500 + os.urandom(100)]
	for data in samples:
		packed = yaz0.compress(data)
		assert packed[:4] == b"Yaz0"
		assert yaz0.decompress(packed) == data
	assert len(yaz0.compress(bytes(100000))) < 2000


def test_yaz0_partial():
	data = os.urandom(500) + bytes(5000)
	assert yaz0.decompress(yaz0.compress(data), 700)[:700] == data[:700]


def test_sarc_round_trip():
	files = {"a.txt": b"hello", "dir/b.bin": os.urandom(300), "pc/rt/Pr/x.prb.szs": b"x" * 129}
	assert sarc.sarc_read(sarc.sarc_write(files)) == files


def test_rgb565_a4_round_trip():
	rng = np.random.default_rng(2)
	rgb = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
	back = tx.decode_rgb565(tx.encode_rgb565(rgb), 64, 64)
	assert np.abs(back.astype(int) - rgb).max() <= 4
	alpha = (rng.integers(0, 16, (64, 64)) * 17).astype(np.uint8)
	assert (tx.decode_a4(tx.encode_a4(alpha), 64, 64) == alpha).all()


def test_tiling_inverse():
	image = np.arange(32 * 16).reshape(16, 32)
	assert (tx.untile(tx.tile(image), 32, 16) == image).all()


def test_etc1a4_quality():
	y, x = np.mgrid[0:128, 0:128]
	rgba = np.zeros((128, 128, 4), np.uint8)
	rgba[..., 0] = x * 2
	rgba[..., 1] = y * 2
	rgba[..., 2] = 128 + 60 * np.sin(x / 9)
	rgba[..., 3] = np.where((x - 64) ** 2 + (y - 64) ** 2 < 50 ** 2, 255, 0)
	back = tx.decode_etc1a4(tx.encode_etc1a4(rgba), 128, 128)
	opaque = rgba[..., 3] > 0
	assert tx.psnr(rgba[opaque][:, :3], back[opaque][:, :3]) > 30
	assert (back[..., 3] == rgba[..., 3]).all()


def test_etc1_quality():
	rng = np.random.default_rng(3)
	rgb = np.repeat(np.repeat(rng.integers(0, 256, (32, 64, 3), dtype=np.uint8), 8, 0), 8, 1)
	back = tx.decode_etc1(tx.encode_etc1(rgb), 512, 256)
	assert tx.psnr(rgb, back) > 28

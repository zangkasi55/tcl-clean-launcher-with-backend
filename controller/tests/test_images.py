from io import BytesIO

import pytest
from PIL import Image

from tvcontrol.images import save_image


def test_image_dimensions_size_and_alpha_are_safe(tmp_path):
    image = Image.new("RGBA", (1920, 1080), (255, 255, 255, 0))
    stream = BytesIO()
    image.save(stream, "PNG")
    for kind, size, limit in [("wallpaper", (1920, 1080), 300 * 1024),
                              ("artwork", (640, 360), 512 * 1024)]:
        name = save_image(stream.getvalue(), kind, tmp_path)
        path = tmp_path / name
        assert path.stat().st_size <= limit
        with Image.open(path) as result:
            assert result.size == size
            pixel = result.convert("RGB").getpixel((100, 100))
            tolerance = 2 if kind == "wallpaper" else 0
            assert max(abs(a - b) for a, b in zip(pixel, (11, 16, 32))) <= tolerance


def test_large_raw_invalid_and_unknown_images_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        save_image(b"x" * (8 * 1024 * 1024 + 1), "wallpaper", tmp_path)
    with pytest.raises(ValueError):
        save_image(b"<svg></svg>", "artwork", tmp_path)
    with pytest.raises(ValueError):
        save_image(b"data", "unknown", tmp_path)


def test_server_decoded_pixel_limit_remains_enforced(tmp_path):
    stream = BytesIO()
    Image.new("RGB", (6000, 3600), "#365e6b").save(stream, "JPEG")
    assert len(stream.getvalue()) < 8 * 1024 * 1024
    with pytest.warns(Image.DecompressionBombWarning):
        with pytest.raises(ValueError, match="20 million decoded pixels"):
            save_image(stream.getvalue(), "wallpaper", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_iphone_exif_orientation_is_applied_before_wallpaper_crop(tmp_path):
    image = Image.new("RGB", (400, 200), "#00ff00")
    image.paste("#ff0000", (0, 0, 160, 200))
    image.paste("#0000ff", (240, 0, 400, 200))
    exif = Image.Exif()
    exif[274] = 6
    stream = BytesIO()
    image.save(stream, "JPEG", quality=95, exif=exif)
    name = save_image(stream.getvalue(), "wallpaper", tmp_path)
    with Image.open(tmp_path / name) as result:
        assert result.size == (1920, 1080)
        for y, channel in [(50, 0), (540, 1), (1030, 2)]:
            pixel = result.getpixel((960, y))
            assert pixel[channel] > 230
            assert all(value < 25 for i, value in enumerate(pixel) if i != channel)

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 8 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 20_000_000


def save_image(data: bytes, kind: Literal["wallpaper", "artwork"], directory: Path) -> str:
    if kind not in {"wallpaper", "artwork"}:
        raise ValueError("Unknown image kind")
    if len(data) > MAX_UPLOAD:
        raise ValueError("Upload an image smaller than 8 MiB")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Use a JPEG, PNG or WebP image")
            if source.width * source.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError("Use an image with at most 20 million decoded pixels")
            source.load()
            foreground = ImageOps.exif_transpose(source).convert("RGBA")
            background = Image.new("RGBA", foreground.size, "#0B1020")
            background.alpha_composite(foreground)
            image = background.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("The image is invalid or exceeds the decoded-size limit") from exc
    output = BytesIO()
    if kind == "wallpaper":
        image = ImageOps.fit(image, (1920, 1080), method=Image.Resampling.LANCZOS)
        for quality in [80, 65, 50, 35]:
            output.seek(0)
            output.truncate()
            image.save(output, "JPEG", quality=quality, optimize=True)
            if output.tell() <= 300 * 1024:
                break
        if output.tell() > 300 * 1024:
            raise ValueError("This wallpaper could not be reduced to the TV's 300 KiB budget")
        suffix = "jpg"
    else:
        image = ImageOps.contain(image, (640, 360), method=Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (640, 360), "#0B1020")
        canvas.paste(image, ((640 - image.width) // 2, (360 - image.height) // 2))
        canvas.quantize(colors=256).save(output, "PNG", optimize=True)
        suffix = "png"
    payload = output.getvalue()
    name = f"{kind}-{hashlib.sha256(payload).hexdigest()}.{suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_bytes(payload)
    return name

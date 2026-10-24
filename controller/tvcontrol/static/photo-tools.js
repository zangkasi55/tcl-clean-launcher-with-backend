const SOURCE_LIMIT = 40 * 1024 * 1024;
const PIXEL_LIMIT = 60_000_000;
const PHOTO_TYPES = new Set(["image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"]);

async function decodePhoto(blob, name) {
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new Error("The photo could not be decoded. It may be damaged or use an unsupported image format."));
      image.src = url;
    });
    return {image, name, width: image.naturalWidth, height: image.naturalHeight,
      release: () => URL.revokeObjectURL(url)};
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
}

export async function openPhoto(file, onProgress) {
  if (!file.size || file.size > SOURCE_LIMIT) {
    throw new Error("Choose a photo between 1 byte and 40 MiB. Large photos are resized before uploading.");
  }
  const type = file.type.toLowerCase();
  const namedPhoto = /\.(jpe?g|png|webp|heic|heif)$/i.test(file.name);
  if (!PHOTO_TYPES.has(type) && !((!type || type === "application/octet-stream") && namedPhoto)) {
    throw new Error("Choose a JPEG, PNG, WebP, HEIC or HEIF photo. Export other formats as JPEG first.");
  }
  const heif = ["image/heic", "image/heif"].includes(type) || /\.(heic|heif)$/i.test(file.name);
  let photo;
  if (heif) onProgress?.("Opening HEIC/HEIF photo. It will be converted automatically to a supported format.");
  try {
    photo = await decodePhoto(file, file.name);
  } catch (error) {
    if (!heif) throw error;
    onProgress?.("Converting HEIC/HEIF to JPEG on this device. Large photos may take a moment...");
    try {
      const {heicTo} = await import("./heic-to.js?v=1.6.6");
      const image = await heicTo({blob: file, type: "bitmap"});
      photo = {image, name: file.name, width: image.width, height: image.height,
        release: () => image.close()};
    } catch (conversionError) {
      const detail = conversionError instanceof Error ? conversionError.message : "The HEIF decoder could not read this photo.";
      throw new Error(`Automatic HEIC/HEIF conversion failed: ${detail} Choose another photo or retry.`, {cause: conversionError});
    }
  }
  if (!photo.width || !photo.height || photo.width * photo.height > PIXEL_LIMIT) {
    photo.release();
    throw new Error("Choose a photo with at most 60 megapixels, or export a smaller copy first.");
  }
  return photo;
}

export function cropRectangle(width, height, {zoom = 1, x = 50, y = 50} = {}) {
  if (![width, height, zoom, x, y].every(Number.isFinite)
      || width <= 0 || height <= 0 || zoom < 1 || zoom > 4
      || x < 0 || x > 100 || y < 0 || y > 100) {
    throw new Error("The photo dimensions or crop position are invalid. Reset the crop and try again.");
  }
  const cropWidth = Math.min(width, height * 16 / 9) / zoom;
  const cropHeight = cropWidth * 9 / 16;
  return {left: (width - cropWidth) * x / 100, top: (height - cropHeight) * y / 100,
    width: cropWidth, height: cropHeight};
}

export function drawPhoto(canvas, photo, kind, selection = {}) {
  const context = canvas.getContext("2d");
  if (!context) throw new Error("This browser could not prepare the photo. Try a smaller JPEG.");
  context.fillStyle = "#0B1020";
  context.fillRect(0, 0, canvas.width, canvas.height);
  const {image} = photo;
  if (kind === "wallpaper") {
    const crop = cropRectangle(photo.width, photo.height, selection);
    context.drawImage(image, crop.left, crop.top, crop.width, crop.height,
      0, 0, canvas.width, canvas.height);
  } else if (kind === "artwork") {
    const scale = Math.min(canvas.width / photo.width, canvas.height / photo.height);
    const width = photo.width * scale, height = photo.height * scale;
    context.drawImage(image, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
  } else throw new Error("Unknown image kind");
}

export async function preparePhotoUpload(photo, kind, selection = {}) {
  const canvas = document.createElement("canvas");
  canvas.width = kind === "wallpaper" ? 1920 : 640;
  canvas.height = kind === "wallpaper" ? 1080 : 360;
  drawPhoto(canvas, photo, kind, selection);
  const type = kind === "wallpaper" ? "image/jpeg" : "image/png";
  const blob = await new Promise(resolve => canvas.toBlob(resolve, type, 0.92));
  if (!blob || !blob.size || blob.size > 8 * 1024 * 1024) {
    throw new Error("This browser could not prepare an upload below 8 MiB. Choose a smaller photo and try again.");
  }
  const suffix = kind === "wallpaper" ? "jpg" : "png";
  return new File([blob], `${photo.name.replace(/\.[^.]+$/, "")}-tv.${suffix}`, {type});
}

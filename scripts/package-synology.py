"""Export a minimal NAS build context, without credentials, caches or TV dumps."""
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
required = ["Dockerfile", "docker-compose.yaml", ".dockerignore", "LICENSE",
            "launcher/FONT-LICENSE-OFL.txt", "controller/README.md",
            "controller/home-assistant/tcl-tv-view.json",
            "controller/pyproject.toml", "controller/requirements.lock",
            "dist/tvhome-release.apk"]
for filename in required:
    if not root.joinpath(filename).is_file():
        raise RuntimeError(f"Build/package input is missing: {filename}")
files = [root.joinpath(name) for name in required]
files.extend(path for path in root.joinpath("controller", "tvcontrol").rglob("*")
             if path.is_file() and "__pycache__" not in path.parts)
target = root / "dist" / "tcl-home-control-synology.zip"
with ZipFile(target, "w", ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, path.relative_to(root).as_posix())
with ZipFile(target) as archive:
    if archive.testzip() is not None:
        raise RuntimeError("The deployment archive failed its CRC check")
    if any("adbkey" in name or "password" in name or name.endswith(".sqlite3")
           for name in archive.namelist()):
        raise RuntimeError("A private runtime file entered the deployment archive")
print(f"Deployment archive: {target}")
print(f"SHA256: {hashlib.sha256(target.read_bytes()).hexdigest()}")

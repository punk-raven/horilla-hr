"""Copy bundled demo media into the default storage (MEDIA_ROOT, S3 or R2)."""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


def _copy_glob(src_dir: Path, dest_dir: str, pattern: str) -> int:
    if not src_dir.exists():
        return 0
    copied = 0
    for src in src_dir.glob(pattern):
        if not src.is_file():
            continue
        # The fixtures point at these exact names, and save() renames rather
        # than overwrites, so replace what a previous load left behind.
        name = f"{dest_dir}/{src.name}"
        default_storage.delete(name)
        with src.open("rb") as fh:
            saved = default_storage.save(name, File(fh, name=src.name))
        if saved != name:
            raise RuntimeError(f"Demo media saved as {saved}, expected {name}")
        copied += 1
    return copied


def _count_existing(dest_dir: str, prefix: str) -> int:
    try:
        _dirs, files = default_storage.listdir(dest_dir)
    except FileNotFoundError:
        return 0
    return sum(1 for name in files if name.startswith(prefix))


def copy_demo_media(load_dir: Path | None = None) -> dict[str, int]:
    """
    Copy company icons and employee avatars into the default storage.

    Fixture image fields point at ``base/icon/*.png`` and
    ``employee/profile/avatar_*.jpg`` - without this step those URLs 404.
    """
    root = Path(load_dir) if load_dir else Path(settings.BASE_DIR) / "load_data"

    icons = _copy_glob(root / "icons", "base/icon", "*.png")
    # Also accept legacy Horilla_*.png already in storage if icons/ was empty
    if icons == 0:
        icons = _count_existing("base/icon", "Horilla_")

    avatars = _copy_glob(root / "avatars", "employee/profile", "avatar_*.jpg")
    if avatars == 0:
        avatars = _copy_glob(root / "avatars", "employee/profile", "*.jpg")

    logger.info("Demo media copied: icons=%s avatars=%s", icons, avatars)
    return {"icons": icons, "avatars": avatars}

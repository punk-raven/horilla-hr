"""
horilla/media.py

Read uploaded files without assuming they sit on local disk.

With S3 or Cloudflare R2 as the default storage there is no file under
MEDIA_ROOT and FieldFile.path raises NotImplementedError, so every read goes
through the storage the file was saved with.
"""

import base64
import logging
import mimetypes

from django.core.files.storage import FileSystemStorage, default_storage

logger = logging.getLogger(__name__)


def is_remote_storage(storage=None):
    """True when files live outside the local filesystem (S3, R2, GCS)."""
    storage = default_storage if storage is None else storage
    return not isinstance(storage, FileSystemStorage)


def _storage_and_name(file):
    """The storage and name of a FieldFile, or of a name in default storage."""
    storage = getattr(file, "storage", None) or default_storage
    name = getattr(file, "name", file)
    return storage, str(name) if name else ""


def read_media(file):
    """
    Contents of a stored file, as bytes.

    ``file`` is a FieldFile or a storage name. Returns None when there is no
    file or it cannot be read, because callers embed optional content (a
    company logo) and carry on without it.
    """
    storage, name = _storage_and_name(file)
    # Uploads never produce hidden paths; the Docker entrypoint keeps the
    # generated SECRET_KEY at media/.generated_secret_key (see protected_media).
    if not name or any(part.startswith(".") for part in name.split("/")):
        return None
    try:
        with storage.open(name, "rb") as fh:
            return fh.read()
    except Exception:
        logger.warning("Media file %s could not be read", name, exc_info=True)
        return None


def media_data_uri(file, content=None):
    """
    A stored file as a data: URI, for PDF renderers; None if unreadable.

    Pass ``content`` when the bytes were already read with read_media().
    """
    if content is None:
        content = read_media(file)
    if content is None:
        return None
    _storage, name = _storage_and_name(file)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{content_type};base64,{encoded}"

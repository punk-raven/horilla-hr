import os
import posixpath
import shutil
import zipfile
from datetime import datetime


def zip_folder(folder_path, output_zip_path):
    with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Walk the directory
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                # Create the full filepath
                file_path = os.path.join(root, file)
                # Add file to zip, preserving the folder structure
                zipf.write(file_path, os.path.relpath(file_path, folder_path))


def iter_storage_files(storage, prefix=""):
    """Yield every file name in ``storage`` under ``prefix``, recursively."""
    dirs, files = storage.listdir(prefix)
    for name in files:
        yield posixpath.join(prefix, name) if prefix else name
    for name in dirs:
        yield from iter_storage_files(
            storage, posixpath.join(prefix, name) if prefix else name
        )


def _modified(storage, name):
    # Streaming through zipf.open() would otherwise stamp every entry 1980-01-01.
    try:
        stamp = storage.get_modified_time(name)
    except (NotImplementedError, OSError):
        stamp = datetime.now()
    return stamp.timetuple()[:6]


def zip_storage(storage, output_zip_path, exclude=()):
    """
    Zip every file in a Django storage backend, keeping its relative names.

    Works for local disk and S3/R2 alike, where zip_folder only sees a local
    MEDIA_ROOT that is empty when media is stored remotely. Files whose name
    starts with one of ``exclude`` are skipped. Returns the number of files.
    """
    count = 0
    with zipfile.ZipFile(
        output_zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True
    ) as zipf:
        for name in iter_storage_files(storage):
            if any(name.startswith(prefix) for prefix in exclude):
                continue
            info = zipfile.ZipInfo(name, date_time=_modified(storage, name))
            info.compress_type = zipfile.ZIP_DEFLATED
            with storage.open(name, "rb") as source, zipf.open(
                info, "w", force_zip64=True
            ) as target:
                shutil.copyfileobj(source, target)
            count += 1
    return count

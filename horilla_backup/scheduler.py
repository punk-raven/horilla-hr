"""
Google Drive backup job.

The schedule is edited at runtime from the settings page, so it cannot be a
static ``register_job`` entry. Instead a static reconcile job runs every minute
inside ``manage.py run_scheduler`` (the scheduler container) and adds, replaces
or removes the real backup job on that scheduler to match the
GoogleDriveBackup row. Web workers only write the row; they never start a
scheduler or run a backup themselves.

The backup itself writes to a private temporary directory, never the app
directory (read-only on Railway), and deletes it once uploaded.
"""

import logging
import tempfile
from datetime import datetime
from pathlib import Path

import pytz
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import connection

from horilla.scheduling import get_running_scheduler, register_job

from .gdrive import get_credentials, upload_file
from .models import GoogleDriveBackup
from .pgdump import dump_postgres_db
from .zip import zip_storage

logger = logging.getLogger(__name__)

GDRIVE_BACKUP_JOB_ID = "horilla_backup.gdrive_backup"

# Never copy the Drive credentials, or the entrypoint's generated SECRET_KEY,
# into a media archive that is itself uploaded to Drive.
MEDIA_BACKUP_EXCLUDE = (
    GoogleDriveBackup._meta.get_field("oauth_credentials_file").upload_to,
    ".generated_secret_key",
)


def run_gdrive_backup(google_drive):
    """
    Dump the database and/or zip media, and upload each to Drive.

    Each part runs even if the other fails; any failure is raised afterwards
    so run_scheduler's error listener reports it instead of a silent success.
    """
    if connection.vendor != "postgresql":
        raise RuntimeError("Google Drive backup needs a PostgreSQL database.")

    credentials = get_credentials(google_drive)
    stamp = datetime.now(pytz.timezone(settings.TIME_ZONE)).strftime(
        "%Y-%m-%d_%H-%M-%S"
    )
    failures = []
    uploaded = []
    with tempfile.TemporaryDirectory(prefix="horilla-backup-") as workdir:
        if google_drive.backup_db:
            try:
                db = settings.DATABASES["default"]
                dump_file = Path(workdir) / f"horilla-db-{stamp}.dump"
                dump_postgres_db(db, dump_file)
                uploaded.append(
                    upload_file(
                        str(dump_file),
                        google_drive,
                        google_drive.gdrive_folder_id,
                        credentials=credentials,
                    )
                )
                dump_file.unlink()
            except Exception as error:
                logger.exception("Google Drive database backup failed")
                failures.append(f"database: {error}")

        if google_drive.backup_media:
            try:
                zip_file = Path(workdir) / f"horilla-media-{stamp}.zip"
                count = zip_storage(
                    default_storage, zip_file, exclude=MEDIA_BACKUP_EXCLUDE
                )
                logger.info("Zipped %d media files", count)
                uploaded.append(
                    upload_file(
                        str(zip_file),
                        google_drive,
                        google_drive.gdrive_folder_id,
                        credentials=credentials,
                    )
                )
                zip_file.unlink()
            except Exception as error:
                logger.exception("Google Drive media backup failed")
                failures.append(f"media: {error}")

    if failures:
        raise RuntimeError("Google Drive backup failed: " + "; ".join(failures))
    return uploaded


def scheduled_gdrive_backup():
    """Entry point of the reconciled job. Re-reads the row so a backup that was
    stopped or deleted since the job was queued does not run."""
    google_drive = GoogleDriveBackup.objects.filter(active=True).first()
    if google_drive is None:
        return None
    return run_gdrive_backup(google_drive)


def gdrive_trigger(google_drive):
    """APScheduler trigger for the row's schedule, or None if it has none."""
    tz = pytz.timezone(settings.TIME_ZONE)
    if google_drive.interval and google_drive.seconds:
        return IntervalTrigger(seconds=google_drive.seconds, timezone=tz)
    if google_drive.fixed and google_drive.hour is not None:
        # The form accepts 0-24 and 0-60; cron wants 0-23 and 0-59.
        return CronTrigger(
            hour=google_drive.hour % 24,
            minute=(google_drive.minute or 0) % 60,
            timezone=tz,
        )
    return None


def _schedule_signature(google_drive):
    return (
        f"gdrive interval={bool(google_drive.interval)} "
        f"seconds={google_drive.seconds} fixed={bool(google_drive.fixed)} "
        f"hour={google_drive.hour} minute={google_drive.minute}"
    )


def sync_gdrive_backup_job(scheduler=None):
    """
    Make the scheduler's backup job match the GoogleDriveBackup row.

    The job's name carries the schedule it was built from, so an unchanged
    schedule leaves the persisted job (and its next run time) alone, including
    across a scheduler restart.
    """
    scheduler = scheduler or get_running_scheduler()
    if scheduler is None:
        return
    google_drive = GoogleDriveBackup.objects.filter(active=True).first()
    trigger = gdrive_trigger(google_drive) if google_drive else None
    existing = scheduler.get_job(GDRIVE_BACKUP_JOB_ID)

    if trigger is None:
        if existing is not None:
            scheduler.remove_job(GDRIVE_BACKUP_JOB_ID)
            logger.info("Removed the Google Drive backup job")
        return

    signature = _schedule_signature(google_drive)
    if existing is not None and existing.name == signature:
        return
    scheduler.add_job(
        scheduled_gdrive_backup,
        trigger,
        id=GDRIVE_BACKUP_JOB_ID,
        name=signature,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # A backup due while the scheduler was down (a redeploy) runs once
        # when it is back, rather than being skipped.
        misfire_grace_time=None,
    )
    logger.info("Scheduled the Google Drive backup job (%s)", signature)


register_job(sync_gdrive_backup_job, "interval", seconds=60)

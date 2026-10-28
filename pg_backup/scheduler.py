"""
pg_backup.scheduler

This module sets up scheduled PostgreSQL backups using APScheduler and pg_dump.

Backups are only enabled if the configured Django database engine is PostgreSQL
and settings.LOCAL_BACKUPS_ENABLED is True (env LOCAL_BACKUPS_ENABLED, default
True). Set it False where the server's disk is read-only or ephemeral, as on
Railway: the backups would fail or vanish with the container.
It supports backing up the default Django database at multiple daily times,
configured via environment variables.

Environment Variables:
----------------------
- BACKUP_CRON_TIMES (str, optional)
    A comma-separated list of times in 24h format ("HH:MM") to run backups.
    Example: "02:00,10:00,18:00"
    Default: "02:00,10:00,18:00"
    If unset or empty, scheduling is disabled and no automated backups will run.

- BACKUP_DIR (str, optional)
    Directory path where PostgreSQL backups will be stored.
    Default: <project_root>/pg_backup/backups

Requirements:
-------------
- The system must have `pg_dump` available in PATH.
- The database user must have permission to perform backups.
- APScheduler and django-environ must be installed and configured.

Logging:
--------
Logs are emitted using the `pg_backup` logger and include backup progress and scheduler setup.
Logging format and handlers can be customized via `LOGGING_CONFIG` in this module.

Usage:
------
`manage.py run_scheduler` imports this module, which registers one cron job per
BACKUP_CRON_TIMES entry with horilla.scheduling. Importing it never starts a
scheduler, so the jobs run once in the scheduler process rather than once per
gunicorn worker. Backups can also be triggered manually by calling
`backup_postgres()`.

"""

import datetime
import logging
import logging.config
import os
from pathlib import Path

import environ
from django.conf import settings

from horilla.scheduling import register_job
from horilla_backup.pgdump import dump_postgres_db

# === Logging Configuration ===

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "loggers": {
        "pg_backup": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger("pg_backup")

# === Configuration ===

env = environ.Env()

default_backup_dir = Path(settings.BASE_DIR) / "pg_backup" / "backups"
BACKUP_DIR = Path(env("BACKUP_DIR", default=str(default_backup_dir)))
BACKUP_CRON_TIMES = env("BACKUP_CRON_TIMES", default="02:00,10:00,18:00")
DATE_FORMAT = "%Y-%m-%d_%H-%M-%S"


def backup_postgres():
    """
    Method that dump pg data
    """
    db = settings.DATABASES["default"]
    timestamp = datetime.datetime.now().strftime(DATE_FORMAT)
    backup_file = BACKUP_DIR / f"{db['NAME']}_backup_{timestamp}.sql"
    os.makedirs(BACKUP_DIR, exist_ok=True)
    logger.info("Starting backup: %s", backup_file)
    # Raises on failure, so run_scheduler reports it instead of a silent miss.
    dump_postgres_db(db, backup_file)
    logger.info("Backup successful: %s", backup_file)


def parse_cron_times(value):
    """``"02:00,18:30"`` -> ``[(2, 0), (18, 30)]``, skipping invalid entries."""
    times = []
    for time_str in (t.strip() for t in value.split(",")):
        if not time_str:
            continue
        try:
            hour, minute = map(int, time_str.split(":"))
            times.append((hour, minute))
        except ValueError:
            logger.error("Invalid time format in BACKUP_CRON_TIMES: '%s'", time_str)
    return times


def register_backup_jobs():
    """Register one cron job per configured time. Returns the times registered."""
    engine = settings.DATABASES["default"]["ENGINE"]
    if "postgresql" not in engine:
        logger.warning(
            "Skipping backup scheduler: not a PostgreSQL database (engine: %s)", engine
        )
        return []
    if not settings.LOCAL_BACKUPS_ENABLED:
        logger.info("LOCAL_BACKUPS_ENABLED is off; pg_backup jobs not registered.")
        return []
    if not BACKUP_CRON_TIMES:
        logger.warning("BACKUP_CRON_TIMES not set. Scheduler is disabled.")
        return []

    times = parse_cron_times(BACKUP_CRON_TIMES)
    for hour, minute in times:
        register_job(
            backup_postgres,
            "cron",
            job_id=f"pg_backup.backup_{hour}_{minute}",
            hour=hour,
            minute=minute,
        )
        logger.info("Backup scheduled at %02d:%02d", hour, minute)
    return times


register_backup_jobs()

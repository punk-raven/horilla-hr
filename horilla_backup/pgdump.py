import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)


def pg_connection(db):
    """
    Turn a Django DATABASES entry into pg_dump connection arguments.

    Returns ``(argv, env)``. Every value comes from the entry itself (which
    DATABASE_URL populates), never from a default: a backup that quietly fell
    back to localhost dumped nothing on any deployment where Postgres is a
    separate host. An empty HOST/PORT is left out so libpq applies its own
    defaults (the local socket), exactly as Django does.

    The password goes in ``env`` as PGPASSWORD. On argv it would be visible to
    every process on the host through ps and /proc.
    """
    argv = []
    if db.get("HOST"):
        argv += ["--host", str(db["HOST"])]
    if db.get("PORT"):
        argv += ["--port", str(db["PORT"])]
    if db.get("USER"):
        argv += ["--username", str(db["USER"])]

    env = {**os.environ}
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = str(db["PASSWORD"])
    # DATABASE_URL query parameters (?sslmode=require) land in OPTIONS.
    sslmode = (db.get("OPTIONS") or {}).get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = str(sslmode)
    return argv, env


def dump_postgres_db(db, output_file):
    """
    Write a custom-format (pg_restore-able) dump of ``db`` to ``output_file``.

    Raises on failure: the caller must never upload a missing or partial file.
    """
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError("pg_dump is not installed or not on PATH.")

    argv, env = pg_connection(db)
    command = [
        pg_dump,
        *argv,
        # Never prompt: with no password available a prompt would hang the job.
        "--no-password",
        "--format=custom",
        "--file",
        str(output_file),
        str(db["NAME"]),
    ]
    try:
        subprocess.run(command, check=True, text=True, capture_output=True, env=env)
    except subprocess.CalledProcessError as e:
        logger.error("pg_dump failed (exit %s): %s", e.returncode, e.stderr)
        raise

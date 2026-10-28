from django.apps import AppConfig


class PgGitBackupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pg_backup"
    # No ready() hook: it used to import pg_backup.scheduler, which started a
    # BackgroundScheduler in every process (each gunicorn worker took its own
    # backups). run_scheduler now imports the module and owns execution.

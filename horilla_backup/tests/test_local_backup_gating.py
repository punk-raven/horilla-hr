"""
LOCAL_BACKUPS_ENABLED gates every backup that writes to the server's own disk:
horilla_backup's LocalBackup and the pg_backup app. Railway turns it off (the
app directory is read-only and nothing on disk survives a redeploy); every
other install keeps upstream's behaviour.
"""

from unittest import mock

from django.contrib.admin import AdminSite
from django.test import SimpleTestCase, override_settings

from horilla.scheduling import clear_registry, get_registered_jobs, register_job
from horilla_backup.admin import register_models
from horilla_backup.models import GoogleDriveBackup, LocalBackup

PG_ENGINE = "django.db.backends.postgresql"


class AdminGatingTests(SimpleTestCase):
    @override_settings(LOCAL_BACKUPS_ENABLED=False)
    def test_local_backup_hidden_when_disabled(self):
        site = AdminSite()
        register_models(site)
        self.assertFalse(site.is_registered(LocalBackup))
        self.assertTrue(site.is_registered(GoogleDriveBackup))

    @override_settings(LOCAL_BACKUPS_ENABLED=True)
    def test_local_backup_offered_by_default(self):
        site = AdminSite()
        register_models(site)
        self.assertTrue(site.is_registered(LocalBackup))
        self.assertTrue(site.is_registered(GoogleDriveBackup))

    def test_default_is_enabled(self):
        from django.conf import settings

        self.assertTrue(settings.LOCAL_BACKUPS_ENABLED)


class PgBackupGatingTests(SimpleTestCase):
    def setUp(self):
        self._saved = get_registered_jobs()
        clear_registry()
        from pg_backup import scheduler as pg_scheduler

        self.pg = pg_scheduler
        clear_registry()

    def tearDown(self):
        clear_registry()
        for job in self._saved:
            register_job(job.func, job.trigger, job_id=job.job_id, **job.kwargs)

    def _pg_jobs(self):
        return [j for j in get_registered_jobs() if j.job_id.startswith("pg_backup.")]

    def _register(self, **settings_overrides):
        databases = {"default": {"ENGINE": PG_ENGINE, "NAME": "x"}}
        with override_settings(DATABASES=databases, **settings_overrides):
            with mock.patch.object(self.pg, "BACKUP_CRON_TIMES", "02:00,18:30"):
                return self.pg.register_backup_jobs()

    def test_not_registered_when_disabled(self):
        self.assertEqual(self._register(LOCAL_BACKUPS_ENABLED=False), [])
        self.assertEqual(self._pg_jobs(), [])

    def test_registered_with_the_scheduler_registry_when_enabled(self):
        self.assertEqual(self._register(LOCAL_BACKUPS_ENABLED=True), [(2, 0), (18, 30)])
        jobs = self._pg_jobs()
        self.assertEqual(
            [(j.job_id, j.trigger, j.kwargs) for j in jobs],
            [
                ("pg_backup.backup_18_30", "cron", {"hour": 18, "minute": 30}),
                ("pg_backup.backup_2_0", "cron", {"hour": 2, "minute": 0}),
            ],
        )
        self.assertIs(jobs[0].func, self.pg.backup_postgres)

    def test_parse_cron_times_skips_garbage(self):
        self.assertEqual(
            self.pg.parse_cron_times("02:00, nope ,,23:59"), [(2, 0), (23, 59)]
        )

    def test_backup_postgres_uses_the_shared_dump_helper(self):
        db = {"ENGINE": PG_ENGINE, "NAME": "horilla", "HOST": "db.internal"}
        with override_settings(DATABASES={"default": db}), mock.patch.object(
            self.pg, "dump_postgres_db"
        ) as dump, mock.patch.object(self.pg.os, "makedirs"):
            self.pg.backup_postgres()
        args = dump.call_args.args
        self.assertEqual(args[0]["HOST"], "db.internal")
        self.assertTrue(str(args[1]).startswith(str(self.pg.BACKUP_DIR)))

    def test_app_config_no_longer_starts_a_scheduler(self):
        import inspect

        from pg_backup import apps

        self.assertNotIn("def ready", inspect.getsource(apps))

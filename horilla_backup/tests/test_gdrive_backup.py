"""
Google Drive backup on a cloud deployment (Railway + R2).

What broke there: pg_dump defaulted to localhost, the password was put in the
process-wide environment, the dump was written into the (read-only) app
directory, the credentials file was read through FieldFile.path (which S3/R2
storage does not implement), and the job ran in a BackgroundScheduler inside
every gunicorn worker.
"""

import json
import os
import subprocess
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage, Storage, default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from base.models import Company
from horilla.scheduling import get_registered_jobs
from horilla.testkit import make_employee, make_user, read_only_cwd
from horilla_backup import gdrive, scheduler
from horilla_backup.forms import GdriveBackupSetupForm
from horilla_backup.models import GoogleDriveBackup
from horilla_backup.pgdump import dump_postgres_db, pg_connection
from horilla_backup.zip import zip_storage


class NoPathStorage(Storage):
    """
    Minimal in-memory storage with no local path, like S3/R2's
    S3Boto3Storage. (Django's InMemoryStorage implements path(), so it would
    not catch a .path regression.) Contents are per-class, reset per test.
    """

    files = {}

    def _open(self, name, mode="rb"):
        return ContentFile(self.files[name], name=name)

    def _save(self, name, content):
        self.files[name] = content.read()
        return name

    def exists(self, name):
        return name in self.files

    def delete(self, name):
        self.files.pop(name, None)

    def size(self, name):
        return len(self.files[name])

    def url(self, name):
        return f"https://bucket.example/{name}"

    def listdir(self, path):
        prefix = f"{path.rstrip('/')}/" if path else ""
        dirs, files = set(), []
        for name in self.files:
            if not name.startswith(prefix):
                continue
            head, _, tail = name[len(prefix) :].partition("/")
            if tail:
                dirs.add(head)
            else:
                files.append(head)
        return sorted(dirs), sorted(files)


NO_PATH_STORAGE = {
    **settings.STORAGES,
    "default": {"BACKEND": f"{__name__}.NoPathStorage"},
}

SERVICE_ACCOUNT_INFO = {
    "type": "service_account",
    "client_email": "backup@project.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----\n",
    "token_uri": "https://oauth2.googleapis.com/token",
}
OAUTH_CLIENT_INFO = {
    "web": {
        "client_id": "id.apps.googleusercontent.com",
        "client_secret": "secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

RAILWAY_DB = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "railway",
    "USER": "postgres",
    "PASSWORD": "s3cret-pw",
    "HOST": "postgres.railway.internal",
    "PORT": 5432,
    "OPTIONS": {},
}


class PgConnectionTests(SimpleTestCase):
    def test_uses_the_configured_host_port_and_user(self):
        argv, env = pg_connection(RAILWAY_DB)
        self.assertEqual(
            argv,
            [
                "--host",
                "postgres.railway.internal",
                "--port",
                "5432",
                "--username",
                "postgres",
            ],
        )
        self.assertEqual(env["PGPASSWORD"], "s3cret-pw")

    def test_password_is_never_on_argv(self):
        argv, _ = pg_connection(RAILWAY_DB)
        self.assertNotIn("s3cret-pw", " ".join(argv))

    def test_empty_host_and_port_fall_back_to_libpq_not_localhost(self):
        argv, _ = pg_connection({**RAILWAY_DB, "HOST": "", "PORT": ""})
        self.assertNotIn("--host", argv)
        self.assertNotIn("--port", argv)
        self.assertNotIn("localhost", argv)

    def test_sslmode_from_database_url_options(self):
        _, env = pg_connection({**RAILWAY_DB, "OPTIONS": {"sslmode": "require"}})
        self.assertEqual(env["PGSSLMODE"], "require")

    def test_database_url_is_parsed_into_these_args(self):
        import environ

        db = environ.Env.db_url_config(
            "postgres://u:p%40ss@db.example.net:6543/horilla?sslmode=require"
        )
        argv, env = pg_connection(db)
        self.assertEqual(argv[:4], ["--host", "db.example.net", "--port", "6543"])
        self.assertEqual(env["PGPASSWORD"], "p@ss")
        self.assertEqual(env["PGSSLMODE"], "require")


class DumpPostgresDbTests(SimpleTestCase):
    @mock.patch("horilla_backup.pgdump.shutil.which", return_value="/usr/bin/pg_dump")
    @mock.patch("horilla_backup.pgdump.subprocess.run")
    def test_runs_pg_dump_with_env_password_and_leaves_os_environ_alone(
        self, run, _which
    ):
        os.environ.pop("PGPASSWORD", None)
        dump_postgres_db(RAILWAY_DB, "/tmp/x.dump")

        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/pg_dump")
        self.assertIn("--format=custom", command)
        self.assertIn("--no-password", command)
        self.assertEqual(command[-1], "railway")
        self.assertNotIn("s3cret-pw", command)
        self.assertEqual(run.call_args.kwargs["env"]["PGPASSWORD"], "s3cret-pw")
        self.assertNotIn("PGPASSWORD", os.environ)

    @mock.patch("horilla_backup.pgdump.shutil.which", return_value=None)
    def test_missing_pg_dump_raises(self, _which):
        with self.assertRaises(RuntimeError):
            dump_postgres_db(RAILWAY_DB, "/tmp/x.dump")

    @mock.patch("horilla_backup.pgdump.shutil.which", return_value="/usr/bin/pg_dump")
    @mock.patch(
        "horilla_backup.pgdump.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "pg_dump", stderr="boom"),
    )
    def test_failure_propagates(self, _run, _which):
        with self.assertRaises(subprocess.CalledProcessError):
            dump_postgres_db(RAILWAY_DB, "/tmp/x.dump")


class CredentialsKindTests(SimpleTestCase):
    def test_service_account(self):
        self.assertEqual(
            gdrive.credentials_kind(SERVICE_ACCOUNT_INFO), gdrive.SERVICE_ACCOUNT
        )

    def test_oauth_web_client(self):
        self.assertEqual(gdrive.credentials_kind(OAUTH_CLIENT_INFO), gdrive.OAUTH)

    def test_rejects_other_shapes(self):
        for info in (
            {},
            {"installed": {}},
            {"type": "service_account"},
            {"web": {"client_id": "x"}},
            [],
        ):
            with self.subTest(info=info), self.assertRaises(ValueError):
                gdrive.credentials_kind(info)


class NoPathStorageMixin:
    def setUp(self):
        super().setUp()
        NoPathStorage.files = {}


@override_settings(STORAGES=NO_PATH_STORAGE)
class StorageCredentialsTests(NoPathStorageMixin, TestCase):
    """The credentials file must be read through storage. NoPathStorage,
    like S3/R2, has no local path, so any .path use fails these tests."""

    def _backup(self, info, **fields):
        backup = GoogleDriveBackup(gdrive_folder_id="folder", backup_db=True, **fields)
        backup.oauth_credentials_file.save(
            "creds.json", ContentFile(json.dumps(info).encode()), save=False
        )
        backup.save()
        return GoogleDriveBackup.objects.get(pk=backup.pk)

    def test_storage_has_no_local_path(self):
        backup = self._backup(SERVICE_ACCOUNT_INFO)
        self.assertIsInstance(default_storage._wrapped, NoPathStorage)
        with self.assertRaises(NotImplementedError):
            backup.oauth_credentials_file.path

    def test_reads_json_through_storage(self):
        backup = self._backup(SERVICE_ACCOUNT_INFO)
        self.assertEqual(
            gdrive.read_credentials_info(backup.oauth_credentials_file),
            SERVICE_ACCOUNT_INFO,
        )

    @mock.patch(
        "horilla_backup.gdrive.service_account.Credentials.from_service_account_info"
    )
    def test_service_account_credentials(self, from_info):
        backup = self._backup(SERVICE_ACCOUNT_INFO)
        creds = gdrive.get_credentials(backup)
        from_info.assert_called_once_with(SERVICE_ACCOUNT_INFO, scopes=gdrive.SCOPES)
        self.assertIs(creds, from_info.return_value)
        self.assertFalse(gdrive.needs_authorization(backup))

    def test_oauth_without_tokens_needs_authorization(self):
        backup = self._backup(OAUTH_CLIENT_INFO)
        self.assertTrue(gdrive.needs_authorization(backup))
        with self.assertRaises(RuntimeError):
            gdrive.get_credentials(backup)

    def test_expired_oauth_token_is_refreshed_and_saved(self):
        backup = self._backup(
            OAUTH_CLIENT_INFO,
            access_token="old",
            refresh_token="refresh",
            token_expiry=timezone.now() - timedelta(hours=1),
        )
        new_expiry = (timezone.now() + timedelta(hours=1)).replace(tzinfo=None)

        def fake_refresh(creds, _request):
            creds.token = "new"
            creds.expiry = new_expiry

        with mock.patch.object(
            gdrive.Credentials, "refresh", autospec=True, side_effect=fake_refresh
        ):
            creds = gdrive.get_credentials(backup)
        self.assertEqual(creds.token, "new")
        backup.refresh_from_db()
        self.assertEqual(backup.access_token, "new")
        self.assertTrue(timezone.is_aware(backup.token_expiry))


@override_settings(STORAGES=NO_PATH_STORAGE)
class RunGdriveBackupTests(NoPathStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.backup = GoogleDriveBackup(
            gdrive_folder_id="folder-id", backup_db=True, backup_media=False
        )
        self.backup.oauth_credentials_file.save(
            "creds.json",
            ContentFile(json.dumps(SERVICE_ACCOUNT_INFO).encode()),
            save=False,
        )
        self.backup.save()
        patcher = mock.patch.object(scheduler, "get_credentials", return_value="creds")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dump_goes_to_a_temp_dir_is_uploaded_then_removed(self):
        seen = {}

        def fake_dump(db, output_file):
            seen["db"] = db
            Path(output_file).write_bytes(b"PGDMP fake dump")

        def fake_upload(path, backup, folder, credentials=None):
            seen["path"] = path
            seen["bytes"] = Path(path).read_bytes()
            seen["folder"] = folder
            seen["credentials"] = credentials
            return "drive-file-id"

        with mock.patch.object(
            scheduler, "dump_postgres_db", side_effect=fake_dump
        ), mock.patch.object(scheduler, "upload_file", side_effect=fake_upload):
            result = scheduler.run_gdrive_backup(self.backup)

        self.assertEqual(result, ["drive-file-id"])
        self.assertIs(seen["db"], settings.DATABASES["default"])
        path = Path(seen["path"])
        self.assertTrue(
            str(path).startswith(os.path.realpath(tempfile.gettempdir()))
            or str(path).startswith(tempfile.gettempdir())
        )
        self.assertFalse(str(path.resolve()).startswith(str(settings.BASE_DIR)))
        self.assertRegex(path.name, r"^horilla-db-.*\.dump$")
        self.assertEqual(seen["bytes"], b"PGDMP fake dump")
        self.assertEqual(seen["folder"], "folder-id")
        self.assertEqual(seen["credentials"], "creds")
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    def test_never_writes_to_a_read_only_working_directory(self):
        # The production image runs as a non-root user that cannot write to
        # /app, so a dump or zip staged under a relative path fails there.
        self.backup.backup_media = True
        default_storage.save("employee/photo.png", ContentFile(b"png"))
        uploaded = {}

        def fake_dump(db, output_file):
            Path(output_file).write_bytes(b"PGDMP fake dump")

        def fake_upload(path, backup, folder, credentials=None):
            self.assertTrue(os.path.isabs(path))
            uploaded[Path(path).suffix] = Path(path).read_bytes()
            return "id"

        with (
            mock.patch.object(scheduler, "dump_postgres_db", side_effect=fake_dump),
            mock.patch.object(scheduler, "upload_file", side_effect=fake_upload),
            read_only_cwd(),
        ):
            scheduler.run_gdrive_backup(self.backup)

        self.assertEqual(sorted(uploaded), [".dump", ".zip"])
        self.assertEqual(uploaded[".dump"], b"PGDMP fake dump")

    def test_failed_dump_is_not_uploaded_and_raises(self):
        paths = []

        def fake_dump(db, output_file):
            paths.append(Path(output_file))
            Path(output_file).write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, "pg_dump")

        with mock.patch.object(
            scheduler, "dump_postgres_db", side_effect=fake_dump
        ), mock.patch.object(scheduler, "upload_file") as upload:
            with self.assertRaises(RuntimeError):
                scheduler.run_gdrive_backup(self.backup)
        upload.assert_not_called()
        self.assertFalse(paths[0].parent.exists())

    def test_media_is_zipped_from_storage_without_the_credentials(self):
        self.backup.backup_db = False
        self.backup.backup_media = True
        default_storage.save("employee/photo.png", ContentFile(b"png"))
        default_storage.save(".generated_secret_key", ContentFile(b"key"))
        seen = {}

        def fake_upload(path, backup, folder, credentials=None):
            with zipfile.ZipFile(path) as zf:
                seen["names"] = sorted(zf.namelist())
                seen["photo"] = zf.read("employee/photo.png")
            return "id"

        with mock.patch.object(scheduler, "upload_file", side_effect=fake_upload):
            scheduler.run_gdrive_backup(self.backup)
        self.assertEqual(seen["names"], ["employee/photo.png"])
        self.assertEqual(seen["photo"], b"png")

    def test_scheduled_entry_point_skips_an_inactive_backup(self):
        with mock.patch.object(scheduler, "run_gdrive_backup") as run:
            self.assertIsNone(scheduler.scheduled_gdrive_backup())
            run.assert_not_called()
            self.backup.active = True
            self.backup.save()
            scheduler.scheduled_gdrive_backup()
            run.assert_called_once()


class ZipStorageTests(SimpleTestCase):
    def test_nested_files_keep_relative_names(self):
        storage = InMemoryStorage()
        storage.save("a/b/c.txt", ContentFile(b"c"))
        storage.save("top.txt", ContentFile(b"t"))
        storage.save("skip/me.txt", ContentFile(b"s"))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "m.zip"
            self.assertEqual(zip_storage(storage, out, exclude=("skip/",)), 2)
            with zipfile.ZipFile(out) as zf:
                self.assertEqual(sorted(zf.namelist()), ["a/b/c.txt", "top.txt"])
                self.assertGreater(zf.getinfo("top.txt").date_time[0], 1980)
                self.assertEqual(zf.read("a/b/c.txt"), b"c")


class SyncGdriveBackupJobTests(TestCase):
    """The schedule lives in the scheduler process; web only writes the row."""

    def setUp(self):
        # Started paused: jobs land in the real jobstore but never execute.
        self.scheduler = BackgroundScheduler()
        self.scheduler.start(paused=True)
        self.addCleanup(self.scheduler.shutdown, wait=False)

    def _backup(self, **fields):
        defaults = {
            "gdrive_folder_id": "f",
            "backup_db": True,
            "fixed": True,
            "hour": 2,
            "minute": 30,
            "active": True,
        }
        defaults.update(fields)
        return GoogleDriveBackup.objects.create(**defaults)

    def _job(self):
        return self.scheduler.get_job(scheduler.GDRIVE_BACKUP_JOB_ID)

    def test_no_running_scheduler_is_a_no_op(self):
        self._backup()
        with mock.patch.object(scheduler, "get_running_scheduler", return_value=None):
            scheduler.sync_gdrive_backup_job()

    def test_adds_a_cron_job_for_an_active_fixed_backup(self):
        self._backup()
        scheduler.sync_gdrive_backup_job(self.scheduler)
        job = self._job()
        self.assertIsNotNone(job)
        self.assertEqual(str(job.trigger), "cron[hour='2', minute='30']")
        self.assertIs(job.func, scheduler.scheduled_gdrive_backup)

    def test_unchanged_schedule_keeps_the_existing_job(self):
        self._backup(fixed=False, interval=True, seconds=3600, hour=None)
        scheduler.sync_gdrive_backup_job(self.scheduler)
        first = self._job()
        scheduler.sync_gdrive_backup_job(self.scheduler)
        self.assertIs(self._job(), first)

    def test_changed_schedule_replaces_the_job(self):
        backup = self._backup()
        scheduler.sync_gdrive_backup_job(self.scheduler)
        backup.hour = 5
        backup.save()
        scheduler.sync_gdrive_backup_job(self.scheduler)
        self.assertEqual(str(self._job().trigger), "cron[hour='5', minute='30']")

    def test_stopped_backup_removes_the_job(self):
        backup = self._backup()
        scheduler.sync_gdrive_backup_job(self.scheduler)
        backup.active = False
        backup.save()
        scheduler.sync_gdrive_backup_job(self.scheduler)
        self.assertIsNone(self._job())

    def test_out_of_range_form_values_still_make_a_valid_trigger(self):
        trigger = scheduler.gdrive_trigger(
            GoogleDriveBackup(fixed=True, hour=24, minute=60)
        )
        self.assertEqual(str(trigger), "cron[hour='0', minute='0']")

    def test_reconcile_job_is_registered_for_run_scheduler(self):
        ids = {job.job_id for job in get_registered_jobs()}
        self.assertIn("horilla_backup.scheduler.sync_gdrive_backup_job", ids)


class GdriveFormTests(TestCase):
    def _form(self, payload):
        data = {"gdrive_folder_id": "f", "backup_db": "on", "fixed": "on", "hour": 2}
        files = {
            "oauth_credentials_file": SimpleUploadedFile(
                "c.json", payload, content_type="application/json"
            )
        }
        return GdriveBackupSetupForm(data, files)

    def test_token_fields_are_not_editable(self):
        for name in ("access_token", "refresh_token", "token_expiry", "active"):
            self.assertNotIn(name, GdriveBackupSetupForm.base_fields)

    def test_rejects_invalid_json(self):
        form = self._form(b"not json")
        self.assertFalse(form.is_valid())
        self.assertIn("oauth_credentials_file", form.errors)


@override_settings(STORAGES=NO_PATH_STORAGE)
class GdriveViewTests(NoPathStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        user = make_user("admin", is_superuser=True)
        make_employee(
            company=Company.objects.create(company="Acme", hq=True),
            email="admin@test.horilla",
            user=user,
        )
        self.client.force_login(user)

    def _upload(self, info):
        return self.client.post(
            reverse("gdrive"),
            {
                "gdrive_folder_id": "folder",
                "backup_db": "on",
                "fixed": "on",
                "hour": "2",
                "minute": "0",
                "oauth_credentials_file": SimpleUploadedFile(
                    "c.json", json.dumps(info).encode(), "application/json"
                ),
            },
        )

    def test_setup_page_renders(self):
        response = self.client.get(reverse("gdrive"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Google Credentials File")
        self.assertNotContains(response, "id_access_token")

    def test_service_account_upload_then_start_only_flips_the_flag(self):
        self.assertEqual(self._upload(SERVICE_ACCOUNT_INFO).status_code, 302)
        backup = GoogleDriveBackup.objects.get()
        self.assertFalse(backup.active)
        with mock.patch(
            "apscheduler.schedulers.base.BaseScheduler.start"
        ) as start_scheduler:
            self.client.get(reverse("gdrive_start_stop"))
        start_scheduler.assert_not_called()
        backup.refresh_from_db()
        self.assertTrue(backup.active)

    def test_oauth_client_must_be_authorized_before_start(self):
        self._upload(OAUTH_CLIENT_INFO)
        page = self.client.get(reverse("gdrive"))
        self.assertContains(page, reverse("gdrive_authorize"))
        self.client.get(reverse("gdrive_start_stop"))
        self.assertFalse(GoogleDriveBackup.objects.get().active)

    def test_authorize_redirects_to_google_with_pkce_and_stores_state(self):
        self._upload(OAUTH_CLIENT_INFO)
        response = self.client.get(reverse("gdrive_authorize"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(OAUTH_CLIENT_INFO["web"]["auth_uri"]))
        self.assertIn("code_challenge=", response.url)
        session = self.client.session
        self.assertTrue(session["gdrive_oauth_state"])
        self.assertTrue(session["gdrive_oauth_code_verifier"])

    def test_callback_with_wrong_state_stores_nothing(self):
        self._upload(OAUTH_CLIENT_INFO)
        self.client.get(reverse("gdrive_authorize"))
        self.client.get(reverse("gdrive_callback"), {"state": "forged", "code": "x"})
        self.assertIsNone(GoogleDriveBackup.objects.get().access_token)

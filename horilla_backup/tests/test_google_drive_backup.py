"""google_drive_backup stages its files in a temp dir, not the working dir."""

import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from horilla.testkit import read_only_cwd
from horilla_backup import scheduler


class GoogleDriveBackupTests(SimpleTestCase):
    def test_dump_and_media_zip_are_staged_outside_the_working_directory(self):
        uploaded = {}

        def fake_dump(output_file, **kwargs):
            Path(output_file).write_bytes(b"dump")

        def record_upload(file_path, service_account_file, parent_folder_id):
            # Must still exist while uploading; gone once the backup returns.
            uploaded[os.path.basename(file_path)] = Path(file_path).read_bytes()
            self.assertTrue(os.path.isabs(file_path))

        backup = mock.Mock(backup_db=True, backup_media=True, gdrive_folder_id="f")
        backup.service_account_file.path = "/secrets/sa.json"
        model = mock.Mock()
        model.objects.exists.return_value = True
        model.objects.first.return_value = backup

        with tempfile.TemporaryDirectory() as media:
            Path(media, "photo.png").write_bytes(b"png")
            with (
                # scheduler reads the horilla.settings module, not django.conf.
                mock.patch.object(scheduler.settings, "MEDIA_ROOT", media),
                mock.patch.object(scheduler, "GoogleDriveBackup", model),
                mock.patch.object(scheduler, "dump_postgres_db", fake_dump),
                mock.patch.object(scheduler, "upload_file", record_upload),
                read_only_cwd(),
            ):
                scheduler.google_drive_backup()

        self.assertEqual(sorted(uploaded), ["backupdb.dump", "media.zip"])
        self.assertEqual(uploaded["backupdb.dump"], b"dump")

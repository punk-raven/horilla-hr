"""
Uploaded files when media lives in S3 or Cloudflare R2 instead of on disk.

Every link points at protected_media, which keeps its login, JWT and public
allow-list checks and then redirects to a short-lived presigned URL. Code that
embeds a file (logos in PDF and Excel exports, mail) reads it through the
storage, because FieldFile.path and MEDIA_ROOT do not exist for a bucket.
"""

import base64
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.core.files.base import ContentFile
from django.core.files.storage import (
    FileSystemStorage,
    InMemoryStorage,
    default_storage,
)
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase, override_settings

from base.demo_data.media import copy_demo_media
from base.models import Company
from horilla.horilla_backends import MediaStorage
from horilla.media import is_remote_storage, media_data_uri, read_media
from horilla.testkit import make_employee, make_user
from report.export import _pdf_link_callback

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

R2_SETTINGS = {
    "AWS_ACCESS_KEY_ID": "test-access-key",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    "AWS_STORAGE_BUCKET_NAME": "horilla-media",
    "AWS_S3_ENDPOINT_URL": "https://accountid.r2.cloudflarestorage.com",
    "AWS_S3_REGION_NAME": "auto",
    "AWS_QUERYSTRING_EXPIRE": 60,
}


def storages_with(backend):
    from django.conf import settings

    return {**settings.STORAGES, "default": {"BACKEND": backend}}


IN_MEMORY = storages_with("django.core.files.storage.InMemoryStorage")
R2 = storages_with("horilla.horilla_backends.MediaStorage")


class ReadMediaTests(SimpleTestCase):
    """One read path for local disk and remote storage."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root)

    def assert_reads_through(self, storage):
        name = storage.save("base/company/icon/logo.png", ContentFile(PNG))
        field_file = mock.Mock(storage=storage)
        field_file.name = name

        self.assertEqual(read_media(field_file), PNG)
        self.assertEqual(
            media_data_uri(field_file),
            "data:image/png;base64," + base64.b64encode(PNG).decode(),
        )

    def test_local_disk(self):
        storage = FileSystemStorage(location=self.media_root)
        self.assertFalse(is_remote_storage(storage))
        self.assert_reads_through(storage)

    def test_remote_storage(self):
        storage = InMemoryStorage()
        self.assertTrue(is_remote_storage(storage))
        self.assert_reads_through(storage)

    @override_settings(STORAGES=IN_MEMORY)
    def test_a_name_reads_from_the_default_storage(self):
        default_storage.save("employee/profile/a.jpg", ContentFile(b"jpeg"))

        self.assertTrue(is_remote_storage())
        self.assertEqual(read_media("employee/profile/a.jpg"), b"jpeg")

    def test_missing_or_empty_files_read_as_none(self):
        storage = FileSystemStorage(location=self.media_root)
        missing = mock.Mock(storage=storage)
        missing.name = "base/icon/missing.png"

        self.assertIsNone(read_media(missing))
        self.assertIsNone(media_data_uri(missing))
        self.assertIsNone(read_media(""))
        self.assertIsNone(read_media(None))

    def test_hidden_paths_are_never_read(self):
        with open(os.path.join(self.media_root, ".generated_secret_key"), "w") as fh:
            fh.write("super-secret")
        with override_settings(MEDIA_ROOT=self.media_root):
            self.assertIsNone(read_media(".generated_secret_key"))
            # Left unresolved, so xhtml2pdf skips it instead of embedding it.
            self.assertEqual(
                _pdf_link_callback("/media/.generated_secret_key", ""),
                "/media/.generated_secret_key",
            )

    @override_settings(STORAGES=IN_MEMORY)
    def test_pdf_link_callback_embeds_media_from_storage(self):
        default_storage.save("base/company/icon/acme logo.png", ContentFile(PNG))

        uri = _pdf_link_callback("/media/base/company/icon/acme%20logo.png", "")

        self.assertTrue(uri.startswith("data:image/png;base64,"))


@override_settings(**R2_SETTINGS)
class MediaStorageUrlTests(SimpleTestCase):
    def test_url_points_at_protected_media(self):
        storage = MediaStorage()

        self.assertEqual(
            storage.url("recruitment/resume/cv 2026.pdf"),
            "/media/recruitment/resume/cv%202026.pdf",
        )

    def test_signed_url_overrides_the_response_headers(self):
        url = urlparse(
            MediaStorage().signed_url(
                "upload/page.html",
                content_type="text/html",
                content_disposition='attachment; filename="page.html"',
            )
        )
        query = parse_qs(url.query)

        self.assertEqual(url.hostname, "accountid.r2.cloudflarestorage.com")
        self.assertIn("X-Amz-Signature", query)
        self.assertEqual(query["X-Amz-Expires"], ["60"])
        self.assertEqual(query["response-content-type"], ["text/html"])
        self.assertEqual(
            query["response-content-disposition"],
            ['attachment; filename="page.html"'],
        )

    @override_settings(STORAGES=R2)
    def test_field_files_and_plain_names_link_to_protected_media(self):
        from django.db.models.fields.files import FieldFile

        from recruitment.models import Candidate

        resume = FieldFile(
            None, Candidate._meta.get_field("resume"), "recruitment/resume/cv.pdf"
        )
        rendered = Template(
            "{% load horillafilters %}{{ name|media_url }}|{{ empty|media_url }}"
        ).render(Context({"name": "recruitment_attachment/a b.pdf", "empty": ""}))

        self.assertEqual(resume.url, "/media/recruitment/resume/cv.pdf")
        self.assertEqual(rendered, "/media/recruitment_attachment/a%20b.pdf|")


class ProtectedMediaRemoteTests(TestCase):
    """protected_media with the bucket as the default storage."""

    def setUp(self):
        company = Company.objects.create(company="Acme", hq=True)
        user = make_user("emp", password="pw-not-real")
        make_employee(company=company, email="emp@test.horilla", user=user)
        self.user = user

        settings_override = override_settings(**R2_SETTINGS, STORAGES=R2)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.exists = mock.patch.object(
            MediaStorage, "exists", return_value=True
        ).start()
        self.addCleanup(mock.patch.stopall)

    def location(self, response):
        url = urlparse(response["Location"])
        return url, parse_qs(url.query)

    def test_inline_types_redirect_to_a_presigned_url(self):
        self.client.force_login(self.user)

        response = self.client.get("/media/employee/profile/photo.png")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        url, query = self.location(response)
        self.assertEqual(url.hostname, "accountid.r2.cloudflarestorage.com")
        self.assertTrue(url.path.endswith("/employee/profile/photo.png"))
        self.assertEqual(query["X-Amz-Expires"], ["60"])
        self.assertEqual(query["response-content-type"], ["image/png"])
        self.assertNotIn("response-content-disposition", query)
        self.exists.assert_called_once_with("employee/profile/photo.png")

    def test_active_content_is_still_forced_to_download(self):
        self.client.force_login(self.user)

        for path, content_type in (
            ("upload/page.html", "text/html"),
            ("upload/image.svg", "image/svg+xml"),
            # text/xml or application/xml, depending on the host's mime.types.
            ("upload/feed.xml", mimetypes.guess_type("feed.xml")[0]),
            ("upload/no-extension", "application/octet-stream"),
        ):
            with self.subTest(path=path):
                response = self.client.get(f"/media/{path}")

                self.assertEqual(response.status_code, 302)
                _url, query = self.location(response)
                self.assertEqual(query["response-content-type"], [content_type])
                filename = os.path.basename(path)
                self.assertEqual(
                    query["response-content-disposition"],
                    [f'attachment; filename="{filename}"'],
                )

    def test_anonymous_users_are_sent_to_login_not_to_the_bucket(self):
        response = self.client.get("/media/employee/profile/photo.png")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login"))

    def test_public_assets_redirect_without_login(self):
        response = self.client.get("/media/base/company/icon/logo.png")

        self.assertEqual(response.status_code, 302)
        url, _query = self.location(response)
        self.assertEqual(url.hostname, "accountid.r2.cloudflarestorage.com")

    def test_missing_objects_are_404(self):
        self.client.force_login(self.user)
        self.exists.return_value = False

        response = self.client.get("/media/employee/profile/gone.png")

        self.assertEqual(response.status_code, 404)

    def test_hidden_and_traversal_paths_never_reach_the_bucket(self):
        self.client.force_login(self.user)

        for path in (".generated_secret_key", "a/.hidden/x.txt", "../etc/passwd"):
            with self.subTest(path=path):
                response = self.client.get(f"/media/{path}")
                self.assertEqual(response.status_code, 404)
        self.exists.assert_not_called()

    def test_page_script_fetches_are_streamed_with_the_same_headers(self):
        """fetch() would need a CORS policy on the bucket to follow a redirect."""
        self.client.force_login(self.user)
        files = {
            "base/company/icon/logo.png": PNG,
            "upload/page.html": b"<script>alert(1)</script>",
        }
        mock.patch.object(
            MediaStorage,
            "_open",
            side_effect=lambda name, mode="rb": ContentFile(files[name], name=name),
        ).start()

        logo = self.client.get(
            "/media/base/company/icon/logo.png", headers={"Sec-Fetch-Mode": "cors"}
        )
        page = self.client.get(
            "/media/upload/page.html", headers={"Sec-Fetch-Mode": "cors"}
        )

        self.assertEqual(logo.status_code, 200)
        self.assertEqual(b"".join(logo.streaming_content), PNG)
        self.assertEqual(logo["Content-Type"], "image/png")
        self.assertEqual(logo["X-Content-Type-Options"], "nosniff")
        self.assertEqual(page["Content-Type"], "text/html")
        self.assertEqual(
            page["Content-Disposition"], 'attachment; filename="page.html"'
        )


class ProtectedMediaLocalTests(TestCase):
    """Local disk is served from MEDIA_ROOT exactly as before."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root)
        os.makedirs(os.path.join(self.media_root, "upload"))
        for name, content in (("photo.png", PNG), ("page.html", b"<p>hi</p>")):
            with open(os.path.join(self.media_root, "upload", name), "wb") as fh:
                fh.write(content)
        company = Company.objects.create(company="Acme", hq=True)
        user = make_user("emp", password="pw-not-real")
        make_employee(company=company, email="emp@test.horilla", user=user)
        self.client.force_login(user)

    def test_files_are_served_not_redirected(self):
        with override_settings(MEDIA_ROOT=self.media_root):
            photo = self.client.get("/media/upload/photo.png")
            page = self.client.get("/media/upload/page.html")
            missing = self.client.get("/media/upload/missing.png")

        self.assertEqual(photo.status_code, 200)
        self.assertEqual(b"".join(photo.streaming_content), PNG)
        self.assertEqual(photo["Content-Type"], "image/png")
        self.assertEqual(photo["X-Content-Type-Options"], "nosniff")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["Content-Type"], "text/html")
        self.assertEqual(
            page["Content-Disposition"], 'attachment; filename="page.html"'
        )
        self.assertEqual(missing.status_code, 404)


@override_settings(STORAGES=IN_MEMORY)
class DemoMediaTests(SimpleTestCase):
    """Demo avatars and icons must reach the bucket, not only MEDIA_ROOT."""

    def setUp(self):
        self.load_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.load_dir)
        (self.load_dir / "icons").mkdir()
        (self.load_dir / "avatars").mkdir()
        (self.load_dir / "icons" / "Horilla_1.png").write_bytes(PNG)
        (self.load_dir / "avatars" / "avatar_ada.jpg").write_bytes(b"jpeg")

    def test_copies_into_the_default_storage_under_the_fixture_names(self):
        self.assertEqual(copy_demo_media(self.load_dir), {"icons": 1, "avatars": 1})
        self.assertEqual(read_media("base/icon/Horilla_1.png"), PNG)
        self.assertEqual(read_media("employee/profile/avatar_ada.jpg"), b"jpeg")

    def test_reloading_replaces_rather_than_renames(self):
        copy_demo_media(self.load_dir)
        (self.load_dir / "avatars" / "avatar_ada.jpg").write_bytes(b"new")

        copy_demo_media(self.load_dir)

        self.assertEqual(read_media("employee/profile/avatar_ada.jpg"), b"new")
        _dirs, files = default_storage.listdir("employee/profile")
        self.assertEqual(files, ["avatar_ada.jpg"])

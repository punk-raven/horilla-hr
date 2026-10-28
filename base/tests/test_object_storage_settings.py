"""
S3-compatible media storage settings (horilla/settings/addons.py).

The S3 branch used to be unusable: boto3 was not installed, the endpoint
could not be pointed anywhere but AWS, and six variables with no default
raised ImproperlyConfigured at boot. These tests load the module the way a
Cloudflare R2 deployment configures it.
"""

import importlib
import os
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase, override_settings

from horilla.settings import base

R2_ENV = {
    "AWS_ACCESS_KEY_ID": "test-access-key",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    "AWS_STORAGE_BUCKET_NAME": "horilla-media",
    "AWS_S3_ENDPOINT_URL": "https://accountid.r2.cloudflarestorage.com",
    "AWS_S3_REGION_NAME": "auto",
}

# Variables the settings module reads; cleared so the host environment of
# whoever runs the suite cannot leak into the assertions.
STORAGE_VARS = (
    *R2_ENV,
    "AWS_LOCATION",
    "AWS_QUERYSTRING_EXPIRE",
    "AWS_S3_ADDRESSING_STYLE",
    "DEFAULT_FILE_STORAGE",
    "MEDIA_URL",
    "MEDIA_ROOT",
    "NAMESPACE",
)


def load_addons(**env):
    clean = {k: v for k, v in os.environ.items() if k not in STORAGE_VARS}
    with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
        from horilla.settings import addons

        return importlib.reload(addons)


class ObjectStorageSettingsTests(SimpleTestCase):
    def setUp(self):
        # addons appends "storages" to the shared INSTALLED_APPS list in place.
        self._installed_apps = list(base.INSTALLED_APPS)
        self.addCleanup(self._restore)

    def _restore(self):
        base.INSTALLED_APPS[:] = self._installed_apps
        load_addons()

    def test_r2_needs_only_credentials_bucket_and_endpoint(self):
        addons = load_addons(**R2_ENV)

        self.assertEqual(
            addons.STORAGES["default"]["BACKEND"],
            "storages.backends.s3boto3.S3Boto3Storage",
        )
        self.assertEqual(
            addons.AWS_S3_ENDPOINT_URL, "https://accountid.r2.cloudflarestorage.com"
        )
        self.assertEqual(addons.AWS_S3_REGION_NAME, "auto")
        self.assertEqual(addons.AWS_LOCATION, "")
        self.assertEqual(addons.AWS_QUERYSTRING_EXPIRE, 3600)
        self.assertIn("storages", addons.INSTALLED_APPS)
        # Static files stay on WhiteNoise; only media moves to the bucket.
        self.assertEqual(addons.STORAGES["staticfiles"], base.STORAGES["staticfiles"])
        self.assertEqual(addons.MEDIA_URL, base.MEDIA_URL)

    def test_location_and_expiry_are_configurable(self):
        addons = load_addons(
            **R2_ENV, AWS_LOCATION="media", AWS_QUERYSTRING_EXPIRE="600"
        )

        self.assertEqual(addons.AWS_LOCATION, "media")
        self.assertEqual(addons.AWS_QUERYSTRING_EXPIRE, 600)

    def test_legacy_namespaced_media_paths_still_apply(self):
        addons = load_addons(
            **R2_ENV,
            MEDIA_URL="https://cdn.example.com",
            MEDIA_ROOT="/srv/media",
            NAMESPACE="acme",
        )

        self.assertEqual(addons.MEDIA_URL, "https://cdn.example.com/acme/")
        self.assertEqual(addons.MEDIA_ROOT, "/srv/media/acme/")


class R2PresignedUrlTests(SimpleTestCase):
    """The configured backend signs URLs against the R2 endpoint, not AWS."""

    @override_settings(
        AWS_ACCESS_KEY_ID=R2_ENV["AWS_ACCESS_KEY_ID"],
        AWS_SECRET_ACCESS_KEY=R2_ENV["AWS_SECRET_ACCESS_KEY"],
        AWS_STORAGE_BUCKET_NAME=R2_ENV["AWS_STORAGE_BUCKET_NAME"],
        AWS_S3_ENDPOINT_URL=R2_ENV["AWS_S3_ENDPOINT_URL"],
        AWS_S3_REGION_NAME="auto",
        AWS_LOCATION="media",
        AWS_QUERYSTRING_EXPIRE=600,
    )
    def test_url_is_presigned_for_the_r2_endpoint(self):
        from storages.backends.s3boto3 import S3Boto3Storage

        url = urlparse(S3Boto3Storage().url("employee/photo.png"))
        query = parse_qs(url.query)

        self.assertEqual(url.hostname, "accountid.r2.cloudflarestorage.com")
        self.assertIn("/media/employee/photo.png", url.path)
        self.assertIn("X-Amz-Signature", query)
        self.assertEqual(query["X-Amz-Expires"], ["600"])

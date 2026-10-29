"""Every process of one install must sign with the same SECRET_KEY.

The Docker entrypoint replaces a missing or public default SECRET_KEY with a
random key persisted in the media volume. It used to `export` that key, which
only reached the entrypoint's own child (gunicorn, or run_scheduler). A
`docker exec ... manage.py shell` starts from the container's configured
environment instead, so it signed with the compose placeholder: password reset
links made there showed "Link Expired" in the app, and no signed cookie or
token crossed between the two. Settings now read the persisted key themselves.
"""

import hashlib
import os
import subprocess
import sys
import tempfile

from django.conf import settings
from django.test import SimpleTestCase

from horilla.settings.security import is_insecure_secret_key, resolve_secret_key

REAL_KEY = "x7#kq2!r9v$m4p0w8z@t6y1n3b5c%e&f*h(j)l_s+u-a=d^g~i"
GENERATED_KEY = "persisted-by-the-entrypoint-0123456789abcdefghijklmnop"


class ResolveSecretKeyTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.key_file = os.path.join(tmp.name, ".generated_secret_key")

    def write_key_file(self, content):
        with open(self.key_file, "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_placeholder_resolves_to_the_persisted_key(self):
        self.write_key_file(GENERATED_KEY + "\n")
        for placeholder in (
            "",
            "dev-secret-key-change-in-production",
            "django-insecure-default-key",
            "change-me-to-a-long-random-value",
        ):
            with self.subTest(placeholder=placeholder):
                self.assertEqual(
                    resolve_secret_key(placeholder, self.key_file), GENERATED_KEY
                )

    def test_real_key_wins_over_the_persisted_key(self):
        self.write_key_file(GENERATED_KEY)
        self.assertEqual(resolve_secret_key(REAL_KEY, self.key_file), REAL_KEY)

    def test_without_a_persisted_key_the_placeholder_is_kept(self):
        # Left unchanged so validate_production_secrets() still refuses it.
        placeholder = "dev-secret-key-change-in-production"
        self.assertEqual(resolve_secret_key(placeholder, self.key_file), placeholder)
        self.write_key_file("  \n")
        self.assertEqual(resolve_secret_key(placeholder, self.key_file), placeholder)

    def test_insecure_key_detection(self):
        self.assertTrue(is_insecure_secret_key(None))
        self.assertTrue(is_insecure_secret_key("django-insecure-abc"))
        self.assertFalse(is_insecure_secret_key(REAL_KEY))


class SeparateProcessesShareTheKeyTests(SimpleTestCase):
    """A fresh process, like `docker exec manage.py`, resolves the same key."""

    def settings_key_hash(self, env):
        code = (
            "import hashlib, django\n"
            "django.setup()\n"
            "from django.conf import settings\n"
            "print(hashlib.sha256(settings.SECRET_KEY.encode()).hexdigest())\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return result.stdout.strip().splitlines()[-1]

    def test_placeholder_env_signs_with_the_persisted_key(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        key_file = os.path.join(tmp.name, ".generated_secret_key")
        with open(key_file, "w", encoding="utf-8") as fh:
            fh.write(GENERATED_KEY)

        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "horilla.settings",
            "DEBUG": "1",
            "SECRET_KEY": "dev-secret-key-change-in-production",
            "SECRET_KEY_FILE": key_file,
        }
        self.assertEqual(
            self.settings_key_hash(env),
            hashlib.sha256(GENERATED_KEY.encode()).hexdigest(),
        )

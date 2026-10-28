"""
Optional integrations (e.g. AWS S3) layered on top of base settings.

Imported from horilla.settings.__init__ after base.py. Client overrides belong
in local_settings.py (imported after this module) — do not import them here.
"""

from .base import INSTALLED_APPS, MEDIA_ROOT, MEDIA_URL, STORAGES, env

if env("AWS_ACCESS_KEY_ID", default=None):
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    # None lets boto3 resolve the region itself; Cloudflare R2 wants "auto".
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default=None)
    # Any S3-compatible store (Cloudflare R2, MinIO). Unset means AWS itself;
    # without it boto3 sent R2 credentials to amazonaws.com.
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)
    # Key prefix inside the bucket. Empty is django-storages' own default, so
    # buckets already written by S3Boto3Storage keep resolving the same keys.
    AWS_LOCATION = env("AWS_LOCATION", default="")
    # Lifetime of the presigned URLs files are served through. Anyone holding
    # one can fetch the file until it expires, so keep it short.
    AWS_QUERYSTRING_EXPIRE = env.int("AWS_QUERYSTRING_EXPIRE", default=3600)
    AWS_S3_ADDRESSING_STYLE = env("AWS_S3_ADDRESSING_STYLE", default=None)
    # DEFAULT_FILE_STORAGE was removed in Django 5.1 and this project pins
    # 5.2, so setting it alone is silently ignored -- uploads kept going to
    # local disk while the bucket stayed empty. STORAGES["default"] is the
    # replacement and is what FileField actually reads.
    #
    # S3Boto3Storage, not horilla_backends.PrivateMediaStorage, by default:
    # that class sends an object ACL, which R2 does not implement.
    _default_storage_backend = env(
        "DEFAULT_FILE_STORAGE", default="storages.backends.s3boto3.S3Boto3Storage"
    )
    DEFAULT_FILE_STORAGE = _default_storage_backend
    STORAGES = {
        **STORAGES,
        "default": {"BACKEND": _default_storage_backend},
    }

if env("AWS_ACCESS_KEY_ID", default=None) and "storages" not in INSTALLED_APPS:
    INSTALLED_APPS.append("storages")

# Legacy namespaced layout. Only applied when all three are set: they used to
# be required, and a missing one raised ImproperlyConfigured at boot.
if (
    env("AWS_ACCESS_KEY_ID", default=None)
    and env("MEDIA_URL", default=None)
    and env("MEDIA_ROOT", default=None)
    and env("NAMESPACE", default=None)
):
    MEDIA_URL = f"{env('MEDIA_URL')}/{env('NAMESPACE')}/"
    MEDIA_ROOT = f"{env('MEDIA_ROOT')}/{env('NAMESPACE')}/"

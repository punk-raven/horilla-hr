"""
horilla/horilla_backends.py

S3-compatible media storage (AWS S3, Cloudflare R2).
"""

from django.urls import reverse
from storages.backends.s3boto3 import S3Boto3Storage
from storages.utils import clean_name

from horilla import settings


class MediaStorage(S3Boto3Storage):
    """
    Keep the bucket private and serve every file through protected_media.

    url() returns the app's own /media/<name> link instead of a bucket URL, so
    each request passes the login, JWT and public allow-list checks, and only
    then is redirected to a short-lived presigned URL (signed_url()).
    """

    # Bucket URLs must be signed: an unsigned custom domain would make every
    # file public to anyone who guesses its name.
    custom_domain = False
    querystring_auth = True

    def url(self, name, parameters=None, expire=None, http_method=None):
        return reverse("protected_media", kwargs={"path": clean_name(name)})

    def signed_url(self, name, content_type=None, content_disposition=None):
        """
        Presigned GET URL for ``name``, valid for AWS_QUERYSTRING_EXPIRE
        seconds. The response headers are overridden in the signature, so the
        bucket sends the content type and disposition protected_media chose,
        not whatever was stored with the object.
        """
        parameters = {}
        if content_type:
            parameters["ResponseContentType"] = content_type
        if content_disposition:
            parameters["ResponseContentDisposition"] = content_disposition
        return super().url(name, parameters=parameters)


class PrivateMediaStorage(MediaStorage):
    """
    MediaStorage under the NAMESPACE key prefix, with a private object ACL.

    Cloudflare R2 does not implement object ACLs; use MediaStorage there.
    """

    location = settings.env("NAMESPACE", default="private")
    default_acl = "private"

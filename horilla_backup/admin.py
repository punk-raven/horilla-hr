from django.conf import settings
from django.contrib import admin

from .models import GoogleDriveBackup, LocalBackup


def register_models(site):
    # LocalBackup writes to a path on the server's own disk. Where that disk is
    # read-only or ephemeral (Railway), LOCAL_BACKUPS_ENABLED=False hides it so
    # nobody configures a backup that cannot succeed.
    if settings.LOCAL_BACKUPS_ENABLED:
        site.register(LocalBackup)
    site.register(GoogleDriveBackup)


register_models(admin.site)

"""
Google Drive access for the backup job.

Two kinds of credentials file are accepted in GoogleDriveBackup's upload field:

- A service-account key (its ``type`` is SERVICE_ACCOUNT). Needs no browser
  consent, so it suits a headless scheduler, but a service account has no
  Drive storage of its own: the target folder must be in a shared drive the
  service account is a member of.
- An OAuth 2.0 web-client file (``"web": {...}``). An admin authorizes once
  through gdrive_authorize/gdrive_callback, and the tokens are kept on the
  model and refreshed here.

The file is always read through its storage backend, never ``.path``: media
lives in S3/R2 on cloud deployments, where there is no local path.
"""

import json
import logging
import os
from datetime import timedelta
from datetime import timezone as dt_timezone

from django.utils import timezone
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]

SERVICE_ACCOUNT = "service_account"
OAUTH = "oauth"


def read_credentials_info(field_file):
    """Parse the uploaded credentials JSON through its storage backend."""
    field_file.open("rb")
    try:
        return json.loads(field_file.read())
    finally:
        field_file.close()


def credentials_kind(info):
    """Return SERVICE_ACCOUNT or OAUTH, raising ValueError for anything else."""
    if not isinstance(info, dict):
        raise ValueError("Expected a JSON object.")
    if info.get("type") == SERVICE_ACCOUNT:
        missing = [
            k for k in ("client_email", "private_key", "token_uri") if k not in info
        ]
        if missing:
            raise ValueError(f"Service account key is missing: {', '.join(missing)}")
        return SERVICE_ACCOUNT
    if "web" in info:
        missing = [
            k
            for k in ("client_id", "client_secret", "auth_uri", "token_uri")
            if k not in info["web"]
        ]
        if missing:
            raise ValueError(f"OAuth client file is missing: {', '.join(missing)}")
        return OAUTH
    raise ValueError(
        "Expected a service account key or an OAuth 2.0 web application client file."
    )


def needs_authorization(google_drive_backup):
    """True when an OAuth client file is uploaded but not yet authorized."""
    if not google_drive_backup.oauth_credentials_file:
        return False
    try:
        info = read_credentials_info(google_drive_backup.oauth_credentials_file)
        return credentials_kind(info) == OAUTH and not google_drive_backup.access_token
    except Exception:
        logger.exception("Could not read the Google Drive credentials file")
        return False


def as_aware(value):
    if value is not None and timezone.is_naive(value):
        return timezone.make_aware(value, dt_timezone.utc)
    return value


def _oauth_credentials(google_drive_backup, info):
    if not google_drive_backup.access_token:
        raise RuntimeError(
            "Google Drive is not authorized yet. Use 'Authorize Google Drive' in "
            "the backup settings."
        )
    web = info["web"]
    creds = Credentials(
        token=google_drive_backup.access_token,
        refresh_token=google_drive_backup.refresh_token,
        token_uri=web["token_uri"],
        client_id=web["client_id"],
        client_secret=web["client_secret"],
        scopes=SCOPES,
    )
    expiry = as_aware(google_drive_backup.token_expiry)
    if expiry is None or timezone.now() >= expiry - timedelta(minutes=5):
        if not creds.refresh_token:
            raise RuntimeError(
                "The Google Drive access token expired and no refresh token is "
                "stored. Authorize Google Drive again."
            )
        creds.refresh(Request())
        google_drive_backup.access_token = creds.token
        if creds.refresh_token:
            google_drive_backup.refresh_token = creds.refresh_token
        google_drive_backup.token_expiry = as_aware(creds.expiry)
        google_drive_backup.save()
    return creds


def get_credentials(google_drive_backup):
    """Credentials for the Drive API from whichever file type was uploaded."""
    if not google_drive_backup.oauth_credentials_file:
        raise RuntimeError("No Google credentials file is uploaded.")
    info = read_credentials_info(google_drive_backup.oauth_credentials_file)
    if credentials_kind(info) == SERVICE_ACCOUNT:
        return service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    return _oauth_credentials(google_drive_backup, info)


def build_oauth_flow(client_config, redirect_uri, state=None, code_verifier=None):
    """Flow for the web-client consent round trip. PKCE is on by default in
    google-auth-oauthlib, so the callback must reuse the same code_verifier."""
    return Flow.from_client_config(
        client_config,
        SCOPES,
        redirect_uri=redirect_uri,
        state=state,
        code_verifier=code_verifier,
    )


def upload_file(file_path, google_drive_backup, parent_folder_id, credentials=None):
    """Upload ``file_path`` into the Drive folder and return the new file id."""
    creds = credentials or get_credentials(google_drive_backup)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    file_metadata = {"name": os.path.basename(file_path), "parents": [parent_folder_id]}
    media = MediaFileUpload(
        file_path, mimetype="application/octet-stream", resumable=True
    )
    created = (
        service.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id",
            # Required for folders in shared drives, which is the only place
            # a service account can write. Harmless for a My Drive folder.
            supportsAllDrives=True,
        )
        .execute()
    )
    file_id = created.get("id")
    logger.info(
        "Uploaded %s (%d bytes) to Google Drive as %s",
        os.path.basename(file_path),
        os.path.getsize(file_path),
        file_id,
    )
    return file_id

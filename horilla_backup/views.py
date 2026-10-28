import logging
import os

from django.conf import settings
from django.contrib import messages
from django.db import connection
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required

from .forms import GdriveBackupSetupForm
from .gdrive import (
    OAUTH,
    as_aware,
    build_oauth_flow,
    credentials_kind,
    needs_authorization,
    read_credentials_info,
)
from .models import GoogleDriveBackup

logger = logging.getLogger(__name__)

OAUTH_SESSION_KEYS = (
    "gdrive_oauth_state",
    "gdrive_oauth_code_verifier",
    "gdrive_oauth_redirect_uri",
)

# @login_required
# @permission_required("horilla_backup.add_localbackup")
# def local_setup(request):
#     """
#     function used to setup local backup.

#     Parameters:
#     request (HttpRequest): The HTTP request object.

#     Returns:
#     GET : return local backup setup template
#     POST : return settings
#     """
#     form = LocalBackupSetupForm()
#     show = False
#     active = False
#     if LocalBackup.objects.exists():
#         form = LocalBackupSetupForm(instance=LocalBackup.objects.first())
#         show = True
#         active = LocalBackup.objects.first().active
#     if request.method == "POST":
#         form = LocalBackupSetupForm(request.POST, request.FILES)
#         if form.is_valid():
#             form.save()
#             stop_backup_job()
#             messages.success(request, _("Local backup automation setup updated."))
#             return redirect("local")
#     return render(request, "backup/local_setup_form.html", {"form": form, "show":show, "active":active})


# @login_required
# @permission_required("horilla_backup.change_localbackup")
# def local_Backup_stop_or_start(request):
#     """
#     function used to stop or start local backup.

#     Parameters:
#     request (HttpRequest): The HTTP request object.

#     Returns:
#     GET : return local backup setup template
#     POST : return settings
#     """
#     if LocalBackup.objects.exists():
#         local_backup = LocalBackup.objects.first()
#         if local_backup.active == True:
#             local_backup.active = False
#             stop_backup_job()
#             message = "Local Backup Automation Stopped Successfully."
#         else:
#             local_backup.active = True
#             start_backup_job()
#             message = "Local Backup Automation Started Successfully."
#         local_backup.save()
#         messages.success(request, _(message))
#     return redirect("local")


# @login_required
# @permission_required("horilla_backup.delete_localbackup")
# def local_Backup_delete(request):
#     """
#     function used to delete local backup.

#     Parameters:
#     request (HttpRequest): The HTTP request object.

#     Returns:
#     GET : return local backup setup template
#     POST : return settings
#     """
#     if LocalBackup.objects.exists():
#         local_backup = LocalBackup.objects.first()
#         local_backup.delete()
#         stop_backup_job()
#         messages.success(request, _("Local Backup Automation Removed Successfully."))
#     return redirect("local")


def _render_setup(request, form, instance):
    return render(
        request,
        "backup/gdrive_setup_form.html",
        {
            "form": form,
            "show": instance is not None,
            "active": bool(instance and instance.active),
            "needs_auth": bool(instance and needs_authorization(instance)),
        },
    )


@login_required
@permission_required("horilla_backup.add_localbackup")
def gdrive_setup(request):
    """
    function used to setup gdrive backup.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return gdrive backup setup template
    POST : return gdrive backup update template
    """
    if connection.vendor != "postgresql":
        return render(request, "backup/404.html")
    instance = GoogleDriveBackup.objects.first()
    form = GdriveBackupSetupForm(instance=instance)
    if request.method == "POST":
        form = GdriveBackupSetupForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            google_drive = form.save()
            if "oauth_credentials_file" in request.FILES:
                # Tokens belong to the previous client; a new file starts over.
                google_drive.access_token = None
                google_drive.refresh_token = None
                google_drive.token_expiry = None
            if instance is not None:
                # An edited schedule is not live until started again.
                google_drive.active = False
            google_drive.save()
            if needs_authorization(google_drive):
                messages.success(
                    request,
                    _("Credentials saved. Authorize Google Drive access to continue."),
                )
            elif instance is not None:
                messages.success(request, _("gdrive backup automation setup updated."))
            else:
                messages.success(request, _("gdrive backup automation setup Created."))
            return redirect("gdrive")
    return _render_setup(request, form, instance)


@login_required
@permission_required("horilla_backup.change_localbackup")
def gdrive_Backup_stop_or_start(request):
    """
    function used to stop or start gdrive backup.

    Only the row's ``active`` flag changes here. The scheduler container picks
    it up within a minute (horilla_backup.scheduler.sync_gdrive_backup_job);
    no web worker runs a scheduler.
    """
    gdrive_backup = GoogleDriveBackup.objects.first()
    if gdrive_backup is not None:
        if gdrive_backup.active:
            gdrive_backup.active = False
            message = _("Google Drive Backup Automation Stopped Successfully.")
        elif needs_authorization(gdrive_backup):
            messages.error(request, _("Authorize Google Drive access first."))
            return redirect("gdrive")
        else:
            gdrive_backup.active = True
            message = _("Google Drive Backup Automation Started Successfully.")
        gdrive_backup.save()
        messages.success(request, message)
    return redirect("gdrive")


@login_required
@permission_required("horilla_backup.delete_localbackup")
def gdrive_Backup_delete(request):
    """
    function used to delete gdrive backup.

        Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return gdrive backup setup template
    """
    gdrive_backup = GoogleDriveBackup.objects.first()
    if gdrive_backup is not None:
        gdrive_backup.delete()
        messages.success(
            request, _("Google Drive Backup Automation Removed Successfully.")
        )
    return redirect("gdrive")


def _allow_http_oauth(request):
    # oauthlib refuses a plain-http redirect. Only a DEBUG install on
    # localhost legitimately has one; production sits behind TLS and
    # SECURE_PROXY_SSL_HEADER makes the callback URL https.
    if settings.DEBUG and not request.is_secure():
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


@login_required
@permission_required("horilla_backup.add_localbackup")
def gdrive_authorize(request):
    """Send the admin to Google's consent screen for an OAuth client file."""
    google_drive = GoogleDriveBackup.objects.first()
    if google_drive is None or not google_drive.oauth_credentials_file:
        messages.error(request, _("Upload a Google credentials file first."))
        return redirect("gdrive")
    try:
        client_config = read_credentials_info(google_drive.oauth_credentials_file)
        if credentials_kind(client_config) != OAUTH:
            messages.info(request, _("A service account needs no authorization."))
            return redirect("gdrive")
        _allow_http_oauth(request)
        redirect_uri = request.build_absolute_uri(reverse("gdrive_callback"))
        flow = build_oauth_flow(client_config, redirect_uri)
        # prompt=consent: Google only returns a refresh token on consent, and
        # the scheduler cannot work without one.
        authorization_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )
    except Exception:
        logger.exception("Could not start Google Drive authorization")
        messages.error(request, _("Could not start Google Drive authorization."))
        return redirect("gdrive")
    request.session["gdrive_oauth_state"] = state
    request.session["gdrive_oauth_code_verifier"] = flow.code_verifier
    request.session["gdrive_oauth_redirect_uri"] = redirect_uri
    return redirect(authorization_url)


@login_required
@permission_required("horilla_backup.add_localbackup")
def gdrive_callback(request):
    """Exchange Google's authorization code for tokens and store them."""
    google_drive = GoogleDriveBackup.objects.first()
    session_values = {key: request.session.pop(key, None) for key in OAUTH_SESSION_KEYS}
    if google_drive is None or not google_drive.oauth_credentials_file:
        messages.error(request, _("Google Drive backup is not configured."))
        return redirect("gdrive")
    if "error" in request.GET:
        messages.error(request, _("Google Drive authorization was declined."))
        return redirect("gdrive")
    if not session_values["gdrive_oauth_state"] or (
        request.GET.get("state") != session_values["gdrive_oauth_state"]
    ):
        messages.error(request, _("Authorization expired. Please try again."))
        return redirect("gdrive")
    try:
        client_config = read_credentials_info(google_drive.oauth_credentials_file)
        _allow_http_oauth(request)
        flow = build_oauth_flow(
            client_config,
            session_values["gdrive_oauth_redirect_uri"],
            state=session_values["gdrive_oauth_state"],
            code_verifier=session_values["gdrive_oauth_code_verifier"],
        )
        flow.fetch_token(authorization_response=request.build_absolute_uri())
        creds = flow.credentials
    except Exception:
        logger.exception("Google Drive authorization callback failed")
        messages.error(request, _("Google Drive authorization failed."))
        return redirect("gdrive")
    google_drive.access_token = creds.token
    if creds.refresh_token:
        google_drive.refresh_token = creds.refresh_token
    google_drive.token_expiry = as_aware(creds.expiry)
    google_drive.save()
    messages.success(request, _("Google Drive authorization successful!"))
    return redirect("gdrive")

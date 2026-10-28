import json
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from horilla_backup.gdrive import credentials_kind

from .models import GoogleDriveBackup, LocalBackup


class LocalBackupSetupForm(ModelForm):
    backup_db = forms.BooleanField(
        required=False, help_text=_("Enable to backup database to server.")
    )
    backup_media = forms.BooleanField(
        required=False, help_text=_("Enable to backup all media files to server.")
    )
    interval = forms.BooleanField(
        required=False,
        help_text=_("Enable to automate the backup in a period of seconds."),
    )
    fixed = forms.BooleanField(
        required=False, help_text=_("Enable to automate the backup in a fixed time.")
    )

    class Meta:
        model = LocalBackup
        exclude = ["active"]

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def clean(self):
        cleaned_data = super().clean()
        backup_db = cleaned_data.get("backup_db")
        backup_media = cleaned_data.get("backup_media")
        interval = cleaned_data.get("interval")
        fixed = cleaned_data.get("fixed")
        seconds = cleaned_data.get("seconds")
        hour = cleaned_data.get("hour")
        minute = cleaned_data.get("minute")
        backup_path = cleaned_data.get("backup_path")
        path = Path(backup_path)
        if not path.exists():
            raise ValidationError({"backup_path": _("The directory does not exist.")})
        if backup_db == False and backup_media == False:
            raise forms.ValidationError(_("Please select any backup option."))
        if interval == False and fixed == False:
            raise forms.ValidationError(_("Please select any backup automate option."))
        if interval == True and seconds == None:
            raise ValidationError({"seconds": _("This field is required.")})
        if fixed == True and hour == None:
            raise ValidationError({"hour": _("This field is required.")})
        if seconds:
            if seconds < 0:
                raise ValidationError(
                    {"seconds": _("Negative value is not accepatable.")}
                )
        if hour:
            if hour < 0 or hour > 24:
                raise ValidationError({"hour": _("Enter a hour between 0 to 24.")})
        if minute:
            if minute < 0 or minute > 60:
                raise ValidationError({"minute": _("Enter a minute between 0 to 60.")})
        return cleaned_data


class GdriveBackupSetupForm(ModelForm):
    verbose_name = _("Google Drive Backup")
    backup_db = forms.BooleanField(
        required=False,
        label="Backup DB",
        help_text=_("Enable to backup database to Google Drive"),
    )
    backup_media = forms.BooleanField(
        required=False,
        label="Backup Media",
        help_text=_("Enable to backup all media files to Google Drive"),
    )
    interval = forms.BooleanField(
        required=False,
        help_text=_("Enable to automate the backup in a period of seconds."),
    )
    fixed = forms.BooleanField(
        required=False, help_text=_("Enable to automate the backup in a fixed time.")
    )

    class Meta:
        model = GoogleDriveBackup
        # The tokens are written by the OAuth callback, never typed in.
        exclude = ["active", "access_token", "refresh_token", "token_expiry"]
        labels = {"oauth_credentials_file": _("Google Credentials File")}
        help_texts = {
            "oauth_credentials_file": _(
                "JSON key of a service account that is a member of the shared "
                "drive holding the folder, or an OAuth 2.0 client (web "
                "application) file followed by 'Authorize Google Drive'."
            )
        }

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def clean(self):
        cleaned_data = super().clean()
        backup_db = cleaned_data.get("backup_db")
        backup_media = cleaned_data.get("backup_media")
        interval = cleaned_data.get("interval")
        fixed = cleaned_data.get("fixed")
        seconds = cleaned_data.get("seconds")
        hour = cleaned_data.get("hour")
        minute = cleaned_data.get("minute")
        credentials_file = cleaned_data.get("oauth_credentials_file")

        if isinstance(credentials_file, UploadedFile):
            try:
                credentials_kind(json.loads(credentials_file.read()))
            except (ValueError, UnicodeDecodeError) as error:
                raise ValidationError(
                    {
                        "oauth_credentials_file": _(
                            "Please provide a valid Google credentials file: %(error)s"
                        )
                        % {"error": error}
                    }
                )
            finally:
                credentials_file.seek(0)
        elif not credentials_file:
            raise ValidationError(
                {"oauth_credentials_file": _("This field is required.")}
            )
        if backup_db == False and backup_media == False:
            raise forms.ValidationError(_("Please select any backup option."))
        if interval == False and fixed == False:
            raise forms.ValidationError(_("Please select any backup automate option."))
        if interval == True and seconds == None:
            raise ValidationError({"seconds": _("This field is required.")})
        if fixed == True and hour == None:
            raise ValidationError({"hour": _("This field is required.")})
        if seconds:
            if seconds < 0:
                raise ValidationError(
                    {"seconds": _("Negative value is not accepatable.")}
                )
        if hour:
            if hour < 0 or hour > 24:
                raise ValidationError({"hour": _("Enter a hour between 0 to 24.")})
        if minute:
            if minute < 0 or minute > 60:
                raise ValidationError({"minute": _("Enter a minute between 0 to 60.")})
        return cleaned_data

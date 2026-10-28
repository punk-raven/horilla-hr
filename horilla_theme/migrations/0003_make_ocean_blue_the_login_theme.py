"""
Make Ocean Blue the login page's theme on existing installs.

New installs get it from THEMES_DATA. Existing ones were seeded with Coral
Red as the login default, so the login page stayed coral whatever theme the
app itself uses. Move the default to Ocean Blue unless an admin already
picked a different one.
"""

from django.db import migrations

STOCK_DEFAULT = "Coral Red Theme (Default)"
BLUE = "Ocean Blue Theme"


def make_blue_the_login_theme(apps, schema_editor):
    HorillaColorTheme = apps.get_model("horilla_theme", "HorillaColorTheme")
    blue = HorillaColorTheme.objects.filter(name=BLUE).first()
    if blue is None:
        return
    defaults = HorillaColorTheme.objects.filter(is_default=True)
    if defaults.exclude(name=STOCK_DEFAULT).exists():
        return
    defaults.update(is_default=False)
    HorillaColorTheme.objects.filter(pk=blue.pk).update(is_default=True)


class Migration(migrations.Migration):

    dependencies = [
        ("horilla_theme", "0002_alter_companytheme_is_active_and_more"),
    ]

    operations = [
        migrations.RunPython(make_blue_the_login_theme, migrations.RunPython.noop),
    ]

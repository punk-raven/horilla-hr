"""
Make the stored themes' muted text colour (dark_100) readable.

dark_100 is only used as a text and placeholder colour. The stock themes were
seeded with #E6E6E6 (the light dark_50 shade), #D4D4D4 or #A8A8A8, all far
below WCAG AA on the light backgrounds, so subtitles, hints and inactive steps
were close to invisible in the light theme. Move rows that still hold one of
those stock values to the new muted text colour; anything else was chosen on
purpose and is left alone.
"""

from django.db import migrations

MUTED_TEXT = "#666666"
STOCK_VALUES = ("#E6E6E6", "#D4D4D4", "#A8A8A8")


def make_muted_text_readable(apps, schema_editor):
    HorillaColorTheme = apps.get_model("horilla_theme", "HorillaColorTheme")
    for theme in HorillaColorTheme.objects.all():
        if (theme.dark_100 or "").upper() in STOCK_VALUES:
            HorillaColorTheme.objects.filter(pk=theme.pk).update(dark_100=MUTED_TEXT)


class Migration(migrations.Migration):

    dependencies = [
        ("horilla_theme", "0003_make_ocean_blue_the_login_theme"),
    ]

    operations = [
        migrations.RunPython(make_muted_text_readable, migrations.RunPython.noop),
    ]

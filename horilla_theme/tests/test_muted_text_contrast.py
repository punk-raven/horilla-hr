"""The theme's muted text colour (dark_100) must be readable in the light theme.

text-dark-100 and placeholder:text-dark-100 are the only uses of dark_100:
page subtitles, hints, inactive steps and placeholders. Most stock themes set
it to #E6E6E6, the light dark_50 shade, which is 1.25:1 on white.
"""

import importlib

from django.apps import apps
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from horilla_theme.models import MUTED_TEXT, THEMES_DATA, HorillaColorTheme

migration = importlib.import_module("horilla_theme.migrations.0004_readable_muted_text")

# White cards, the #f3f3f3 card headers and the lightest page backgrounds.
LIGHT_BACKGROUNDS = ("#FFFFFF", "#F3F3F3", "#F6F6F6", "#F1F5F9", "#EFF6FF")
WCAG_AA = 4.5


def luminance(hex_colour):
    channels = [int(hex_colour.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground, background):
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class MutedTextContrastTests(SimpleTestCase):
    def test_every_stock_theme_meets_wcag_aa_on_light_backgrounds(self):
        for theme in THEMES_DATA:
            for background in LIGHT_BACKGROUNDS:
                with self.subTest(theme=theme["name"], background=background):
                    self.assertGreaterEqual(
                        contrast(theme["dark_100"], background), WCAG_AA
                    )


class MutedTextTemplateTests(TestCase):
    # The templates query the database, so this cannot be a SimpleTestCase.
    def test_templates_fall_back_to_the_readable_colour(self):
        for template in (
            "horilla_theme/components/header_scripts.html",
            "auth_layout.html",
        ):
            with self.subTest(template=template):
                html = render_to_string(template, {})
                self.assertIn(f"100: '{MUTED_TEXT}'", html)
                self.assertNotIn("100: '#E6E6E6'", html)


class MutedTextMigrationTests(TestCase):
    def test_stock_values_are_replaced_and_custom_ones_kept(self):
        base = dict(THEMES_DATA[1], is_default=False)
        stored = {
            "Stored Light Theme": "#E6E6E6",
            "Stored Lower Theme": "#d4d4d4",
            "Stored Coral Theme": "#A8A8A8",
            "Stored Custom Theme": "#3A3A3A",
        }
        for name, value in stored.items():
            HorillaColorTheme.objects.create(**dict(base, name=name, dark_100=value))

        migration.make_muted_text_readable(apps, None)

        values = dict(
            HorillaColorTheme.objects.filter(name__in=stored).values_list(
                "name", "dark_100"
            )
        )
        self.assertEqual(
            values,
            {
                "Stored Light Theme": MUTED_TEXT,
                "Stored Lower Theme": MUTED_TEXT,
                "Stored Coral Theme": MUTED_TEXT,
                "Stored Custom Theme": "#3A3A3A",
            },
        )

"""Primary buttons and badges keep their white label readable in every theme.

Filled controls use the theme's button fill (horilla_theme/colors.py), not
primary_600 itself: most stock 600 shades are below WCAG AA (4.5:1) under
white text, Ocean Blue's #3b82f6 at 3.7:1.
"""

from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from base.email_branding import BRAND_BLUE
from horilla_theme.colors import (
    WCAG_AA,
    contrast_ratio,
    deepen,
    parse_hex,
    to_hex,
    white_text_fill,
    white_text_fill_hover,
)
from horilla_theme.models import THEMES_DATA, HorillaColorTheme

WHITE = "#ffffff"


class ButtonFillContrastTests(SimpleTestCase):
    def test_every_stock_theme_fill_meets_wcag_aa_under_white(self):
        for theme in THEMES_DATA:
            with self.subTest(theme=theme["name"]):
                fill = white_text_fill(theme["primary_600"])
                self.assertGreaterEqual(contrast_ratio(fill, WHITE), WCAG_AA)

    def test_every_stock_theme_hover_is_aa_and_distinct(self):
        for theme in THEMES_DATA:
            with self.subTest(theme=theme["name"]):
                fill = white_text_fill(theme["primary_600"])
                hover = white_text_fill_hover(theme["primary_600"])
                self.assertGreaterEqual(contrast_ratio(hover, WHITE), WCAG_AA)
                self.assertNotEqual(hover.lower(), fill.lower())
                # Darker than the fill, by a visible step.
                self.assertGreater(
                    contrast_ratio(hover, WHITE), contrast_ratio(fill, WHITE) * 1.15
                )

    def test_ocean_blue_matches_the_sign_in_and_mail_blue(self):
        self.assertEqual(white_text_fill("#3b82f6"), BRAND_BLUE)

    def test_shades_that_already_pass_are_kept(self):
        # Crimson Wine (5.5:1) and Slate Gray (4.8:1).
        self.assertEqual(white_text_fill("#c92a2a"), "#c92a2a")
        self.assertEqual(white_text_fill("#64748b"), "#64748b")

    def test_the_fill_stays_as_close_to_the_theme_as_aa_allows(self):
        for theme in THEMES_DATA:
            fill = white_text_fill(theme["primary_600"])
            if fill == theme["primary_600"]:
                continue
            with self.subTest(theme=theme["name"]):
                # One 5% step lighter would fail.
                rgb = parse_hex(theme["primary_600"])
                for percent in range(100, 0, -5):
                    if to_hex(deepen(rgb, percent / 100)) == fill:
                        lighter = to_hex(deepen(rgb, (percent + 5) / 100))
                        self.assertLess(contrast_ratio(lighter, WHITE), WCAG_AA)
                        break
                else:
                    self.fail(f"{fill} is not a 5% step of {theme['primary_600']}")

    def test_short_hex_and_non_colours(self):
        self.assertEqual(white_text_fill("#fff"), "#737373")
        self.assertEqual(white_text_fill(""), "")
        self.assertEqual(white_text_fill(None), None)
        self.assertEqual(white_text_fill("hsl(8, 77%, 56%)"), "hsl(8, 77%, 56%)")
        self.assertEqual(white_text_fill_hover("not-a-colour"), "not-a-colour")

    def test_template_filters(self):
        html = Template(
            "{% load theme_colors %}{{ c|button_fill }} {{ c|button_fill_hover }}"
        ).render(Context({"c": "#3b82f6"}))
        self.assertEqual(html, f"{BRAND_BLUE} {white_text_fill_hover('#3b82f6')}")


def seed():
    for data in THEMES_DATA:
        HorillaColorTheme.objects.get_or_create(name=data["name"], defaults=data)


class ButtonFillTemplateTests(TestCase):
    def setUp(self):
        HorillaColorTheme.objects.all().delete()
        seed()

    def use_theme(self, name):
        HorillaColorTheme.objects.update(is_default=False)
        HorillaColorTheme.objects.filter(name=name).update(is_default=True)
        return HorillaColorTheme.objects.get(name=name)

    def test_signed_in_pages_fill_buttons_with_the_aa_shade(self):
        for name in ("Ocean Blue Theme", "Forest Green Theme", "Amber Glow Theme"):
            with self.subTest(theme=name):
                theme = self.use_theme(name)
                fill = white_text_fill(theme.primary_600)
                hover = white_text_fill_hover(theme.primary_600)
                html = render_to_string(
                    "horilla_theme/components/header_scripts.html", {}
                )
                # .oh-btn--secondary and the Tailwind bg-primary/brand-600.
                self.assertIn(f"background-color: {fill} !important;", html)
                self.assertIn(f"background-color: {hover} !important;", html)
                self.assertIn(
                    f"primary: {{ 600: '{fill}', 700: '{hover}', 800: '{hover}' }}",
                    html,
                )
                # Text, borders and rings keep the theme's own 600.
                self.assertIn(f"600: '{theme.primary_600}'", html)

    def test_sign_in_pages_fill_buttons_with_the_aa_shade(self):
        theme = self.use_theme("Forest Green Theme")
        body = self.client.get(reverse("login")).content.decode()
        fill = white_text_fill(theme.primary_600)
        self.assertIn(f"--primary-fill: {fill};", body)
        self.assertIn(f"600: '{fill}'", body)

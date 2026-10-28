"""The colour theme of the pre-login pages."""

import importlib

from django.apps import apps
from django.test import TestCase
from django.urls import reverse

from horilla_theme.models import LOGIN_FALLBACK_THEME, THEMES_DATA, HorillaColorTheme

migration = importlib.import_module(
    "horilla_theme.migrations.0003_make_ocean_blue_the_login_theme"
)


def seed():
    for data in THEMES_DATA:
        HorillaColorTheme.objects.get_or_create(name=data["name"], defaults=data)


class LoginPageThemeTests(TestCase):
    def setUp(self):
        HorillaColorTheme.objects.all().delete()
        seed()

    def test_new_installs_default_the_login_page_to_ocean_blue(self):
        default = HorillaColorTheme.objects.get(is_default=True)
        self.assertEqual(default.name, "Ocean Blue Theme")

    def test_login_page_uses_the_theme_marked_as_its_default(self):
        HorillaColorTheme.objects.filter(name="Forest Green Theme").update(
            is_default=True
        )
        HorillaColorTheme.objects.exclude(name="Forest Green Theme").update(
            is_default=False
        )
        for name in ("login", "forgot-password", "reset-send-success"):
            html = reverse(name)
            body = self.client.get(html).content.decode()
            self.assertIn("600: '#22c55e'", body, name)

    def test_login_page_falls_back_to_ocean_blue_without_a_default(self):
        HorillaColorTheme.objects.update(is_default=False)
        body = self.client.get(reverse("login")).content.decode()
        self.assertIn("600: '#3b82f6'", body)
        self.assertNotIn("#E54F38", body)

    def test_login_heading_names_the_brand_once(self):
        with self.settings(BRAND_NAME="PunkRaven HR"):
            body = self.client.get(reverse("login")).content.decode()
        self.assertNotIn("HR HR", " ".join(body.split()))
        self.assertIn("PunkRaven HR</span>", body)


class MakeBlueTheLoginThemeMigrationTests(TestCase):
    def setUp(self):
        HorillaColorTheme.objects.all().delete()
        seed()

    def default_name(self):
        return HorillaColorTheme.objects.get(is_default=True).name

    def test_moves_the_stock_coral_default_to_ocean_blue(self):
        HorillaColorTheme.objects.update(is_default=False)
        HorillaColorTheme.objects.filter(name=migration.STOCK_DEFAULT).update(
            is_default=True
        )
        migration.make_blue_the_login_theme(apps, None)
        self.assertEqual(self.default_name(), LOGIN_FALLBACK_THEME)

    def test_sets_ocean_blue_when_nothing_is_the_default(self):
        HorillaColorTheme.objects.update(is_default=False)
        migration.make_blue_the_login_theme(apps, None)
        self.assertEqual(self.default_name(), LOGIN_FALLBACK_THEME)

    def test_keeps_a_default_an_admin_picked(self):
        HorillaColorTheme.objects.update(is_default=False)
        HorillaColorTheme.objects.filter(name="Indigo Night Theme").update(
            is_default=True
        )
        migration.make_blue_the_login_theme(apps, None)
        self.assertEqual(self.default_name(), "Indigo Night Theme")

"""The light or dark theme choice: stored in a cookie, system by default."""

import re

from django.test import TestCase
from django.urls import reverse

from base.theme import THEME_COOKIE_MAX_AGE, THEME_COOKIE_NAME
from horilla.testkit import make_company, make_employee, make_user


class ToggleThemeViewTests(TestCase):
    url = reverse("toggle_theme")

    def post(self, **data):
        return self.client.post(self.url, data)

    def test_stores_an_explicit_choice_in_a_long_lived_cookie(self):
        for theme in ("dark", "light"):
            with self.subTest(theme=theme):
                response = self.post(theme=theme)
                self.assertEqual(response.status_code, 204)
                cookie = response.cookies[THEME_COOKIE_NAME]
                self.assertEqual(cookie.value, theme)
                self.assertEqual(cookie["max-age"], THEME_COOKIE_MAX_AGE)
                self.assertEqual(cookie["path"], "/")
                self.assertEqual(cookie["samesite"], "Lax")
                self.assertTrue(cookie["httponly"])

    def test_system_forgets_the_choice(self):
        self.client.cookies[THEME_COOKIE_NAME] = "dark"
        response = self.post(theme="system")
        self.assertEqual(response.status_code, 204)
        cookie = response.cookies[THEME_COOKIE_NAME]
        self.assertEqual(cookie.value, "")
        self.assertEqual(cookie["max-age"], 0)

    def test_rejects_anything_else(self):
        for data in ({}, {"theme": "blue"}, {"theme": "DARK"}, {"theme": ""}):
            with self.subTest(data=data):
                response = self.post(**data)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(THEME_COOKIE_NAME, response.cookies)

    def test_get_is_rejected(self):
        response = self.client.get(self.url, {"theme": "dark"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(THEME_COOKIE_NAME, response.cookies)

    def test_works_before_sign_in(self):
        """The sign-in pages have a theme switch too."""
        response = self.post(theme="dark")
        self.assertEqual(response.status_code, 204)

    def test_needs_the_csrf_token(self):
        client = self.client_class(enforce_csrf_checks=True)
        response = client.post(self.url, {"theme": "dark"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(THEME_COOKIE_NAME, response.cookies)

    def test_drops_the_old_session_value(self):
        session = self.client.session
        session["theme"] = "dark"
        session.save()
        self.post(theme="system")
        self.assertNotIn("theme", self.client.session)


class ThemeMarkupMixin:
    def get_page(self):
        raise NotImplementedError

    def assert_theme(self, body, preference):
        dark = preference == "dark"
        self.assertIn(f'var preference = "{preference}" || null;', body)
        self.assertEqual("dark" in self.classes(body, "html"), dark)
        # Dark comes from the template only for an explicit choice; for the
        # system default the head script decides, before the first paint.
        self.assertIn('matchMedia("(prefers-color-scheme: dark)")', body)
        self.assertIn("darkMode: 'class'", body)
        head = body.split("</head>")[0]
        self.assertLess(
            head.index("prefers-color-scheme"),
            head.index('rel="stylesheet"'),
            "the theme script must run before any stylesheet",
        )

    def classes(self, body, tag):
        match = re.search(rf'<{tag}\b[^>]*\bclass="([^"]*)"', body)
        self.assertIsNotNone(match, f"no <{tag} class=...> in the page")
        return match.group(1).split()

    def test_cookie_dark(self):
        self.client.cookies[THEME_COOKIE_NAME] = "dark"
        body = self.get_page()
        self.assert_theme(body, "dark")
        self.assertIn("dark", self.classes(body, "body"))

    def test_cookie_light(self):
        self.client.cookies[THEME_COOKIE_NAME] = "light"
        body = self.get_page()
        self.assert_theme(body, "light")
        self.assertNotIn("dark", self.classes(body, "body"))

    def test_no_cookie_follows_the_system(self):
        body = self.get_page()
        self.assert_theme(body, "")
        self.assertNotIn("dark", self.classes(body, "body"))

    def test_invalid_cookie_follows_the_system(self):
        self.client.cookies[THEME_COOKIE_NAME] = "<script>"
        body = self.get_page()
        self.assert_theme(body, "")


class SignedInPageThemeTests(ThemeMarkupMixin, TestCase):
    def setUp(self):
        company = make_company()
        self.user = make_user("theme_admin", is_superuser=True)
        self.user.is_new_employee = False
        self.user.save()
        make_employee(company=company, email="theme@test.horilla", user=self.user)
        self.client.force_login(self.user)

    def get_page(self):
        response = self.client.get(reverse("employee-view"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_header_shows_the_icon_for_the_theme_on_screen(self):
        """Both icons render; CSS keyed on html.dark shows the right one."""
        body = self.get_page()
        self.assertIn("data-theme-toggle", body)
        self.assertIn("hx-theme-when-light fa-solid fa-moon", body)
        self.assertIn("hx-theme-when-dark fa-solid fa-lightbulb", body)
        self.assertIn("html.dark .hx-theme-when-light", body)

    def test_session_choice_from_before_the_cookie_is_kept(self):
        session = self.client.session
        session["theme"] = "dark"
        session.save()
        response = self.client.get(reverse("employee-view"))
        self.assertIn('var preference = "dark" || null;', response.content.decode())
        self.assertEqual(response.cookies[THEME_COOKIE_NAME].value, "dark")
        self.assertNotIn("theme", self.client.session)


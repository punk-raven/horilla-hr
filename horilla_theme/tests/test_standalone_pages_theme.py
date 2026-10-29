"""
The light or dark theme on the standalone pages that extend neither
index.html nor auth_layout.html: the first-run setup (initialize_database)
and the error pages (403, 404, 405, 500, no_perm).

They used to be light only, whatever the stored choice or the OS said.
"""

import re

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from base.theme import THEME_COOKIE_NAME
from horilla_theme.colors import white_text_fill
from horilla_theme.models import THEMES_DATA, HorillaColorTheme

ERROR_TEMPLATES = ("403.html", "404.html", "405.html", "no_perm.html")
SETUP_SHELL = "initialize_database/horilla_user.html"


def seed():
    for data in THEMES_DATA:
        HorillaColorTheme.objects.get_or_create(name=data["name"], defaults=data)


class StandalonePageMixin:
    template = None

    def setUp(self):
        HorillaColorTheme.objects.all().delete()
        seed()

    def render(self, theme=None, template=None):
        request = RequestFactory().get("/", HTTP_HOST="localhost")
        if theme:
            request.COOKIES[THEME_COOKIE_NAME] = theme
        request.session = SessionStore()
        request.user = AnonymousUser()
        request._messages = FallbackStorage(request)
        return render_to_string(template or self.template, {}, request=request)

    def classes(self, body, tag):
        match = re.search(rf'<{tag}\b[^>]*\bclass="([^"]*)"', body)
        self.assertIsNotNone(match, f"no <{tag} class=...> in the page")
        return match.group(1).split()

    def assert_theme_init_in_head(self, body):
        head = body.split("</head>")[0]
        self.assertIn('matchMedia("(prefers-color-scheme: dark)")', head)
        self.assertIn("window.horillaTheme", head)
        for stylesheet in re.finditer(r'rel="stylesheet"', head):
            self.assertLess(
                head.index("prefers-color-scheme"),
                stylesheet.start(),
                "the theme script must run before any stylesheet",
            )

    def assert_theme(self, theme):
        body = self.render(theme)
        self.assert_theme_init_in_head(body)
        dark = theme == "dark"
        self.assertIn(f'var preference = "{theme or ""}" || null;', body)
        self.assertEqual("dark" in self.classes(body, "html"), dark)
        self.assertEqual("dark" in self.classes(body, "body"), dark)
        return body


class ErrorPageThemeTests(StandalonePageMixin, TestCase):
    def test_every_error_page_follows_the_theme(self):
        for template in ERROR_TEMPLATES:
            self.template = template
            for theme in ("dark", "light", None):
                with self.subTest(template=template, theme=theme):
                    body = self.assert_theme(theme)
                    self.assertIn("hx-error-page", self.classes(body, "body"))
                    # The dark palette, scoped to the page.
                    self.assertIn(
                        "html.dark body.hx-error-page {\n"
                        "        background-color: #05080f;",
                        body,
                    )

    def test_error_pages_use_the_theme_and_its_aa_button_fill(self):
        HorillaColorTheme.objects.update(is_default=False)
        HorillaColorTheme.objects.filter(name="Forest Green Theme").update(
            is_default=True
        )
        fill = white_text_fill("#22c55e")
        for template in ERROR_TEMPLATES:
            with self.subTest(template=template):
                body = self.render(template=template)
                self.assertIn("--primary-600: #22c55e;", body)
                self.assertIn(f"--primary-fill: {fill};", body)

    def test_the_500_page_needs_no_request_and_no_database(self):
        with self.assertNumQueries(0):
            body = render_to_string("500.html")
        self.assert_theme_init_in_head(body)
        self.assertIn(">500</h1>", body)
        self.assertIn("Something went wrong!", body)
        self.assertIn("hx-error-page", self.classes(body, "body"))
        # The stock login theme's colours, without asking the database.
        self.assertIn(f"--primary-fill: {white_text_fill('#3b82f6')};", body)

    def test_the_404_texts_are_unchanged(self):
        body = self.render(template="404.html")
        self.assertIn(">404</h1>", body)
        self.assertIn("Page not found!", body)
        self.assertIn("Oops! The page you are looking for could not be found.", body)

    def test_went_wrong_is_the_404_page(self):
        body = self.render(template="went_wrong.html")
        self.assertIn("hx-error-page", body)
        self.assertIn(">404</h1>", body)


class ThemeInitRunsOnceTests(StandalonePageMixin, TestCase):
    """no_perm.html and 404.html are also swapped into a page by htmx."""

    def test_a_second_copy_does_nothing(self):
        body = self.render(template="no_perm.html")
        script = body.split("(function () {", 1)[1]
        self.assertTrue(
            script.lstrip().startswith("if (window.horillaTheme) {"),
            "theme_init.html must bail out when the page already has it",
        )


class SetupPageThemeTests(StandalonePageMixin, TestCase):
    template = SETUP_SHELL

    def test_cookie_dark(self):
        self.assert_theme("dark")

    def test_cookie_light(self):
        self.assert_theme("light")

    def test_no_cookie_follows_the_system(self):
        self.assert_theme(None)

    def test_theme_switch_tokens_and_dark_styles(self):
        body = self.render()
        self.assertIn("oh-setup-page", self.classes(body, "body"))
        self.assertIn("data-theme-toggle", body)
        self.assertIn("oh-auth-alerts", body)
        # The login page's theme (Ocean Blue on a new install).
        self.assertIn("--primary-600: #3b82f6;", body)
        self.assertIn(f"--primary-fill: {white_text_fill('#3b82f6')};", body)
        self.assertRegex(body, r"horilla_theme/assets/css/ux\.css\?v=\d+")
        # The card grows with the Company step instead of overflowing.
        self.assertNotIn("max-height: 790px", body)

    def test_setup_dark_styles_are_built(self):
        from django.contrib.staticfiles import finders

        path = finders.find("horilla_theme/assets/css/ux.css")
        with open(path, encoding="utf-8") as built:
            css = built.read()
        self.assertIn(".dark .oh-setup-page", css)
        self.assertIn(".oh-setup-theme-toggle", css)

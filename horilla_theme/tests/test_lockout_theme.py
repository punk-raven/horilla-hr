"""
django-axes' lockout page: on the sign-in card, in the light or dark theme,
with axes' 429 status and the cool-off in words.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse

from .test_auth_flow_theme import SignInCardPageMixin

LIMIT = 3


def reset_axes():
    from axes.handlers.proxy import AxesProxyHandler

    AxesProxyHandler.reset_attempts()
    AxesProxyHandler.reset_logs(age_days=None)


@override_settings(AXES_FAILURE_LIMIT=LIMIT, AXES_COOLOFF_TIME=timedelta(minutes=30))
class LockoutPageThemeTests(SignInCardPageMixin, TestCase):
    def setUp(self):
        reset_axes()
        self.addCleanup(reset_axes)

    def attempt(self, **extra):
        return self.client.post(
            reverse("login"),
            {"username": "lockout_check", "password": "wrong-password"},
            **extra,
        )

    def lock_out(self):
        """The failures up to the limit; the last one is the locking one.

        The earlier ones follow their redirect, as a browser does, so their
        "invalid" toast is shown on the login page and not carried over.
        """
        for _ in range(LIMIT - 1):
            response = self.attempt(follow=True)
            self.assertEqual(response.redirect_chain, [(reverse("login"), 302)])
        return self.attempt()

    def get_page(self):
        response = self.lock_out()
        self.assertEqual(response.status_code, 429)
        return response.content.decode()

    def test_locking_attempt_renders_the_themed_page(self):
        response = self.lock_out()
        self.assertEqual(response.status_code, 429)
        self.assertTemplateUsed(response, "account_lockout.html")
        self.assertTemplateUsed(response, "auth_layout.html")
        self.assertTemplateUsed(response, "horilla_theme/components/theme_init.html")
        body = response.content.decode()
        self.assertIn("Too many login attempts", body)
        self.assertIn("You can try again 30\xa0minutes after your last attempt.", body)
        self.assertIn(f'href="{reverse("login")}"', body)
        self.assertIn(f'href="{reverse("forgot-password")}"', body)
        # Not axes' bare text response.
        self.assertNotIn("Account locked: too many login attempts", body)

    def test_attempts_while_locked_get_the_same_page(self):
        self.lock_out()
        response = self.attempt()
        self.assertEqual(response.status_code, 429)
        self.assertTemplateUsed(response, "account_lockout.html")

    def test_no_invalid_password_toast_on_the_lockout(self):
        body = self.get_page()
        self.assertNotIn("Invalid username or password.", body)
        # Nor queued for the next page.
        body = self.client.get(reverse("login")).content.decode()
        self.assertNotIn("Invalid username or password.", body)

    @override_settings(AXES_COOLOFF_TIME=timedelta(hours=1, minutes=30))
    def test_cooloff_is_in_words(self):
        body = self.get_page()
        # timesince keeps each number with its unit (a no-break space).
        self.assertIn(
            "You can try again 1\xa0hour, 30\xa0minutes after your last attempt.",
            body,
        )

    @override_settings(AXES_COOLOFF_TIME=None)
    def test_permanent_lock_points_to_the_administrator(self):
        body = self.get_page()
        self.assertIn("Ask your administrator to unlock your account.", body)
        self.assertNotIn("You can try again", body)

    def test_xhr_posts_still_get_json(self):
        for _ in range(LIMIT - 1):
            self.attempt(follow=True)
        response = self.attempt(HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["cooloff_time"], "PT30M")

"""
The light or dark theme on the auth-flow pages that are not the login form:
two-factor code, candidate sign-in, and the change password / username cards
(also the forced change on first login).
"""

from django.test import TestCase
from django.urls import reverse

from base.theme import THEME_COOKIE_NAME
from horilla.testkit import make_company, make_employee, make_user

from .test_theme_preference import ThemeMarkupMixin


class AuthFlowMixin(ThemeMarkupMixin):
    """Every page here must carry theme_init.html, like the login page."""

    def assert_dark_on_request(self, body):
        self.assertIn("dark", self.classes(body, "html"))
        self.assertIn('var preference = "dark" || null;', body)


class SignInCardPageMixin(AuthFlowMixin):
    """Pages moved onto auth_layout.html: its card and theme switch."""

    def test_uses_the_sign_in_card_and_switch(self):
        body = self.get_page()
        self.assertIn("oh-auth-card", body)
        self.assertIn("data-theme-toggle", body)
        self.assertIn("oh-auth-alerts", body)
        # Not the old standalone, light-only page any more.
        self.assertNotIn("build/css/style.min.css", body)


class TwoFactorPageThemeTests(SignInCardPageMixin, TestCase):
    def setUp(self):
        company = make_company()
        self.user = make_user("tfa_user")
        self.user.is_new_employee = False
        self.user.save()
        make_employee(company=company, email="tfa@test.horilla", user=self.user)
        self.client.force_login(self.user)

    def get_page(self):
        # GET only renders while 2FA is switched on; a POST without a live
        # code renders the page (with "OTP expired") in every configuration.
        response = self.client.post(reverse("two-factor"), {"otp": "000000"})
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_without_a_code_offers_to_send_one(self):
        body = self.get_page()
        self.assertIn(f'action="{reverse("send-otp")}"', body)
        self.assertIn("tfa@test.horilla", body)

    def test_with_a_code_asks_for_it(self):
        from django.utils import timezone

        session = self.client.session
        session["otp_code"] = "123456"
        session["otp_code_timestamp"] = timezone.now().timestamp()
        session.save()
        body = self.get_page()
        self.assertIn('name="otp"', body)
        self.assertIn('autocomplete="one-time-code"', body)
        self.assertIn('id="otp-timer"', body)


class CandidateLoginThemeTests(SignInCardPageMixin, TestCase):
    def get_page(self):
        response = self.client.get(reverse("candidate-login"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_bad_credentials_are_shown_on_the_card(self):
        self.client.cookies[THEME_COOKIE_NAME] = "dark"
        response = self.client.post(
            reverse("candidate-login"), {"email": "nobody@x.com", "phone": "1"}
        )
        body = response.content.decode()
        self.assertIn('role="alert"', body)
        self.assertIn("Invalid credentials", body)
        self.assert_dark_on_request(body)


class AccountCardMixin(AuthFlowMixin):
    """Change password / username: cards over the signed-in app."""

    url_name = None

    def setUp(self):
        company = make_company()
        self.user = make_user("pw_user")
        self.user.is_new_employee = False
        self.user.save()
        make_employee(company=company, email="pw@test.horilla", user=self.user)
        self.client.force_login(self.user)

    def get_page(self):
        response = self.client.get(reverse(self.url_name))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_card_uses_the_sign_in_styles(self):
        body = self.get_page()
        self.assertIn('class="pw-modal oh-auth-card"', body)
        self.assertIn(".dark .pw-modal-backdrop", body)


class ChangePasswordThemeTests(AccountCardMixin, TestCase):
    url_name = "change-password"

    def test_forced_first_login_change_gets_the_theme(self):
        self.user.is_new_employee = True
        self.user.save()
        self.client.cookies[THEME_COOKIE_NAME] = "dark"
        # Any page sends a new employee to the change-password card first.
        response = self.client.get(reverse("employee-view"), follow=True)
        self.assertEqual(
            response.redirect_chain[-1][0], reverse("change-password")
        )
        body = response.content.decode()
        self.assert_dark_on_request(body)
        self.assertIn('class="pw-modal oh-auth-card"', body)
        # No way around it: the card has no close button.
        self.assertNotIn('class="pw-modal__close"', body)


class ChangeUsernameThemeTests(AccountCardMixin, TestCase):
    url_name = "change-username"

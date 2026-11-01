"""A stale sign-in form redirects instead of showing Django's 403 page.

Production log, one browser, one sign-in:

    POST /login/?next=/  302   signed in; Django rotates the CSRF token
    GET  /               302
    POST /login/?next=/  403   "CSRF token from POST incorrect."
    GET  /dashboard/     200

The second POST was the same form submitted again while the dashboard loaded.
The user was signed in, but the browser showed the 403.
"""

import re

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from horilla.testkit import make_company, make_employee, make_user

PASSWORD = "secret123"


class StaleLoginFormTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        user = make_user("stale_login", password=PASSWORD)
        make_employee(
            company=make_company("CSRF Co"),
            email="stale_login@test.horilla",
            first_name="Stale",
            last_name="Login",
            user=user,
        )
        self.url = reverse("login") + "?next=/employee/employee-view/"

    def open_login_page(self):
        """The csrfmiddlewaretoken the rendered form would submit."""
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        return re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()
        ).group(1)

    def submit(self, token, password=PASSWORD):
        return self.client.post(
            self.url,
            {
                "csrfmiddlewaretoken": token,
                "username": "stale_login",
                "password": password,
            },
        )

    def test_resubmitting_after_signing_in_goes_on_to_next(self):
        token = self.open_login_page()
        first = self.submit(token)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first.url, "/employee/employee-view/")

        second = self.submit(token)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second.url, "/employee/employee-view/")

    def test_expired_form_when_signed_out_returns_to_the_login_page(self):
        token = self.open_login_page()
        self.client.cookies.pop("csrftoken")  # as if the token had expired
        response = self.submit(token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url)
        self.assertNotIn("_auth_user_id", self.client.session)
        notes = [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]
        # A note, not an alarm: INFO renders as oh-alert--info.
        self.assertEqual(
            notes,
            [("oh-alert--info", "The sign-in page had expired. Please sign in again.")],
        )

    def test_stale_form_never_signs_anyone_in(self):
        token = self.open_login_page()
        self.client.cookies.pop("csrftoken")
        self.submit(token)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unsafe_next_falls_back_to_home(self):
        self.url = reverse("login") + "?next=https://evil.example/"
        token = self.open_login_page()
        self.submit(token)
        response = self.submit(token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_other_csrf_failures_still_answer_403(self):
        self.open_login_page()
        response = self.client.post(reverse("toggle_theme"), {"theme": "dark"})
        self.assertEqual(response.status_code, 403)

    def test_login_form_submits_once(self):
        page = self.client.get(self.url).content.decode()
        self.assertIn('id="loginForm"', page)
        self.assertIn("form.dataset.submitted", page)

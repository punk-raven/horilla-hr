"""Mail server settings: the stored secret stays out of the page, and the
test send checks the server that was picked within a bounded time."""

from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from base.forms import DynamicMailConfForm
from base.models import DynamicEmailConfiguration
from base.views import TEST_EMAIL_TIMEOUT
from horilla.testkit import make_company, make_employee, make_user


def mail_server(**overrides):
    fields = {
        "host": "smtp.example.com",
        "port": 465,
        "username": "user",
        "password": "s3cret-api-key",
        "from_email": "hr@example.com",
        "use_ssl": True,
        "use_tls": False,
    }
    fields.update(overrides)
    return DynamicEmailConfiguration.objects.create(**fields)


def form_data(server, **overrides):
    data = {
        "host": server.host,
        "port": server.port,
        "username": server.username,
        "password": "",
        "from_email": server.from_email,
        "display_name": "HR",
        "timeout": 30,
        "company_id": server.company_id_id,
        "use_ssl": "on",
    }
    data.update(overrides)
    return data


class MailServerFormSecretTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = make_company("Secret Corp")

    def test_stored_password_is_not_rendered(self):
        server = mail_server(company_id=self.company)

        html = str(DynamicMailConfForm(instance=server)["password"])

        self.assertIn('type="password"', html)
        self.assertNotIn("s3cret-api-key", html)

    def test_blank_password_on_edit_keeps_the_stored_one(self):
        server = mail_server(company_id=self.company)

        form = DynamicMailConfForm(form_data(server), instance=server)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().password, "s3cret-api-key")

    def test_new_password_on_edit_replaces_it(self):
        server = mail_server(company_id=self.company)

        form = DynamicMailConfForm(
            form_data(server, password="rotated"), instance=server
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().password, "rotated")


@override_settings(STATIC_ROOT=settings.BASE_DIR / "static")
class MailServerTestEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = make_company("Mail Corp")
        cls.user = make_user("mailadmin", is_superuser=True)
        make_employee(company=company, email="mailadmin@example.com", user=cls.user)
        # The primary server is what the backend picks up by default ...
        cls.primary = mail_server(host="primary.example.com", is_primary=True)
        # ... but the test must exercise the one that was clicked. One server
        # per company: save() deletes a second one for the same company.
        cls.picked = mail_server(
            host="picked.example.com",
            port=587,
            timeout=60,
            company_id=make_company("Other Corp", hq=False),
        )

    def send(self, server):
        self.client.force_login(self.user)
        sent = []

        def capture(message, *args, **kwargs):
            sent.append(message.connection)
            return 1

        with mock.patch(
            "base.views.EmailMultiAlternatives.send", autospec=True, side_effect=capture
        ):
            self.client.post(
                reverse("mail-server-test-email") + f"?instance_id={server.pk}",
                {"to_email": "someone@example.com"},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(len(sent), 1)
        return sent[0]

    def test_uses_the_picked_server(self):
        connection = self.send(self.picked)

        self.assertEqual(
            (connection.host, connection.port), ("picked.example.com", 587)
        )

    def test_caps_the_per_address_timeout(self):
        connection = self.send(self.picked)

        self.assertEqual(connection.timeout, TEST_EMAIL_TIMEOUT)

    def test_blank_timeout_still_gets_the_cap(self):
        connection = self.send(self.primary)

        self.assertEqual(connection.timeout, TEST_EMAIL_TIMEOUT)

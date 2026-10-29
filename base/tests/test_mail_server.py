"""Mail server settings: the stored secret stays out of the page, the test
send checks the server that was picked within a bounded time, and a server
can deliver over Resend's HTTPS API where outbound SMTP is blocked."""

import json
from unittest import mock

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.mail import EmailMessage
from django.test import TestCase, override_settings
from django.urls import reverse

from base.backends import ConfiguredEmailBackend
from base.forms import DynamicMailConfForm
from base.models import DynamicEmailConfiguration, EmailLog
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
        "transport": server.transport,
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


class MailServerTransportFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = make_company("Transport Corp")

    def new_server_form(self, **overrides):
        data = {
            "transport": DynamicEmailConfiguration.RESEND_API,
            "password": "re_test_key",
            "from_email": "hr@example.com",
            "display_name": "HR",
            "timeout": 30,
            "company_id": self.company.pk,
        }
        data.update(overrides)
        return DynamicMailConfForm(data)

    def test_resend_api_needs_no_smtp_settings(self):
        form = self.new_server_form()

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().transport, DynamicEmailConfiguration.RESEND_API)

    def test_smtp_still_requires_host_port_and_username(self):
        form = self.new_server_form(transport=DynamicEmailConfiguration.SMTP)

        self.assertFalse(form.is_valid())
        self.assertEqual(set(form.errors), {"host", "port", "username"})

    def test_select_renders_the_field_toggle(self):
        html = str(self.new_server_form()["transport"])

        self.assertIn('"api": "resend_api"', html)
        self.assertIn("Resend API Key", html)

    def test_select_reapplies_the_toggle_after_htmx_settles(self):
        # Reopening the modal swaps in a form with the same row ids, and htmx's
        # settle step then restores the rows' style, showing the SMTP fields.
        html = str(self.new_server_form()["transport"])

        self.assertIn("htmx:afterSettle", html)

    def test_resend_api_saves_with_the_hidden_smtp_fields_still_posted(self):
        # The browser posts the hidden SMTP rows too: "Use TLS" is on by
        # default, and "Use SSL" is on when the server was set up for port 465.
        form = self.new_server_form(
            host="smtp.example.com",
            port=465,
            username="user",
            use_tls="on",
            use_ssl="on",
        )

        self.assertTrue(form.is_valid(), form.errors)
        server = form.save()
        self.assertEqual(server.transport, DynamicEmailConfiguration.RESEND_API)
        self.assertEqual(
            (server.host, server.port, server.username), (None, None, None)
        )
        self.assertEqual((server.use_tls, server.use_ssl), (False, False))

    def test_switching_an_ssl_smtp_server_to_resend_api_saves(self):
        server = mail_server(company_id=self.company)

        form = DynamicMailConfForm(
            form_data(
                server,
                transport=DynamicEmailConfiguration.RESEND_API,
                password="re_test_key",
                use_tls="on",
            ),
            instance=server,
        )

        self.assertTrue(form.is_valid(), form.errors)
        server = form.save()
        self.assertEqual(server.transport, DynamicEmailConfiguration.RESEND_API)
        self.assertEqual(server.password, "re_test_key")

    def test_smtp_still_rejects_tls_and_ssl_together(self):
        form = self.new_server_form(
            transport=DynamicEmailConfiguration.SMTP,
            host="smtp.example.com",
            port=465,
            username="user",
            use_tls="on",
            use_ssl="on",
        )

        self.assertFalse(form.is_valid())
        self.assertIn("mutually exclusive", str(form.non_field_errors()))

    def test_api_key_input_is_styled_like_the_other_inputs(self):
        # An unstyled password input has no border: the required key field
        # looked like it was missing.
        html = str(self.new_server_form()["password"])

        self.assertIn("oh-input", html)
        self.assertIn('type="password"', html)

    def test_labels_keep_their_acronyms(self):
        form = self.new_server_form()

        self.assertEqual(form.fields["use_tls"].label, "Use TLS")
        self.assertEqual(form.fields["use_ssl"].label, "Use SSL")
        self.assertEqual(form.fields["timeout"].label, "Email Send Timeout (Seconds)")
        self.assertEqual(form.required_fields, ["host", "port", "username"])


def resend_response(*args, **kwargs):
    response = mock.Mock(status_code=200, headers={})
    response.json.return_value = {"id": "email-id"}
    response.text = json.dumps({"id": "email-id"})
    return response


class ResendApiBackendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.server = mail_server(
            transport=DynamicEmailConfiguration.RESEND_API,
            host=None,
            port=None,
            password="re_test_key",
            is_primary=True,
        )

    def test_sends_over_https_with_the_stored_key_and_never_opens_smtp(self):
        message = EmailMessage(
            "Hello", "Body", "HR <hr@example.com>", ["someone@example.com"]
        )
        with (
            mock.patch("smtplib.SMTP", side_effect=AssertionError("SMTP used")),
            mock.patch("smtplib.SMTP_SSL", side_effect=AssertionError("SMTP used")),
            mock.patch(
                "requests.Session.request", side_effect=resend_response
            ) as request,
        ):
            sent = ConfiguredEmailBackend().send_messages([message])

        self.assertEqual(sent, 1)
        call = request.call_args.kwargs
        self.assertEqual(
            (call["method"], call["url"]), ("POST", "https://api.resend.com/emails")
        )
        self.assertEqual(call["headers"]["Authorization"], "Bearer re_test_key")
        self.assertEqual(json.loads(call["data"])["to"], ["someone@example.com"])
        self.assertEqual(EmailLog.objects.get(subject="Hello").status, "sent")


@override_settings(STATIC_ROOT=settings.BASE_DIR / "static")
class MailServerTestEmailFailureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = make_company("Fail Corp")
        cls.user = make_user("failadmin", is_superuser=True)
        make_employee(company=company, email="failadmin@example.com", user=cls.user)
        # A company server that would swallow errors must not fake a pass.
        cls.server = mail_server(
            host="smtp.blocked.example", is_primary=True, fail_silently=True
        )

    def test_a_timeout_is_reported_logged_and_explained(self):
        self.client.force_login(self.user)
        with (
            mock.patch("smtplib.SMTP_SSL", side_effect=TimeoutError("timed out")),
            self.assertLogs("base.views", level="ERROR"),
        ):
            response = self.client.post(
                reverse("mail-server-test-email") + f"?instance_id={self.server.pk}",
                {"to_email": "someone@example.com"},
                HTTP_HX_REQUEST="true",
            )

        [message] = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn("The test email was not sent: timed out.", message)
        self.assertIn("smtp.blocked.example:465 did not answer", message)


@override_settings(STATIC_ROOT=settings.BASE_DIR / "static")
class ResendApiTestEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = make_company("Resend Corp")
        cls.user = make_user("resendadmin", is_superuser=True)
        make_employee(company=company, email="resendadmin@example.com", user=cls.user)
        # Hidden TLS and SSL values left over from SMTP must not matter.
        cls.server = mail_server(
            transport=DynamicEmailConfiguration.RESEND_API,
            password="re_bad_key",
            use_tls=True,
            use_ssl=True,
            is_primary=True,
        )

    def test_backend_ignores_tls_and_ssl_for_the_api(self):
        backend = ConfiguredEmailBackend()

        self.assertEqual((backend.use_tls, backend.use_ssl), (False, False))

    def test_a_rejected_key_is_reported_with_the_api_message(self):
        def unauthorized(*args, **kwargs):
            response = mock.Mock(status_code=401, headers={})
            body = {"message": "API key is invalid", "name": "validation_error"}
            response.json.return_value = body
            response.text = json.dumps(body)
            return response

        self.client.force_login(self.user)
        with (
            mock.patch("requests.Session.request", side_effect=unauthorized),
            self.assertLogs("base.views", level="ERROR"),
        ):
            response = self.client.post(
                reverse("mail-server-test-email") + f"?instance_id={self.server.pk}",
                {"to_email": "someone@example.com"},
                HTTP_HX_REQUEST="true",
            )

        [message] = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(
            message,
            "The test email was not sent: the API answered 401: API key is "
            "invalid. Check the Resend API key and that the sender domain is "
            "verified.",
        )

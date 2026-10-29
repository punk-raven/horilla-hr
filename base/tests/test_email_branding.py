"""Every HTML email carries the PunkRaven logo inline, and the brand blue.

The production host sits behind Cloudflare Access, so a logo linked from it
reaches a mail client as the Access login page, and Gmail and Outlook block
data: URIs. The logo therefore travels in the message as a multipart/related
part with a Content-ID, which the HTML shows as cid:brand_logo -- over SMTP,
and over the Resend API as an attachment with a content_id.

These send the real mails through the senders and the configured backends,
then read what a mail client would receive.
"""

import base64
import datetime
import email
import io
import json
import re
from email import policy
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from base.backends import ConfiguredEmailBackend
from base.email_branding import (
    BRAND_BLUE,
    BRAND_LOGO_CID,
    BRAND_LOGO_STATIC,
    brand_logo_bytes,
)
from base.models import DynamicEmailConfiguration
from horilla.testkit import make_company, make_employee, make_user
from horilla.testkit.factories import make_candidate, make_payslip, make_recruitment

ORANGE = re.compile(r"#e54f3[78]|theme_obj\.primary_600", re.IGNORECASE)
MAIL_TEMPLATES = [
    "base/templates/base/mail_templates/password_reset_email.html",
    "base/templates/base/mail_templates/leave_request_template.html",
    "horilla_theme/templates/base/mail_templates/leave_request_template.html",
    "employee/templates/employee/mail_templates/invitation_email.html",
    "payroll/templates/payroll/mail_templates/default.html",
    "onboarding/templates/onboarding/mail_templates/default.html",
    "horilla_theme/templates/onboarding/mail_templates/default.html",
    "helpdesk/templates/helpdesk/mail_templates/ticket_mail.html",
]
HOSTS = ["hr.punkraven.test", "testserver", "localhost"]


def received(message):
    """The message as the recipient's mail client parses it."""
    return email.message_from_bytes(message.message().as_bytes(), policy=policy.default)


def html_of(parsed):
    return parsed.get_body(preferencelist=("html",)).get_content()


def inline_parts(parsed):
    return {
        part["Content-ID"].strip("<>"): part
        for part in parsed.walk()
        if part["Content-ID"]
    }


def a_request(user, host="hr.punkraven.test"):
    request = RequestFactory().get("/", HTTP_HOST=host)
    request.user = user
    request.session = SessionStore()
    request.session.create()
    return request


class BrandedMailAssertions:
    def assert_branded(self, message, *, structure=None):
        parsed = received(message)
        html = html_of(parsed)
        parts = inline_parts(parsed)

        self.assertIn(f'src="cid:{BRAND_LOGO_CID}"', html)
        self.assertIn(BRAND_LOGO_CID, parts)
        logo = parts[BRAND_LOGO_CID]
        self.assertEqual(logo.get_content_type(), "image/png")
        self.assertEqual(logo.get_content_disposition(), "inline")
        self.assertEqual(logo.get_content(), brand_logo_bytes())
        # The logo sits with the HTML it belongs to, not among the files.
        self.assertEqual(
            parsed.get_body(preferencelist=("related",)).get_content_type(),
            "multipart/related",
        )
        self.assertIn(logo, list(parsed.get_body(("related",)).iter_parts()))

        self.assertNotRegex(html, ORANGE)
        self.assertNotIn("horilla-logo", html)
        self.assertNotIn("horilla-sticker", html)
        self.assertNotIn("Horilla", html)
        # No image is linked from the app's own host (behind Cloudflare Access).
        for src in re.findall(r'<img[^>]+src="([^"]+)"', html):
            self.assertFalse(any(host in src for host in HOSTS), f"remote image {src}")
            self.assertFalse(src.startswith("data:"), "data: URI image")
        if structure:
            self.assertEqual(shape(parsed), structure)
        return parsed, html


def shape(part):
    if part.is_multipart():
        inner = ", ".join(shape(p) for p in part.iter_parts())
        return f"{part.get_content_type()}[{inner}]"
    return part.get_content_type()


class MailTemplateSourceTests(TestCase):
    def test_no_orange_or_horilla_logo_left_in_any_mail_template(self):
        for name in MAIL_TEMPLATES:
            source = (Path(settings.BASE_DIR) / name).read_text()
            with self.subTest(template=name):
                self.assertNotRegex(source, ORANGE)
                self.assertNotIn("horilla-logo", source)
                self.assertNotIn("cid:company_logo", source)
                self.assertIn(f"cid:{BRAND_LOGO_CID}", source)

    def test_brand_blue_meets_aa_for_white_text(self):
        def luminance(hex_colour):
            channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [
                c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                for c in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        self.assertGreaterEqual(1.05 / (luminance(BRAND_BLUE) + 0.05), 4.5)

    def test_logo_asset_is_a_small_square_png(self):
        path = Path(settings.BASE_DIR) / "static" / BRAND_LOGO_STATIC
        with Image.open(path) as logo:
            self.assertEqual(logo.format, "PNG")
            self.assertEqual(logo.size, (256, 256))
        self.assertLess(path.stat().st_size, 8 * 1024)


@override_settings(ALLOWED_HOSTS=HOSTS)
class BrandedSenderTests(BrandedMailAssertions, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = make_company("Brand Co")
        cls.user = make_user("brandadmin", is_superuser=True)
        cls.employee = make_employee(
            company=cls.company,
            email="brandadmin@test.horilla",
            first_name="Brand",
            user=cls.user,
        )
        DynamicEmailConfiguration.objects.create(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            from_email="hr@example.com",
            use_tls=True,
            is_primary=True,
        )

    def test_password_reset(self):
        self.client.post(
            reverse("forgot-password"),
            {"email": self.user.username},
            HTTP_HOST="hr.punkraven.test",
        )

        [message] = mail.outbox
        _parsed, html = self.assert_branded(
            message,
            structure=(
                "multipart/alternative[text/plain, "
                "multipart/related[text/html, image/png]]"
            ),
        )
        self.assertIn(f"background-color: {BRAND_BLUE}", html)
        self.assertIn('alt="PunkRaven HR"', html)

    def test_invitation(self):
        from employee.methods.methods import send_employee_invitation

        self.assertTrue(
            send_employee_invitation(self.employee, "hr.punkraven.test", True)
        )

        [message] = mail.outbox
        _parsed, html = self.assert_branded(message)
        self.assertIn("Welcome to PunkRaven HR", html)
        self.assertIn("https://hr.punkraven.test/", html)
        self.assertIn("/accounts/reset/", message.body)

    def test_payslip(self):
        from payroll.threadings.mail import MailSendThread

        payslip = make_payslip(
            employee=self.employee,
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 30),
        )
        record = {"instances": [payslip], "count": 1}
        pdf = mock.Mock(content=b"%PDF-1.4 test")
        with mock.patch("payroll.threadings.mail.payslip_pdf", return_value=pdf):
            MailSendThread(a_request(self.user), {"1": record}, [payslip.pk]).run()

        [message] = mail.outbox
        parsed, html = self.assert_branded(
            message,
            structure=(
                "multipart/mixed[multipart/related[text/html, image/png], "
                "application/pdf]"
            ),
        )
        self.assertIn(f"background-color: {BRAND_BLUE}", html)
        [pdf_part] = list(parsed.iter_attachments())
        self.assertEqual(pdf_part.get_content(), b"%PDF-1.4 test")

    def test_onboarding_portal_mail(self):
        recruitment = make_recruitment(company=self.company)
        candidate = make_candidate(
            recruitment=recruitment, email="cand@test.horilla", name="Jordan Lee"
        )
        self.client.force_login(self.user)

        self.client.post(
            reverse("email-send"),
            {"ids": [candidate.pk]},
            HTTP_HOST="hr.punkraven.test",
        )

        [message] = mail.outbox
        _parsed, html = self.assert_branded(message)
        self.assertIn(f"background-color: {BRAND_BLUE}", html)

    def test_leave_request_mail(self):
        from leave.threading import LeaveMailSendThread

        thread = LeaveMailSendThread(a_request(self.user), None, "request")
        thread.send_email("Leave approved", "Enjoy", [self.employee], 7)

        [message] = mail.outbox
        parsed, html = self.assert_branded(message)
        self.assertIn(f"background-color: {BRAND_BLUE}", html)
        self.assertIn("leave_icon", inline_parts(parsed))

    def test_helpdesk_ticket_mail(self):
        from helpdesk.threading import TicketSendThread

        thread = TicketSendThread.__new__(TicketSendThread)
        thread.request = a_request(self.user)
        thread.host, thread.protocol = "hr.punkraven.test", "https"
        thread.send_email("Ticket raised", "A ticket", [self.employee], 7)

        [message] = mail.outbox
        parsed, html = self.assert_branded(message)
        self.assertIn(f"background-color: {BRAND_BLUE}", html)
        self.assertIn('src="cid:ticket_icon"', html)
        self.assertIn("ticket_icon", inline_parts(parsed))

    @override_settings(WHITE_LABELLING=True)
    def test_white_labelled_company_logo_stands_in_for_the_brand_logo(self):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
        icon = buffer.getvalue()
        self.company.icon.name = "base/icon/brand-co.png"
        self.company.save(update_fields=["icon"])

        with mock.patch("horilla.media.read_media", return_value=icon):
            self.client.post(
                reverse("forgot-password"),
                {"email": self.user.username},
                HTTP_HOST="hr.punkraven.test",
            )

        parsed = received(mail.outbox[0])
        logos = [
            part
            for part in parsed.walk()
            if part["Content-ID"] == f"<{BRAND_LOGO_CID}>"
        ]
        self.assertEqual(len(logos), 1)
        self.assertEqual(logos[0].get_payload(decode=True), icon)
        self.assertIn("Brand Co", html_of(parsed))

    @override_settings(WHITE_LABELLING=True)
    def test_a_company_icon_mail_cannot_show_keeps_the_brand_logo(self):
        self.company.icon.name = "base/icon/brand-co.svg"
        self.company.save(update_fields=["icon"])

        with mock.patch("horilla.media.read_media", return_value=b"<svg/>"):
            self.client.post(
                reverse("forgot-password"),
                {"email": self.user.username},
                HTTP_HOST="hr.punkraven.test",
            )

        logo = inline_parts(received(mail.outbox[0]))[BRAND_LOGO_CID]
        self.assertEqual(logo.get_content(), brand_logo_bytes())


class BrandLogoAttachmentTests(TestCase):
    def html_message(self, html):
        message = EmailMultiAlternatives("Hi", "text", "a@example.com", ["b@x.com"])
        message.attach_alternative(html, "text/html")
        return message

    def test_mail_that_does_not_show_the_logo_is_left_alone(self):
        message = EmailMessage("Hi", "Plain text", "a@example.com", ["b@x.com"])

        self.assertEqual(shape(received(message)), "text/plain")
        self.assertEqual(message.attachments, [])

    def test_building_the_message_twice_attaches_the_logo_once(self):
        message = self.html_message(f'<img src="cid:{BRAND_LOGO_CID}">')
        received(message)
        parsed = received(message)

        self.assertEqual(len(message.attachments), 1)
        self.assertEqual(len(inline_parts(parsed)), 1)

    def test_html_only_body_is_related_to_the_logo(self):
        message = EmailMessage(
            "Hi", f'<img src="cid:{BRAND_LOGO_CID}">', "a@x.com", ["b@x.com"]
        )
        message.content_subtype = "html"

        self.assertEqual(
            shape(received(message)), "multipart/related[text/html, image/png]"
        )


def resend_response(*args, **kwargs):
    response = mock.Mock(status_code=200, headers={})
    response.json.return_value = {"id": "email-id"}
    response.text = json.dumps({"id": "email-id"})
    return response


class ConfiguredBackendTransportTests(TestCase):
    def message(self):
        html = render_to_string(
            "base/mail_templates/password_reset_email.html",
            {
                "white_label_company_name": "PunkRaven HR",
                "reset_url": "https://hr.punkraven.test/reset/x/",
            },
        )
        message = EmailMultiAlternatives(
            "Hello", "text", "HR <hr@example.com>", ["someone@example.com"]
        )
        message.attach_alternative(html, "text/html")
        return message

    def test_resend_api_sends_the_logo_as_an_inline_attachment(self):
        DynamicEmailConfiguration.objects.create(
            transport=DynamicEmailConfiguration.RESEND_API,
            password="re_test_key",
            from_email="hr@example.com",
            is_primary=True,
        )
        with mock.patch(
            "requests.Session.request", side_effect=resend_response
        ) as request:
            sent = ConfiguredEmailBackend().send_messages([self.message()])

        self.assertEqual(sent, 1)
        payload = json.loads(request.call_args.kwargs["data"])
        self.assertIn(f'src="cid:{BRAND_LOGO_CID}"', payload["html"])
        [attachment] = payload["attachments"]
        self.assertEqual(attachment["content_id"], BRAND_LOGO_CID)
        self.assertEqual(attachment["filename"], "punkraven-mail-logo.png")
        self.assertEqual(attachment["content_type"], "image/png")
        self.assertEqual(base64.b64decode(attachment["content"]), brand_logo_bytes())

    def test_smtp_sends_the_logo_in_a_related_part(self):
        DynamicEmailConfiguration.objects.create(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            from_email="hr@example.com",
            use_tls=True,
            is_primary=True,
        )
        with mock.patch("smtplib.SMTP") as smtp:
            sent = ConfiguredEmailBackend().send_messages([self.message()])

        self.assertEqual(sent, 1)
        raw = smtp.return_value.sendmail.call_args.args[2]
        parsed = email.message_from_bytes(raw, policy=policy.default)
        self.assertEqual(
            shape(parsed),
            "multipart/alternative[text/plain, multipart/related[text/html, image/png]]",
        )
        logo = inline_parts(parsed)[BRAND_LOGO_CID]
        self.assertEqual(logo.get_content(), brand_logo_bytes())
        self.assertIn(f'src="cid:{BRAND_LOGO_CID}"', html_of(parsed))

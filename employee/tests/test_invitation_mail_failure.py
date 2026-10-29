"""A broken mail server must not break employee creation."""

from django.test import TestCase

from base.models import DynamicEmailConfiguration
from employee.methods.methods import send_employee_invitation
from horilla.testkit import make_company, make_employee


class InvitationMailFailureTests(TestCase):
    def test_invalid_mail_server_returns_false_instead_of_raising(self):
        # TLS and SSL both on makes Django's SMTP backend raise ValueError on
        # construction; written with update() to skip model validation, the
        # way such rows reach the database.
        config = DynamicEmailConfiguration.objects.create(
            host="smtp.example.com",
            port=465,
            username="mailer@example.com",
            password="secret",
            from_email="mailer@example.com",
            is_primary=True,
        )
        DynamicEmailConfiguration.objects.filter(pk=config.pk).update(
            use_tls=True, use_ssl=True
        )
        employee = make_employee(
            company=make_company("Mail Co"),
            email="invitee@test.horilla",
            first_name="In",
            last_name="Vitee",
        )

        self.assertFalse(send_employee_invitation(employee, "testserver", False))

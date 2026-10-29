"""Admins can set an employee's login username on create and change it on edit.

The login HorillaUser is created by Employee.save() with the email as its
username, and nothing in the employee forms exposed it, so the only way to
change it was the user's own change-username page.
"""

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from employee.models import Employee
from horilla.testkit import make_company, make_employee, make_user
from horilla_auth.models import HorillaUser


class EmployeeUsernameTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = make_company("Username Co")
        admin_user = make_user("hr-admin", is_superuser=True)
        cls.admin = make_employee(
            company=cls.company,
            email="hr-admin@test.horilla",
            first_name="Hr",
            user=admin_user,
        )
        cls.employee = make_employee(
            company=cls.company,
            email="jane.doe@test.horilla",
            first_name="Jane",
            last_name="Doe",
        )

    def _client(self, user):
        client = Client()
        client.force_login(user)
        session = client.session
        session["selected_company"] = str(self.company.pk)
        session.save()
        return client

    def _personal_data(self, employee=None, **extra):
        data = {
            "employee_first_name": "Jane",
            "employee_last_name": "Doe",
            "email": "jane.doe@test.horilla",
            "phone": "9111111111",
            "gender": "female",
            "marital_status": "single",
        }
        if employee is not None:
            data["badge_id"] = employee.badge_id or ""
        data.update(extra)
        return data

    def _create(self, client, **extra):
        data = self._personal_data(
            email="new.hire@test.horilla", employee_first_name="New", **extra
        )
        return client.post(reverse("employee-create-personal-info"), data)

    def _update(self, client, **extra):
        data = self._personal_data(self.employee, form="personal", **extra)
        return client.post(
            reverse("employee-view-update", kwargs={"obj_id": self.employee.pk}),
            data,
        )

    def _username(self, employee):
        employee.refresh_from_db()
        return HorillaUser.objects.get(pk=employee.employee_user_id_id).username

    def _errors(self, response):
        return response.context["form"].errors

    def _unlink_user(self):
        # An employee without a login, e.g. one whose link was lost. The old
        # user is renamed so the email is free as the default username.
        HorillaUser.objects.filter(pk=self.employee.employee_user_id_id).update(
            username="unlinked-old-login"
        )
        Employee.objects.entire().filter(pk=self.employee.pk).update(
            employee_user_id=None
        )

    # Create

    def test_create_with_username_uses_it(self):
        client = self._client(self.admin.employee_user_id)
        self._create(client, username="  new.hire  ")
        employee = Employee.objects.entire().get(email="new.hire@test.horilla")
        self.assertEqual(employee.employee_user_id.username, "new.hire")
        self.assertEqual(employee.employee_user_id.email, "new.hire@test.horilla")

    def test_create_without_username_defaults_to_email(self):
        client = self._client(self.admin.employee_user_id)
        self._create(client, username="")
        employee = Employee.objects.entire().get(email="new.hire@test.horilla")
        self.assertEqual(employee.employee_user_id.username, "new.hire@test.horilla")

    def test_create_with_taken_username_is_rejected(self):
        client = self._client(self.admin.employee_user_id)
        response = self._create(client, username="JANE.DOE@test.horilla")
        self.assertIn("username", self._errors(response))
        self.assertFalse(
            Employee.objects.entire().filter(email="new.hire@test.horilla").exists()
        )

    # Edit

    def test_edit_form_shows_current_username(self):
        client = self._client(self.admin.employee_user_id)
        response = client.get(
            reverse("employee-view-update", kwargs={"obj_id": self.employee.pk})
        )
        self.assertEqual(
            response.context["form"]["username"].value(), "jane.doe@test.horilla"
        )
        self.assertContains(response, 'name="username"')

    def test_edit_changes_username(self):
        client = self._client(self.admin.employee_user_id)
        self._update(client, username="jdoe")
        self.assertEqual(self._username(self.employee), "jdoe")

    def test_unchanged_own_username_passes(self):
        client = self._client(self.admin.employee_user_id)
        response = self._update(
            client, username="jane.doe@test.horilla", employee_last_name="Smith"
        )
        self.assertNotIn("username", self._errors(response))
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employee_last_name, "Smith")
        self.assertEqual(self._username(self.employee), "jane.doe@test.horilla")

    def test_case_only_change_of_own_username_passes(self):
        client = self._client(self.admin.employee_user_id)
        self._update(client, username="Jane.Doe@test.horilla")
        self.assertEqual(self._username(self.employee), "Jane.Doe@test.horilla")

    def test_duplicate_username_is_rejected_case_insensitively(self):
        client = self._client(self.admin.employee_user_id)
        response = self._update(client, username="HR-Admin")
        self.assertEqual(
            self._errors(response)["username"],
            ['The username "HR-Admin" is already taken.'],
        )
        self.assertEqual(self._username(self.employee), "jane.doe@test.horilla")

    def test_invalid_character_is_rejected(self):
        client = self._client(self.admin.employee_user_id)
        response = self._update(client, username="jane doe!")
        self.assertIn("username", self._errors(response))
        self.assertEqual(self._username(self.employee), "jane.doe@test.horilla")

    def test_blank_username_on_edit_is_rejected(self):
        client = self._client(self.admin.employee_user_id)
        response = self._update(client, username="   ")
        self.assertEqual(self._errors(response)["username"], ["Username is required."])
        self.assertEqual(self._username(self.employee), "jane.doe@test.horilla")

    def test_edit_without_username_posted_keeps_it(self):
        client = self._client(self.admin.employee_user_id)
        self._update(client, employee_last_name="Smith")
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employee_last_name, "Smith")
        self.assertEqual(self._username(self.employee), "jane.doe@test.horilla")

    # Permissions

    def test_user_without_permission_cannot_change_it(self):
        # A reporting manager reaches the edit view through manager_can_enter
        # without holding employee.change_employee.
        manager_user = make_user("manager")
        manager = make_employee(
            company=self.company,
            email="manager@test.horilla",
            first_name="Manager",
            user=manager_user,
        )
        manager_user.user_permissions.add(
            Permission.objects.get(codename="view_employee")
        )
        work_info = self.employee.employee_work_info
        work_info.reporting_manager_id = manager
        work_info.save()

        client = self._client(manager_user)
        response = client.get(
            reverse("employee-view-update", kwargs={"obj_id": self.employee.pk})
        )
        self.assertNotIn("username", response.context["form"].fields)
        self.assertNotContains(response, 'name="username"')

        self._update(client, username="hijacked")
        self.assertEqual(self._username(self.employee), "jane.doe@test.horilla")

    # Employee without a login

    def test_employee_without_user_gets_login_with_chosen_username(self):
        self._unlink_user()
        client = self._client(self.admin.employee_user_id)
        response = client.get(
            reverse("employee-view-update", kwargs={"obj_id": self.employee.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("username", response.context["form"].fields)

        self._update(client, username="jane")
        self.assertEqual(self._username(self.employee), "jane")

    def test_saving_employee_without_user_links_one_login(self):
        self._unlink_user()
        employee = Employee.objects.entire().get(pk=self.employee.pk)
        users_before = HorillaUser.objects.count()
        employee.save()
        employee.save()
        self.assertEqual(HorillaUser.objects.count(), users_before + 1)
        self.assertEqual(self._username(employee), "jane.doe@test.horilla")

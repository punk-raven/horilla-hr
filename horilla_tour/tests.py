"""
Tests for the horilla_tour engine.

Covers the things that matter for a multi-tenant SaaS: company isolation,
audience + page targeting, auto-start suppression after completion, and
permission gating of the admin CRUD. The seed migration ships two global
tours (getting-started, dashboard-overview) which the API tests rely on.
"""

import json

from django.test import Client, TestCase
from django.urls import reverse

from base.models import Company
from employee.models import Employee
from horilla.horilla_middlewares import set_selected_company
from horilla_auth.models import HorillaUser
from horilla_tour.models import Tour, TourPreference, TourProgress, TourStep


def make_company(name, address):
    return Company.objects.create(
        company=name,
        address=address,
        country="US",
        state="ST",
        city="City",
        zip="00000",
    )


def attach_employee(user, first="Test", last="User"):
    """CompanyMiddleware requires every logged-in user to have an employee."""
    return Employee.objects.create(
        employee_user_id=user,
        employee_first_name=first,
        employee_last_name=last,
        email=user.email or f"{user.username}@t.com",
        phone="1234567890",
        badge_id=f"B{user.id}",
    )


def make_tour(slug, **kwargs):
    defaults = dict(
        title=slug.replace("-", " ").title(),
        page_match="dashboard",
        match_type="url_name",
        audience="all",
        trigger="auto_once",
        is_published=True,
    )
    defaults.update(kwargs)
    tour = Tour.objects.create(slug=slug, **defaults)
    TourStep.objects.create(
        tour=tour, sequence=1, title="Step 1", description="Hello", side="over"
    )
    return tour


class TourApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = HorillaUser.objects.create_superuser(
            username="tour_admin", email="admin@t.com", password="pass12345"
        )
        attach_employee(self.admin, "Admin", "User")
        self.employee = HorillaUser.objects.create_user(
            username="tour_emp", email="emp@t.com", password="pass12345"
        )
        employee = attach_employee(self.employee, "Emp", "User")
        # A non-superuser only sees records of their companies (plus global
        # ones) once the company middleware scopes the request: give the
        # employee a home company, as every real employee has.
        from employee.models import EmployeeWorkInformation

        EmployeeWorkInformation.objects.update_or_create(
            employee_id=employee,
            defaults={"company_id": make_company("Tour Co", "addr-tour")},
        )
        self.active_url = reverse("tour-active")
        self.progress_url = reverse("tour-progress")

    def _active(self, page="dashboard"):
        resp = self.client.get(self.active_url, {"page": page})
        self.assertEqual(resp.status_code, 200)
        return {t["slug"]: t for t in json.loads(resp.content)["tours"]}

    def test_api_requires_login(self):
        resp = self.client.get(self.active_url, {"page": "dashboard"})
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_seeded_getting_started_autostarts_for_admin(self):
        self.client.force_login(self.admin)
        tours = self._active("dashboard")
        self.assertIn("getting-started", tours)
        self.assertTrue(tours["getting-started"]["auto_start"])
        self.assertGreater(len(tours["getting-started"]["steps"]), 0)

    def test_page_matching_excludes_other_pages(self):
        self.client.force_login(self.admin)
        tours = self._active(page="employee-view")
        self.assertNotIn("getting-started", tours)  # only matches 'dashboard'

    def test_audience_filtering(self):
        # admins-only tour must not reach a non-admin user; an 'all' tour must.
        make_tour("admins-only", audience="admins")
        self.client.force_login(self.employee)
        tours = self._active("dashboard")
        self.assertNotIn("admins-only", tours)
        self.assertNotIn("getting-started", tours)  # seeded tour is admins-only
        self.assertIn("dashboard-highlights", tours)  # seeded 'all' tour

    def test_unpublished_tour_hidden(self):
        make_tour("draft-tour", is_published=False)
        self.client.force_login(self.admin)
        self.assertNotIn("draft-tour", self._active("dashboard"))

    def test_progress_completion_suppresses_autostart(self):
        self.client.force_login(self.admin)
        gs = Tour.objects.get(slug="getting-started")
        # Initially auto-starts
        self.assertTrue(self._active("dashboard")["getting-started"]["auto_start"])
        # Record completion
        resp = self.client.post(
            self.progress_url,
            {"tour_id": gs.id, "status": "completed", "last_step": 5},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.content)["ok"])
        self.assertTrue(
            TourProgress.objects.filter(
                tour=gs, user=self.admin, status="completed"
            ).exists()
        )
        # No longer auto-starts, but still listed for replay
        again = self._active("dashboard")["getting-started"]
        self.assertFalse(again["auto_start"])
        self.assertEqual(again["status"], "completed")

    def test_skip_suppresses_autostart(self):
        self.client.force_login(self.admin)
        gs = Tour.objects.get(slug="getting-started")
        self.client.post(self.progress_url, {"tour_id": gs.id, "status": "skipped"})
        again = self._active("dashboard")["getting-started"]
        self.assertFalse(again["auto_start"])
        self.assertEqual(again["status"], "skipped")

    def test_tour_left_mid_way_does_not_autostart_again(self):
        # The controller records "in_progress" as the tour starts. A user who
        # then navigates away (reload, link) has seen it: it must not start by
        # itself on every later page load until finished.
        self.client.force_login(self.admin)
        gs = Tour.objects.get(slug="getting-started")
        self.client.post(
            self.progress_url,
            {"tour_id": gs.id, "status": "in_progress", "last_step": 0},
        )
        again = self._active("dashboard")["getting-started"]
        self.assertFalse(again["auto_start"])
        self.assertEqual(again["status"], "in_progress")
        # Still listed (with its steps) so the Help launcher can replay it.
        self.assertGreater(len(again["steps"]), 0)

    def test_autostart_is_per_tour(self):
        # Dismissing one page's tour leaves the others to auto-start once.
        self.client.force_login(self.admin)
        gs = Tour.objects.get(slug="getting-started")
        other = make_tour("other-dashboard-tour")
        self.client.post(self.progress_url, {"tour_id": gs.id, "status": "skipped"})
        tours = self._active("dashboard")
        self.assertFalse(tours["getting-started"]["auto_start"])
        self.assertTrue(tours[other.slug]["auto_start"])

    def test_autostart_is_per_user(self):
        tour = make_tour("everyone-tour")
        self.client.force_login(self.admin)
        self.client.post(self.progress_url, {"tour_id": tour.id, "status": "skipped"})
        self.client.force_login(self.employee)
        self.assertTrue(self._active("dashboard")["everyone-tour"]["auto_start"])

    def test_late_start_report_does_not_reopen_a_dismissed_tour(self):
        # The "started" and "closed" requests can arrive out of order.
        self.client.force_login(self.admin)
        gs = Tour.objects.get(slug="getting-started")
        self.client.post(self.progress_url, {"tour_id": gs.id, "status": "skipped"})
        resp = self.client.post(
            self.progress_url,
            {"tour_id": gs.id, "status": "in_progress", "last_step": 0},
        )
        self.assertEqual(json.loads(resp.content)["status"], "skipped")
        self.assertEqual(
            TourProgress.objects.get(tour=gs, user=self.admin).status, "skipped"
        )

    def test_pending_flag_ignores_tours_already_shown(self):
        from types import SimpleNamespace

        from horilla_tour.context_processors import pending_tours_flag

        Tour.objects.exclude(slug="getting-started").update(is_published=False)
        gs = Tour.objects.get(slug="getting-started")
        request = SimpleNamespace(user=self.admin)
        self.assertTrue(pending_tours_flag(request)["tour_has_pending"])
        TourProgress.objects.create(tour=gs, user=self.admin, status="in_progress")
        self.assertFalse(pending_tours_flag(request)["tour_has_pending"])


class TourPreferenceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = HorillaUser.objects.create_superuser(
            username="pref_admin", email="pref@t.com", password="pass12345"
        )
        attach_employee(self.admin, "Pref", "Admin")
        self.active_url = reverse("tour-active")
        self.preference_url = reverse("tour-preference")

    def _active(self):
        resp = self.client.get(self.active_url, {"page": "dashboard"})
        self.assertEqual(resp.status_code, 200)
        return json.loads(resp.content)

    def _auto_started(self, data):
        return [t["slug"] for t in data["tours"] if t["auto_start"]]

    def test_preference_off_tour_autostarts(self):
        self.client.force_login(self.admin)
        data = self._active()
        self.assertFalse(data["auto_start_disabled"])
        self.assertIn("getting-started", self._auto_started(data))

    def test_preference_on_no_tour_autostarts_but_still_listed(self):
        TourPreference.objects.create(user=self.admin, auto_start_disabled=True)
        self.client.force_login(self.admin)
        data = self._active()
        self.assertTrue(data["auto_start_disabled"])
        self.assertEqual(self._auto_started(data), [])
        # Still offered in the Help launcher for a manual start.
        self.assertIn("getting-started", [t["slug"] for t in data["tours"]])

    def test_toggle_off_then_back_on(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.preference_url, {"auto_start_disabled": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.content)["auto_start_disabled"])
        self.assertTrue(TourPreference.auto_start_disabled_for(self.admin))
        self.assertEqual(self._auto_started(self._active()), [])

        resp = self.client.post(self.preference_url, {"auto_start_disabled": "0"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(json.loads(resp.content)["auto_start_disabled"])
        self.assertFalse(TourPreference.auto_start_disabled_for(self.admin))
        self.assertIn("getting-started", self._auto_started(self._active()))
        self.assertEqual(TourPreference.objects.filter(user=self.admin).count(), 1)

    def test_preference_is_per_user(self):
        other = HorillaUser.objects.create_superuser(
            username="pref_other", email="other@t.com", password="pass12345"
        )
        attach_employee(other, "Other", "Admin")
        TourPreference.objects.create(user=other, auto_start_disabled=True)
        self.client.force_login(self.admin)
        self.assertIn("getting-started", self._auto_started(self._active()))

    def test_toggle_requires_login(self):
        resp = self.client.post(self.preference_url, {"auto_start_disabled": "1"})
        self.assertIn(resp.status_code, (302, 401, 403))
        self.assertFalse(TourPreference.objects.exists())

    def test_toggle_rejects_get(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.preference_url, {"auto_start_disabled": "1"})
        self.assertEqual(resp.status_code, 405)
        self.assertFalse(TourPreference.objects.exists())

    def test_toggle_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        resp = client.post(self.preference_url, {"auto_start_disabled": "1"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(TourPreference.objects.exists())

    def test_context_flag_hides_pending_dot(self):
        from django.test import RequestFactory

        from horilla_tour.context_processors import pending_tours_flag

        request = RequestFactory().get("/")
        request.user = self.admin
        self.assertTrue(pending_tours_flag(request)["tour_has_pending"])
        TourPreference.objects.create(user=self.admin, auto_start_disabled=True)
        flags = pending_tours_flag(request)
        self.assertFalse(flags["tour_has_pending"])
        self.assertTrue(flags["tour_auto_start_disabled"])
        self.assertTrue(flags["tour_launcher_enabled"])

    def test_footer_scripts_expose_preference_config(self):
        from django.template import Context
        from django.template.loader import get_template
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = self.admin
        # Plain Context: skip the global context processors (they need a session).
        template = get_template("horilla_theme/components/footer_scripts.html")
        html = template.template.render(Context({"request": request}))
        self.assertIn(f'preferenceUrl: "{self.preference_url}"', html)
        self.assertIn("Don\\u0027t show tours again", html)
        self.assertIn("tourController.js?v=3", html)

    def test_opt_out_controls_are_styled_for_both_themes(self):
        # The opt-out link and the launcher's checkbox used to carry inline
        # light-only colours (grey text and a near-white rule on the dark
        # popover and panel). Their looks live in ux.css, with dark variants.
        from django.contrib.staticfiles import finders

        with open(finders.find("build/js/tourController.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("'hx-tour-panel__pref'", js)
        self.assertIn("'horilla-tour-optout__link'", js)
        self.assertNotIn("style.cssText", js)
        with open(
            finders.find("horilla_theme/assets/css/ux.css"), encoding="utf-8"
        ) as f:
            css = f.read()
        for selector in (
            ".dark .hx-tour-panel__pref",
            ".dark .horilla-tour-optout__link",
            ".dark .horilla-tour-optout",
        ):
            self.assertIn(selector, css)


class TourPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.regular = HorillaUser.objects.create_user(
            username="plain_user", email="p@t.com", password="pass12345"
        )
        attach_employee(self.regular, "Plain", "User")

    def test_settings_page_denied_for_non_privileged_user(self):
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("tour-section"))
        # permission_required denies (redirect or 403) — never a 200 settings page
        self.assertNotEqual(resp.status_code, 200)


MODULE_TOUR_SLUGS = [
    "employee-directory",
    "ess-dashboard-tour",
    "recruitment-pipeline",
    "leave-management",
    "attendance-tracking",
    "payroll-overview",
    "asset-management",
    "performance-management",
    "onboarding-pipeline",
    "offboarding-process",
    "project-management",
    "helpdesk-overview",
]

MODULE_PAGE_MATCHES = {
    "employee-directory": "employee-view",
    "ess-dashboard-tour": "ess-dashboard",
    "recruitment-pipeline": "cbv-pipeline",
    "leave-management": "leave-dashboard",
    "attendance-tracking": "attendance-dashboard",
    "payroll-overview": "view-payroll-dashboard",
    "asset-management": "asset-dashboard",
    "performance-management": "dashboard-view",
    "onboarding-pipeline": "onboarding-dashboard",
    "offboarding-process": "offboarding-dashboard",
    "project-management": "project-dashboard-view",
    "helpdesk-overview": "helpdesk-dashboard",
}

VALID_AUDIENCES = {"all", "admins", "managers", "employees"}


class TourModuleSeedTests(TestCase):
    """Verify migration 0004 seeded all 12 module tours correctly."""

    def test_all_slugs_present_and_published(self):
        for slug in MODULE_TOUR_SLUGS:
            with self.subTest(slug=slug):
                tour = Tour.objects.filter(slug=slug).first()
                self.assertIsNotNone(tour, f"Tour '{slug}' not found in DB")
                self.assertTrue(tour.is_published, f"Tour '{slug}' is not published")

    def test_each_tour_has_minimum_steps(self):
        for slug in MODULE_TOUR_SLUGS:
            with self.subTest(slug=slug):
                tour = Tour.objects.filter(slug=slug).first()
                if tour is None:
                    continue
                step_count = TourStep.objects.filter(tour=tour).count()
                self.assertGreaterEqual(
                    step_count,
                    4,
                    f"Tour '{slug}' has only {step_count} steps (need ≥ 4)",
                )

    def test_page_match_values_are_correct(self):
        for slug, expected_page in MODULE_PAGE_MATCHES.items():
            with self.subTest(slug=slug):
                tour = Tour.objects.filter(slug=slug).first()
                if tour is None:
                    continue
                self.assertEqual(
                    tour.page_match,
                    expected_page,
                    f"Tour '{slug}': expected page_match '{expected_page}', got '{tour.page_match}'",
                )

    def test_audience_values_are_valid(self):
        for slug in MODULE_TOUR_SLUGS:
            with self.subTest(slug=slug):
                tour = Tour.objects.filter(slug=slug).first()
                if tour is None:
                    continue
                self.assertIn(
                    tour.audience,
                    VALID_AUDIENCES,
                    f"Tour '{slug}' has invalid audience '{tour.audience}'",
                )

    def test_trigger_is_auto_once_for_all_module_tours(self):
        for slug in MODULE_TOUR_SLUGS:
            with self.subTest(slug=slug):
                tour = Tour.objects.filter(slug=slug).first()
                if tour is None:
                    continue
                self.assertEqual(
                    tour.trigger,
                    "auto_once",
                    f"Tour '{slug}' should be auto_once but is '{tour.trigger}'",
                )

    def test_no_duplicate_slugs(self):
        from django.db.models import Count

        duplicates = (
            Tour.objects.values("slug").annotate(cnt=Count("id")).filter(cnt__gt=1)
        )
        dup_slugs = [d["slug"] for d in duplicates]
        self.assertEqual(dup_slugs, [], f"Duplicate tour slugs found: {dup_slugs}")

    def test_global_tours_have_no_company(self):
        """Module tours are seeded globally (company_id=None) so every tenant sees them."""
        for slug in MODULE_TOUR_SLUGS:
            with self.subTest(slug=slug):
                tour = Tour.objects.filter(slug=slug).first()
                if tour is None:
                    continue
                self.assertIsNone(
                    tour.company_id_id,
                    f"Tour '{slug}' should be global (company_id=None) but has company_id={tour.company_id_id}",
                )


class TourCompanyIsolationTests(TestCase):
    def setUp(self):
        # Clear any thread-local request left over from prior tests so
        # HorillaModel.save() doesn't stamp modified_by with a stale user id.
        from horilla.horilla_middlewares import _thread_locals

        _thread_locals.request = None

    def tearDown(self):
        set_selected_company(None)

    def test_manager_scopes_to_selected_company_plus_global(self):
        company_a = make_company("Alpha", "addr-a")
        company_b = make_company("Beta", "addr-b")
        make_tour("alpha-tour", company_id=company_a)
        make_tour("beta-tour", company_id=company_b)

        set_selected_company(company_a.id)
        slugs_a = set(Tour.objects.values_list("slug", flat=True))
        self.assertIn("alpha-tour", slugs_a)
        self.assertNotIn("beta-tour", slugs_a)
        self.assertIn("getting-started", slugs_a)  # global tour visible everywhere

        set_selected_company(company_b.id)
        slugs_b = set(Tour.objects.values_list("slug", flat=True))
        self.assertIn("beta-tour", slugs_b)
        self.assertNotIn("alpha-tour", slugs_b)
        self.assertIn("getting-started", slugs_b)

"""Breadcrumb helper smoke tests."""

from django.test import SimpleTestCase

from horilla_crumbs.context_processors import (
    _resolve_menu_section,
    crumb_name,
    is_valid_uuid,
)


class UuidHelperTests(SimpleTestCase):
    def test_valid_uuid4(self):
        self.assertTrue(is_valid_uuid("550e8400-e29b-41d4-a716-446655440000"))

    def test_invalid_uuid(self):
        self.assertFalse(is_valid_uuid("not-a-uuid"))


class ResolveMenuSectionTests(SimpleTestCase):
    def test_longest_submenu_match(self):
        menus = [
            {
                "menu": "People",
                "submenu": [
                    {"redirect": "/employee/"},
                    {"redirect": "/employee/list/"},
                ],
            }
        ]
        result = _resolve_menu_section("/employee/list/detail/", menus)
        self.assertEqual(result, ("People", "/employee/list/"))


class CrumbNameTests(SimpleTestCase):
    def test_global_settings_is_not_labelled_payroll(self):
        self.assertEqual(crumb_name(["settings", "mail-server-conf"], 0), "Settings")

    def test_payroll_settings_keeps_its_label(self):
        self.assertEqual(crumb_name(["payroll", "settings"], 1), "Payroll Settings")

    def test_unmapped_segment_falls_through(self):
        self.assertEqual(crumb_name(["x", "unknown-slug"], 1), "unknown-slug")

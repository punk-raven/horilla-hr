"""with_dot / without_dot split a view's row_status_indications. A view that
defines none passes None; the Document Requests pipeline did, and its whole
list failed to render (TypeError: 'NoneType' object is not iterable)."""

from django.test import SimpleTestCase

from horilla_views.templatetags.generic_template_filters import with_dot, without_dot


class StatusIndicationFilterTests(SimpleTestCase):
    def test_none_means_no_indications(self):
        self.assertEqual(with_dot(None), [])
        self.assertEqual(without_dot(None), [])

    def test_splits_on_whether_a_dot_class_is_set(self):
        indications = [("approved--dot", "Approved", "a"), ("", "Today", "b")]
        self.assertEqual(with_dot(indications), [indications[0]])
        self.assertEqual(without_dot(indications), [indications[1]])


class EmptyCellValueTests(SimpleTestCase):
    """An unset field used to render as the literal word "None" in every
    generic list cell (e.g. the Host of a Resend API mail server)."""

    def test_none_renders_the_empty_placeholder(self):
        from horilla_views.templatetags.generic_template_filters import (
            EMPTY_VALUE,
            selected_format,
        )

        self.assertEqual(selected_format(None), EMPTY_VALUE)
        self.assertEqual(selected_format(None, company=object()), EMPTY_VALUE)

    def test_set_values_pass_through(self):
        from horilla_views.templatetags.generic_template_filters import selected_format

        self.assertEqual(selected_format("smtp.example.com"), "smtp.example.com")
        self.assertEqual(selected_format(""), "")
        self.assertEqual(selected_format(0), 0)

    def test_placeholder_gets_no_tooltip(self):
        from horilla_views.templatetags.generic_template_filters import (
            EMPTY_VALUE,
            cell_tooltip,
        )

        self.assertEqual(cell_tooltip(EMPTY_VALUE), "")
        self.assertEqual(cell_tooltip(None), "")
        self.assertEqual(cell_tooltip("<b>Host</b>"), "Host")


class TimeCellValueTests(SimpleTestCase):
    """A manual check-in/out is stamped from datetime.now(), so without a
    company time format its cell used to read "15:10:17.451000"."""

    def test_unformatted_time_drops_microseconds(self):
        import datetime

        from horilla_views.templatetags.generic_template_filters import selected_format

        value = selected_format(datetime.time(15, 10, 17, 451000))
        self.assertEqual(str(value), "15:10:17")
        self.assertEqual(str(selected_format(datetime.time(15, 10))), "15:10:00")

    def test_company_time_format_still_applies(self):
        import datetime
        from types import SimpleNamespace

        from horilla_views.templatetags.generic_template_filters import selected_format

        company = SimpleNamespace(date_format=None, time_format="HH:mm")
        self.assertEqual(
            selected_format(datetime.time(15, 10, 17, 451000), company), "15:10"
        )

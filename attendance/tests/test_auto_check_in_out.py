"""Tests for automatic check-in and check-out (attendance/methods/auto_attendance.py)."""

from datetime import date, datetime, time, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.methods.auto_attendance import auto_check_in, auto_check_out
from attendance.models import (
    Attendance,
    AttendanceActivity,
    AttendanceGeneralSetting,
    AttendanceLateComeEarlyOut,
    AutoCheckInLog,
)
from attendance.views.clock_in_out import employee_clock_in, employee_clock_out
from base.models import (
    CompanyLeaves,
    EmployeeShift,
    EmployeeShiftDay,
    EmployeeShiftSchedule,
    Holidays,
)
from horilla.testkit import make_company, make_employee, make_user
from horilla.testkit.company import clear_selected_company


def _past_monday():
    """A Monday far enough back that attendance dates are never in the future."""
    day = date.today() - timedelta(days=35)
    while day.weekday() != 0:
        day -= timedelta(days=1)
    return day


MONDAY = _past_monday()
TUESDAY = MONDAY + timedelta(days=1)


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class AutoAttendanceTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        clear_selected_company()
        cls.company = make_company("Auto Co")
        cls.monday = EmployeeShiftDay.objects.get(day="monday")

        cls.day_shift = EmployeeShift.objects.create(employee_shift="Auto Day")
        cls.day_shift.company_id.add(cls.company)
        # Monday only: every other weekday is the shift's weekly off.
        cls.day_schedule = EmployeeShiftSchedule.objects.create(
            day=cls.monday,
            shift_id=cls.day_shift,
            minimum_working_hour="08:00",
            start_time=time(9, 0),
            end_time=time(17, 0),
        )

        cls.night_shift = EmployeeShift.objects.create(employee_shift="Auto Night")
        cls.night_shift.company_id.add(cls.company)
        cls.night_schedule = EmployeeShiftSchedule.objects.create(
            day=cls.monday,
            shift_id=cls.night_shift,
            minimum_working_hour="08:00",
            start_time=time(22, 0),
            end_time=time(6, 0),
            is_night_shift=True,
        )

        cls.employee = make_employee(
            company=cls.company,
            email="auto.day@test.horilla",
            first_name="Ada",
            shift=cls.day_shift,
            date_joining=MONDAY - timedelta(days=365),
        )

    def setUp(self):
        clear_selected_company()

    def tearDown(self):
        clear_selected_company()
        super().tearDown()

    def make_employee(self, email, shift=None, company=None, **kwargs):
        return make_employee(
            company=company or self.company,
            email=email,
            shift=shift or self.day_shift,
            date_joining=kwargs.pop("date_joining", MONDAY - timedelta(days=365)),
            **kwargs,
        )

    def set_setting(self, enabled, company=None):
        AttendanceGeneralSetting.objects.entire().update_or_create(
            company_id=company, defaults={"auto_check_in_out": enabled}
        )

    def attendance(self, employee=None, day=MONDAY):
        return Attendance.objects.entire().filter(
            employee_id=employee or self.employee, attendance_date=day
        )

    def activities(self, employee=None):
        return AttendanceActivity.objects.entire().filter(
            employee_id=employee or self.employee
        )


class AutoCheckInTests(AutoAttendanceTestBase):
    def test_setting_defaults_to_on(self):
        self.assertTrue(AttendanceGeneralSetting.auto_check_in_out_enabled(None))
        self.assertTrue(
            AttendanceGeneralSetting.auto_check_in_out_enabled(self.company)
        )

    def test_checks_in_at_shift_start(self):
        self.assertEqual(auto_check_in(at(MONDAY, 10, 30)), 1)

        attendance = self.attendance().get()
        self.assertEqual(attendance.shift_id, self.day_shift)
        self.assertEqual(attendance.attendance_day, self.monday)
        self.assertEqual(attendance.attendance_clock_in, time(9, 0))
        self.assertEqual(attendance.attendance_clock_in_date, MONDAY)
        self.assertIsNone(attendance.attendance_clock_out)
        self.assertEqual(attendance.minimum_hour, "08:00")

        activity = self.activities().get()
        self.assertEqual(activity.clock_in, time(9, 0))
        self.assertEqual(activity.clock_in_date, MONDAY)
        self.assertTrue(activity.auto_clock_in)
        self.assertFalse(activity.auto_clock_out)
        self.assertEqual(activity.entry_mode(), "Auto in")

        # Checked in exactly at the shift start: not a late come.
        self.assertFalse(
            AttendanceLateComeEarlyOut.objects.filter(attendance_id=attendance).exists()
        )
        self.assertTrue(
            AutoCheckInLog.objects.filter(
                employee_id=self.employee, attendance_date=MONDAY
            ).exists()
        )

    def test_does_nothing_before_the_shift_starts(self):
        self.assertEqual(auto_check_in(at(MONDAY, 8, 55)), 0)
        self.assertFalse(self.attendance().exists())

    def test_does_not_check_in_once_the_shift_is_over(self):
        # A shift missed entirely (scheduler down all day) is not backfilled.
        self.assertEqual(auto_check_in(at(MONDAY, 17, 0)), 0)
        self.assertFalse(self.attendance().exists())

    def test_is_idempotent(self):
        auto_check_in(at(MONDAY, 9, 0))
        auto_check_in(at(MONDAY, 9, 5))
        auto_check_in(at(MONDAY, 12, 0))
        self.assertEqual(self.attendance().count(), 1)
        self.assertEqual(self.activities().count(), 1)

    def test_deleted_attendance_is_not_recreated(self):
        auto_check_in(at(MONDAY, 9, 5))
        self.activities().delete()
        self.attendance().delete()

        self.assertEqual(auto_check_in(at(MONDAY, 9, 10)), 0)
        self.assertFalse(self.attendance().exists())

    def test_skips_employee_already_checked_in(self):
        employee_clock_in(
            employee=self.employee,
            shift=self.day_shift,
            date_today=MONDAY,
            now="08:50",
            in_datetime=at(MONDAY, 8, 50),
        )
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 0)
        activity = self.activities().get()
        self.assertEqual(activity.clock_in, time(8, 50))
        self.assertFalse(activity.auto_clock_in)

    def test_skips_employee_who_already_checked_out_for_the_day(self):
        employee_clock_in(
            employee=self.employee,
            shift=self.day_shift,
            date_today=MONDAY,
            now="07:00",
            in_datetime=at(MONDAY, 7, 0),
        )
        employee_clock_out(
            employee=self.employee,
            shift=self.day_shift,
            date_today=MONDAY,
            now="08:00",
            out_datetime=at(MONDAY, 8, 0),
        )
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 0)
        self.assertEqual(self.activities().count(), 1)

    def test_skips_inactive_employee(self):
        self.employee.is_active = False
        self.employee.save()
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 0)

    def test_skips_employee_without_a_shift(self):
        employee = self.make_employee("no.shift@test.horilla")
        employee.employee_work_info.shift_id = None
        employee.employee_work_info.save()
        auto_check_in(at(MONDAY, 9, 5))
        self.assertFalse(self.attendance(employee).exists())

    def test_skips_employee_not_yet_joined(self):
        employee = self.make_employee(
            "joiner@test.horilla", date_joining=MONDAY + timedelta(days=1)
        )
        auto_check_in(at(MONDAY, 9, 5))
        self.assertFalse(self.attendance(employee).exists())
        self.assertTrue(self.attendance().exists())

    def test_skips_shift_weekly_off_day(self):
        # The day shift has no Tuesday schedule.
        self.assertEqual(auto_check_in(at(TUESDAY, 10, 0)), 0)
        self.assertFalse(self.attendance(day=TUESDAY).exists())

    def test_skips_company_weekly_off_day(self):
        CompanyLeaves.objects.create(
            based_on_week=None, based_on_week_day="0"
        ).company_id.add(self.company)
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 0)

    def test_another_companys_weekly_off_does_not_apply(self):
        other = make_company("Other Co", hq=False)
        CompanyLeaves.objects.create(
            based_on_week=None, based_on_week_day="0"
        ).company_id.add(other)
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 1)

    def test_skips_holiday(self):
        Holidays.objects.create(
            name="Auto Holiday",
            start_date=MONDAY,
            end_date=MONDAY,
            company_id=self.company,
        )
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 0)

    def test_another_companys_holiday_does_not_apply(self):
        Holidays.objects.create(
            name="Other Holiday",
            start_date=MONDAY,
            end_date=MONDAY,
            company_id=make_company("Other Co", hq=False),
        )
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 1)

    def test_skips_approved_full_day_leave(self):
        self._leave(status="approved")
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 0)

    def test_checks_in_on_half_day_or_unapproved_leave(self):
        half_day = self.make_employee("half.day@test.horilla")
        requested = self.make_employee("requested@test.horilla")
        self._leave(employee=half_day, breakdown="first_half")
        self._leave(employee=requested, status="requested")
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 3)

    def test_skips_published_roster_day_off(self):
        from base.models import Department, Roster

        department = Department.objects.create(department="Auto Dept")
        Roster.objects.create(
            employee=self.employee,
            date=MONDAY,
            department=department,
            is_off=True,
            is_published=True,
        )
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 0)

    def _leave(self, employee=None, status="approved", breakdown="full_day"):
        from leave.models import LeaveRequest, LeaveType

        leave_type, _ = LeaveType.objects.get_or_create(name="Auto Leave")
        # bulk_create: LeaveRequest.save() runs balance checks irrelevant here.
        LeaveRequest.objects.bulk_create(
            [
                LeaveRequest(
                    employee_id=employee or self.employee,
                    leave_type_id=leave_type,
                    start_date=MONDAY,
                    end_date=MONDAY,
                    start_date_breakdown=breakdown,
                    end_date_breakdown=breakdown,
                    requested_days=1 if breakdown == "full_day" else 0.5,
                    description="Test",
                    status=status,
                )
            ]
        )


class AutoCheckOutTests(AutoAttendanceTestBase):
    def test_checks_out_at_shift_end(self):
        auto_check_in(at(MONDAY, 9, 5))
        self.assertEqual(auto_check_out(at(MONDAY, 16, 55)), 0)
        self.assertEqual(auto_check_out(at(MONDAY, 17, 5)), 1)

        attendance = self.attendance().get()
        self.assertEqual(attendance.attendance_clock_out, time(17, 0))
        self.assertEqual(attendance.attendance_clock_out_date, MONDAY)
        self.assertEqual(attendance.attendance_worked_hour, "08:00")
        self.assertEqual(attendance.attendance_overtime, "00:00")
        self.assertFalse(
            AttendanceLateComeEarlyOut.objects.filter(attendance_id=attendance).exists()
        )

        activity = self.activities().get()
        self.assertEqual(activity.clock_out, time(17, 0))
        self.assertTrue(activity.auto_clock_out)
        self.assertEqual(activity.entry_mode(), "Automatic")
        self.assertEqual(attendance.entry_mode(), "Automatic")

    def test_is_idempotent(self):
        auto_check_in(at(MONDAY, 9, 5))
        auto_check_out(at(MONDAY, 17, 5))
        self.assertEqual(auto_check_out(at(MONDAY, 17, 10)), 0)
        self.assertEqual(auto_check_in(at(MONDAY, 16, 0)), 0)
        self.assertEqual(self.activities().count(), 1)

    def test_checks_out_manual_check_in(self):
        employee_clock_in(
            employee=self.employee,
            shift=self.day_shift,
            date_today=MONDAY,
            now="09:30",
            in_datetime=at(MONDAY, 9, 30),
        )
        self.assertEqual(auto_check_out(at(MONDAY, 18, 0)), 1)
        attendance = self.attendance().get()
        self.assertEqual(attendance.attendance_clock_out, time(17, 0))
        self.assertEqual(attendance.attendance_worked_hour, "07:30")
        activity = self.activities().get()
        self.assertFalse(activity.auto_clock_in)
        self.assertTrue(activity.auto_clock_out)
        self.assertEqual(activity.entry_mode(), "Auto out")

    def test_leaves_overtime_started_after_shift_end_open(self):
        employee_clock_in(
            employee=self.employee,
            shift=self.day_shift,
            date_today=MONDAY,
            now="17:30",
            in_datetime=at(MONDAY, 17, 30),
        )
        self.assertEqual(auto_check_out(at(MONDAY, 19, 0)), 0)
        self.assertIsNone(self.activities().get().clock_out)

    def test_uses_per_shift_automatic_check_out_time(self):
        self.day_schedule.is_auto_punch_out_enabled = True
        self.day_schedule.auto_punch_out_time = time(18, 0)
        self.day_schedule.save()
        auto_check_in(at(MONDAY, 9, 5))
        self.assertEqual(auto_check_out(at(MONDAY, 17, 30)), 0)
        self.assertEqual(auto_check_out(at(MONDAY, 18, 0)), 1)
        self.assertEqual(self.attendance().get().attendance_worked_hour, "09:00")

    def test_does_not_rewrite_old_open_attendance(self):
        employee_clock_in(
            employee=self.employee,
            shift=self.day_shift,
            date_today=MONDAY,
            now="09:00",
            in_datetime=at(MONDAY, 9, 0),
        )
        self.assertEqual(auto_check_out(at(MONDAY + timedelta(days=3), 10, 0)), 0)


class NightShiftTests(AutoAttendanceTestBase):
    def setUp(self):
        super().setUp()
        self.night_worker = self.make_employee(
            "night@test.horilla", shift=self.night_shift
        )

    def test_night_shift_crosses_midnight(self):
        self.assertEqual(auto_check_in(at(MONDAY, 21, 55)), 0)
        self.assertFalse(self.attendance(self.night_worker).exists())

        auto_check_in(at(MONDAY, 22, 5))
        attendance = self.attendance(self.night_worker).get()
        self.assertEqual(attendance.attendance_clock_in, time(22, 0))
        self.assertEqual(attendance.attendance_day, self.monday)

        self.assertEqual(auto_check_out(at(TUESDAY, 5, 55)), 0)
        self.assertIsNone(self.attendance(self.night_worker).get().attendance_clock_out)

        self.assertEqual(auto_check_out(at(TUESDAY, 6, 5)), 1)
        attendance = self.attendance(self.night_worker).get()
        self.assertEqual(attendance.attendance_clock_out, time(6, 0))
        self.assertEqual(attendance.attendance_clock_out_date, TUESDAY)
        self.assertEqual(attendance.attendance_worked_hour, "08:00")
        self.assertFalse(
            AttendanceLateComeEarlyOut.objects.filter(attendance_id=attendance).exists()
        )

    def test_catches_up_after_midnight(self):
        # The scheduler was down from before 22:00 until 01:00: the check-in
        # still lands on Monday's attendance at the shift start.
        auto_check_in(at(TUESDAY, 1, 0))
        attendance = self.attendance(self.night_worker).get()
        self.assertEqual(attendance.attendance_clock_in, time(22, 0))
        self.assertEqual(attendance.attendance_clock_in_date, MONDAY)
        self.assertFalse(self.attendance(self.night_worker, TUESDAY).exists())


class SettingTests(AutoAttendanceTestBase):
    def test_setting_off_stops_check_in_and_check_out(self):
        self.set_setting(False, self.company)
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 0)

        employee_clock_in(
            employee=self.employee,
            shift=self.day_shift,
            date_today=MONDAY,
            now="09:00",
            in_datetime=at(MONDAY, 9, 0),
        )
        self.assertEqual(auto_check_out(at(MONDAY, 17, 5)), 0)

    def test_per_shift_automatic_check_out_works_with_setting_off(self):
        self.set_setting(False, self.company)
        self.day_schedule.is_auto_punch_out_enabled = True
        self.day_schedule.auto_punch_out_time = time(17, 30)
        self.day_schedule.save()
        employee_clock_in(
            employee=self.employee,
            shift=self.day_shift,
            date_today=MONDAY,
            now="09:00",
            in_datetime=at(MONDAY, 9, 0),
        )
        self.assertEqual(auto_check_out(at(MONDAY, 17, 35)), 1)
        self.assertEqual(self.activities().get().clock_out, time(17, 30))

    def test_setting_is_per_company(self):
        other = make_company("Other Co", hq=False)
        other_employee = self.make_employee("other@test.horilla", company=other)
        self.set_setting(False, other)
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 1)
        self.assertTrue(self.attendance().exists())
        self.assertFalse(self.attendance(other_employee).exists())

    def test_company_without_its_own_row_follows_the_global_setting(self):
        AttendanceGeneralSetting.objects.entire().filter(
            company_id=self.company
        ).delete()
        self.set_setting(False, None)
        self.assertEqual(auto_check_in(at(MONDAY, 9, 5)), 0)

    def _admin_client(self, selected_company):
        user = make_user("auto.admin", is_superuser=True)
        # login_required turns away a user with no employee profile.
        self.make_employee("auto.admin@test.horilla", user=user)
        client = Client()
        client.force_login(user)
        session = client.session
        session["selected_company"] = selected_company
        session.save()
        return client

    def test_toggle_view(self):
        client = self._admin_client(str(self.company.pk))
        url = reverse("enable-disable-auto-check-in-out")

        response = client.post(url, {}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            AttendanceGeneralSetting.auto_check_in_out_enabled(self.company)
        )
        # Only the selected company changed.
        self.assertTrue(AttendanceGeneralSetting.auto_check_in_out_enabled(None))

        client.post(url, {"auto_check_in_out": "on"}, HTTP_HX_REQUEST="true")
        self.assertTrue(
            AttendanceGeneralSetting.auto_check_in_out_enabled(self.company)
        )

    def test_toggle_view_for_all_companies_updates_every_company(self):
        client = self._admin_client("all")

        client.post(
            reverse("enable-disable-auto-check-in-out"), {}, HTTP_HX_REQUEST="true"
        )
        self.assertFalse(AttendanceGeneralSetting.auto_check_in_out_enabled(None))
        self.assertFalse(
            AttendanceGeneralSetting.auto_check_in_out_enabled(self.company)
        )

    def test_management_command(self):
        out = StringIO()
        call_command("run_auto_attendance", "--at", f"{MONDAY} 17:30", stdout=out)
        # The shift is over at 17:30, so nothing is checked in.
        self.assertIn("Checked in 0", out.getvalue())
        call_command("run_auto_attendance", "--at", f"{MONDAY} 09:30", stdout=out)
        self.assertTrue(self.attendance().exists())

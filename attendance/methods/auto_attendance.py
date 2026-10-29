"""
Automatic check-in and check-out.

Two scheduler jobs (attendance/scheduler.py) drive this module:

* ``auto_check_in`` checks every active employee in at their shift's start
  time, while the shift is under way, on days the shift is scheduled.
* ``auto_check_out`` checks out any open attendance, automatic or manual, once
  its shift has ended. It also serves the older per-shift "Automatic Check
  Out" option, which works whether or not the global setting is on.

Both go through ``employee_clock_in`` / ``employee_clock_out``, the same code
the check-in button and biometric devices use, so worked hours, overtime, late
come / early out and validation come out exactly as for a manual punch.

The global switch is ``AttendanceGeneralSetting.auto_check_in_out``, resolved
per company. Every run is idempotent: an employee and attendance date is
checked in at most once (``AutoCheckInLog``), and only open activities are
checked out, so running the jobs repeatedly or late is safe.
"""

import logging
from datetime import datetime, time, timedelta

from django.apps import apps
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# How far back the global setting reaches when closing open attendances. The
# previous day is needed for night shifts that end after midnight; anything
# older was left open before the job could see it and is not rewritten.
CHECK_OUT_LOOKBACK_DAYS = 1


def _aware(day, at):
    """`day` at `at`, in the current timezone."""
    combined = datetime.combine(day, at)
    if timezone.is_naive(combined):
        combined = timezone.make_aware(combined)
    return combined


def shift_window(attendance_date, schedule):
    """
    Return the (check-in, check-out) datetimes of `schedule` worked for
    `attendance_date`. A shift whose end is not after its start runs into
    the next day. The check-out is the shift's "Automatic Check Out Time"
    when that option is on, otherwise the shift end.
    """
    start_dt = _aware(attendance_date, schedule.start_time)
    out_time = schedule.end_time
    if schedule.is_auto_punch_out_enabled and schedule.auto_punch_out_time:
        out_time = schedule.auto_punch_out_time
    out_day = attendance_date
    if schedule.end_time <= schedule.start_time:
        out_day = attendance_date + timedelta(days=1)
    return start_dt, _aware(out_day, out_time)


class _SettingCache:
    """Per-run cache of the company-scoped automatic check in/out setting."""

    def __init__(self):
        self._values = {}

    def enabled(self, company):
        from attendance.models import AttendanceGeneralSetting

        key = company.pk if company else None
        if key not in self._values:
            self._values[key] = AttendanceGeneralSetting.auto_check_in_out_enabled(
                company
            )
        return self._values[key]


def _full_day_leave_employee_ids(attendance_date):
    """Employees on an approved leave covering all of `attendance_date`."""
    if not apps.is_installed("leave"):
        return set()
    from leave.models import LEAVE_IN_EFFECT_STATUSES, LeaveRequest

    employee_ids = set()
    leaves = LeaveRequest.objects.entire().filter(
        status__in=LEAVE_IN_EFFECT_STATUSES, start_date__lte=attendance_date
    )
    for leave in leaves.exclude(end_date__lt=attendance_date):
        end_date = leave.end_date or leave.start_date
        if attendance_date > end_date:
            continue
        # The first and last days can be half days; the days between are not.
        if (
            attendance_date == leave.start_date
            and leave.start_date_breakdown != "full_day"
        ):
            continue
        if attendance_date == end_date and leave.end_date_breakdown != "full_day":
            continue
        employee_ids.add(leave.employee_id_id)
    return employee_ids


def _is_day_off(employee, company, attendance_date):
    """A holiday or weekly off day of the employee's company."""
    from base.methods import is_company_leave, is_holiday

    return bool(
        is_holiday(attendance_date, employee, company=company)
        or is_company_leave(attendance_date, company=company)
    )


def _check_in_for_date(attendance_date, now, settings):
    from attendance.models import Attendance, AttendanceActivity, AutoCheckInLog
    from attendance.views.clock_in_out import employee_clock_in
    from base.models import EmployeeShiftSchedule, Roster
    from employee.models import EmployeeWorkInformation

    # A shift without a schedule for this weekday is off that day.
    schedules = {
        schedule.shift_id_id: schedule
        for schedule in EmployeeShiftSchedule.objects.entire()
        .filter(
            day__day=attendance_date.strftime("%A").lower(),
            start_time__isnull=False,
            end_time__isnull=False,
        )
        .select_related("shift_id")
    }
    if not schedules:
        return 0

    # Published roster entries override the assigned shift for the day.
    roster = {
        entry.employee_id: entry
        for entry in Roster.objects.entire().filter(
            date=attendance_date, is_published=True
        )
    }
    # Already handled by an earlier run, already checked in for the day, or
    # still checked in from an earlier shift.
    skip = set(
        AutoCheckInLog.objects.filter(attendance_date=attendance_date).values_list(
            "employee_id", flat=True
        )
    )
    skip.update(
        Attendance.objects.entire()
        .filter(attendance_date=attendance_date)
        .values_list("employee_id", flat=True)
    )
    skip.update(
        AttendanceActivity.objects.entire()
        .filter(clock_out__isnull=True)
        .values_list("employee_id", flat=True)
    )
    on_leave = None

    work_infos = (
        EmployeeWorkInformation.objects.entire()
        .filter(employee_id__is_active=True)
        .select_related("employee_id", "company_id")
    )
    created = 0
    for work_info in work_infos:
        employee = work_info.employee_id
        entry = roster.get(employee.pk)
        if entry is not None and entry.is_off:
            continue
        shift_id = work_info.shift_id_id
        if entry is not None and entry.shift_id:
            shift_id = entry.shift_id
        schedule = schedules.get(shift_id)
        if schedule is None:
            continue
        start_dt, out_dt = shift_window(attendance_date, schedule)
        if not start_dt <= now < out_dt:
            continue
        if employee.pk in skip:
            continue
        if work_info.date_joining and work_info.date_joining > attendance_date:
            continue
        company = work_info.company_id
        if not settings.enabled(company):
            continue
        if on_leave is None:
            on_leave = _full_day_leave_employee_ids(attendance_date)
        if employee.pk in on_leave:
            continue
        if _is_day_off(employee, company, attendance_date):
            continue

        try:
            with transaction.atomic():
                _log, is_new = AutoCheckInLog.objects.get_or_create(
                    employee_id=employee,
                    attendance_date=attendance_date,
                    defaults={"shift_id": schedule.shift_id},
                )
                if not is_new:
                    continue
                employee_clock_in(
                    employee=employee,
                    shift=schedule.shift_id,
                    date_today=attendance_date,
                    now=schedule.start_time.strftime("%H:%M"),
                    in_datetime=start_dt,
                    attendance_date=attendance_date,
                    auto=True,
                )
            created += 1
        except Exception:
            logger.exception(
                "Automatic check-in failed for employee_id=%s on %s",
                employee.pk,
                attendance_date,
            )
    return created


def auto_check_in(now=None):
    """
    Check in every eligible employee whose shift is under way. Returns how
    many employees were checked in.

    An employee is skipped when inactive, not yet joined, already checked in
    (or still checked in from an earlier shift), on an approved full-day
    leave, on a holiday or weekly off day of their company, on a shift with no
    schedule that weekday (or a roster day off), or when the setting is off
    for their company.
    """
    now = timezone.localtime(now or timezone.now())
    settings = _SettingCache()
    today = now.date()
    created = 0
    # Yesterday too: a night shift that started then may still be running.
    for attendance_date in (today - timedelta(days=1), today):
        created += _check_in_for_date(attendance_date, now, settings)
    return created


def auto_check_out(now=None):
    """
    Check out every open attendance whose shift has ended. Returns how many
    employees were checked out.

    An open attendance is checked out at its shift's end (or its "Automatic
    Check Out Time") when the global setting is on for the employee's company
    or the shift has the per-shift automatic check out turned on. An activity
    begun after that time is overtime the employee closes themselves, so it
    is left open.
    """
    from attendance.models import Attendance, AttendanceActivity
    from attendance.views.clock_in_out import employee_clock_out
    from base.models import EmployeeShiftSchedule

    now = timezone.localtime(now or timezone.now())
    settings = _SettingCache()
    lookback = now.date() - timedelta(days=CHECK_OUT_LOOKBACK_DAYS)

    # clock_out_attendance_and_activity() closes an employee's last open
    # activity and updates their latest attendance, so only that pair is
    # ever a candidate.
    latest_open = {}
    for activity in (
        AttendanceActivity.objects.entire()
        .filter(clock_out__isnull=True)
        .select_related("employee_id__employee_work_info__company_id")
        .order_by("attendance_date", "id")
    ):
        latest_open[activity.employee_id_id] = activity

    closed = 0
    for activity in latest_open.values():
        employee = activity.employee_id
        attendance = (
            Attendance.objects.entire()
            .filter(employee_id=employee)
            .order_by("-attendance_date", "-id")
            .select_related("shift_id")
            .first()
        )
        if (
            attendance is None
            or attendance.attendance_date != activity.attendance_date
            or attendance.shift_id is None
            or attendance.attendance_day_id is None
        ):
            continue
        schedule = (
            EmployeeShiftSchedule.objects.entire()
            .filter(shift_id=attendance.shift_id, day_id=attendance.attendance_day_id)
            .first()
        )
        if schedule is None or not (schedule.start_time and schedule.end_time):
            continue

        per_shift = bool(
            schedule.is_auto_punch_out_enabled and schedule.auto_punch_out_time
        )
        if not per_shift:
            if attendance.attendance_date < lookback:
                continue
            work_info = getattr(employee, "employee_work_info", None)
            if not settings.enabled(getattr(work_info, "company_id", None)):
                continue

        _start_dt, out_dt = shift_window(attendance.attendance_date, schedule)
        if now < out_dt:
            continue
        in_dt = activity.in_datetime or _aware(
            activity.clock_in_date, activity.clock_in or time()
        )
        if in_dt >= out_dt:
            continue

        try:
            with transaction.atomic():
                employee_clock_out(
                    employee=employee,
                    shift=attendance.shift_id,
                    date_today=timezone.localtime(out_dt).date(),
                    now=timezone.localtime(out_dt).strftime("%H:%M"),
                    out_datetime=out_dt,
                    auto=True,
                )
            closed += 1
        except Exception:
            logger.exception(
                "Automatic check-out failed for employee_id=%s on %s",
                employee.pk,
                attendance.attendance_date,
            )
    return closed

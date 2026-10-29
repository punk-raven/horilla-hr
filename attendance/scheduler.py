import datetime

from base.backends import logger
from horilla.scheduling import register_job


def auto_punch_out():
    """
    Check out open attendances whose shift has ended: for shifts with
    "Automatic Check Out" on, and for every shift when automatic check in/out
    is on for the employee's company. The job keeps its historical name and
    id so the persisted jobstore row stays valid.
    """
    from attendance.methods.auto_attendance import auto_check_out

    auto_check_out()


def auto_check_in():
    """
    Check employees in at their shift start when automatic check in/out is
    on for their company.
    """
    from attendance.methods.auto_attendance import auto_check_in as run

    run()


def create_work_record():
    from attendance.models import WorkRecords
    from employee.models import Employee

    date = datetime.date.today()
    work_records = WorkRecords.objects.filter(date=date).values_list(
        "employee_id", flat=True
    )
    employees = Employee.objects.exclude(id__in=work_records)
    records_to_create = []

    for employee in employees:
        try:
            shift_schedule = employee.get_shift_schedule()
            if shift_schedule is None:
                continue

            shift = employee.get_shift()
            record = WorkRecords(
                employee_id=employee,
                date=date,
                work_record_type="DFT",
                shift_id=shift,
                message="",
            )
            records_to_create.append(record)
        except Exception as e:
            # Employee.__str__ is "Name (BADGE)", so interpolating the
            # object writes a real name into the log. The id is enough to
            # find the row, and logger.exception keeps the traceback.
            logger.exception(
                "Error preparing work record for employee_id=%s",
                getattr(employee, "pk", employee),
            )

    if records_to_create:
        try:
            WorkRecords.objects.bulk_create(records_to_create, ignore_conflicts=True)
        except Exception as e:
            logger.error(f"Failed to bulk create work records: {e}")


register_job(create_work_record, "interval", minutes=30, misfire_grace_time=3600 * 3)
register_job(
    create_work_record,
    "cron",
    job_id="create_daily_work_record",
    hour=0,
    minute=30,
    misfire_grace_time=3600 * 9,
)
register_job(
    auto_punch_out,
    "interval",
    job_id="auto_punch_out",
    minutes=5,
    misfire_grace_time=600,
)
register_job(
    auto_check_in,
    "interval",
    job_id="auto_check_in",
    minutes=5,
    misfire_grace_time=600,
)

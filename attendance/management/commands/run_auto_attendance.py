"""
Run automatic check-in and check-out once, outside the scheduler.

    python manage.py run_auto_attendance
    python manage.py run_auto_attendance --at "2026-10-29 09:30"

The scheduler runs the same code every five minutes (see
attendance/scheduler.py); this is for running it on demand, for example right
after turning the setting on. Like the jobs, it is safe to run repeatedly.
"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from attendance.methods.auto_attendance import auto_check_in, auto_check_out


class Command(BaseCommand):
    help = "Check employees in and out automatically as per their work shifts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--at",
            help=(
                'Run as if the time were this local "YYYY-MM-DD HH:MM" instead '
                "of now. It cannot be in the future."
            ),
        )

    def handle(self, *args, **options):
        now = timezone.now()
        if options["at"]:
            try:
                at = datetime.strptime(options["at"], "%Y-%m-%d %H:%M")
            except ValueError as error:
                raise CommandError('--at must look like "YYYY-MM-DD HH:MM"') from error
            at = timezone.make_aware(at)
            if at > now:
                raise CommandError("--at cannot be in the future.")
            now = at

        # Close finished shifts first, so an employee whose previous shift
        # was still open can be checked in to the next one in the same run.
        checked_out = auto_check_out(now)
        checked_in = auto_check_in(now)
        self.stdout.write(
            self.style.SUCCESS(
                f"Checked in {checked_in} employee(s), checked out {checked_out}."
            )
        )

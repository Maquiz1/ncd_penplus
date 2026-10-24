import io

from celery import shared_task
from django.core.management import call_command


@shared_task(name='reporting.monthly_enrollment_report')
def monthly_enrollment_report():
    """Runs daily; sends only on the last Sunday of the month (the command checks the date)."""
    out = io.StringIO()
    call_command('send_monthly_enrollment_reports', stdout=out)
    return out.getvalue().strip()

import json

from django.conf import settings
from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, PeriodicTask

# name, task, crontab (minute, hour, day_of_week, day_of_month), description
TASKS = [
    ('SMS: reminders and sending', 'notifications.send_sms', ('*/15', '7-18', '*', '*'),
     'Every 15 minutes, 07:00–18:45: queue day-before / same-day reminders and send queued SMS (with retries).'),
    ('Default / LTFU rule', 'clinical.apply_ltfu_rule', ('0', '1', '*', '*'),
     'Daily 01:00: no attended visit for LTFU_MONTHS months → default / LTFU (care and research).'),
    ('Data-quality checks', 'issues.detect_data_issues', ('30', '1', '*', '*'),
     'Daily 01:30: add new problems to Data issues (never reopens resolved issues).'),
    ('Monthly enrollment report', 'reporting.monthly_enrollment_report', ('0', '9', '*', '*'),
     'Daily 09:00: sends only on the last Sunday of the month, to each recipient for their own scope.'),
    ('Remove old export files', 'exports.cleanup_exports', ('0', '2', '*', '*'),
     'Daily 02:00: delete export files older than EXPORT_RETENTION_DAYS (default 7).'),
]


class Command(BaseCommand):
    help = ('Create the default scheduled jobs for Celery beat. Existing jobs are left as they are, so changes made '
            'in Django admin (Periodic tasks) are kept.')

    def handle(self, *args, **options):
        for name, task, (minute, hour, dow, dom), description in TASKS:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute=minute, hour=hour, day_of_week=dow, day_of_month=dom, month_of_year='*',
                timezone=settings.TIME_ZONE)
            _, created = PeriodicTask.objects.get_or_create(name=name, defaults={
                'task': task, 'crontab': schedule, 'description': description, 'kwargs': json.dumps({})})
            self.stdout.write(f'{"Created" if created else "Kept existing"}: {name}')
        self.stdout.write(self.style.SUCCESS('Scheduled jobs ready. Adjust them in Django admin → Periodic tasks.'))

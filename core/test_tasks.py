from django.core.management import call_command
from django_celery_beat.models import PeriodicTask

from core.tests import BaseTestCase


class ScheduledJobTests(BaseTestCase):
    def test_setup_creates_jobs_once_and_keeps_admin_changes(self):
        call_command('setup_periodic_tasks', stdout=open('/dev/null', 'w'))
        self.assertEqual(PeriodicTask.objects.count(), 5)
        job = PeriodicTask.objects.get(name='Default / LTFU rule')
        job.enabled = False
        job.save()
        call_command('setup_periodic_tasks', stdout=open('/dev/null', 'w'))
        self.assertEqual(PeriodicTask.objects.count(), 5)
        self.assertFalse(PeriodicTask.objects.get(name='Default / LTFU rule').enabled)

    def test_tasks_are_registered_and_run(self):
        from ncd_penplus.celery import app
        from clinical.tasks import apply_ltfu_rule_task
        from issues.tasks import detect_data_issues
        from notifications.tasks import send_sms
        import exports.tasks  # noqa: F401
        import reporting.tasks  # noqa: F401  (the worker autodiscovers every tasks.py)
        for name in ('notifications.send_sms', 'clinical.apply_ltfu_rule', 'issues.detect_data_issues',
                     'reporting.monthly_enrollment_report'):
            self.assertIn(name, app.tasks)
        self.assertEqual(send_sms.apply().get()['sent'], 0)
        self.assertEqual(apply_ltfu_rule_task.apply().get(), {'became_ltfu': 0})
        self.assertIn('visits_same_month', detect_data_issues.apply().get())

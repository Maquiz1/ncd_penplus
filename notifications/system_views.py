"""System settings for administrators: one overview of email, SMS, test mode and the scheduled jobs (Celery)."""
import datetime
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from audit.models import AuditEvent
from audit.services import log_event

from .email import active_site, from_email, site_url
from .models import EmailLog, SmsMessage, SmsSettings
from .sms import SmsError, sms_config
from .views import SYSTEM_PERMISSION


def broker_reachable():
    """Whether the Celery broker (Redis) answers, i.e. the worker can be handed a job."""
    from config.celery import app
    try:
        with app.connection_for_write() as connection:
            connection.ensure_connection(max_retries=1, timeout=2)
        return True
    except Exception:  # noqa: BLE001 - any broker failure means "not reachable"
        return False


def schedule_text(task):
    c = task.crontab
    if c is None:
        return str(task.interval or task.clocked or task.solar or '—')
    return f'minute {c.minute} · hour {c.hour} · day {c.day_of_month} · weekday {c.day_of_week}'


class SystemSettingsView(PermissionRequiredMixin, View):
    permission_required = SYSTEM_PERMISSION

    def get(self, request):
        from django_celery_beat.models import PeriodicTask
        week = timezone.now() - datetime.timedelta(days=7)
        try:
            sms, sms_error = sms_config(), ''
        except SmsError as exc:
            sms, sms_error = None, str(exc)
        tasks = list(PeriodicTask.objects.exclude(task__startswith='celery.').order_by('name'))
        return render(request, 'notifications/system.html', {
            'test': SmsSettings.load(), 'sms': sms, 'sms_error': sms_error, 'site': active_site(),
            'site_url': site_url(), 'sender': from_email(),
            'email_backend': settings.EMAIL_BACKEND.rsplit('.', 2)[-2],
            'email_week': dict(EmailLog.objects.filter(created_at__gte=week).values_list('status').annotate(n=Count('id'))),
            'sms_week': dict(SmsMessage.objects.filter(created_at__gte=week).values_list('status').annotate(n=Count('id'))),
            'sms_queued': SmsMessage.objects.filter(status='queued').count(),
            'tasks': tasks, 'tasks_enabled': sum(t.enabled for t in tasks),
            'last_run': max((t.last_run_at for t in tasks if t.last_run_at), default=None),
            'broker': broker_reachable(), 'eager': settings.CELERY_TASK_ALWAYS_EAGER})


class ScheduledJobsView(PermissionRequiredMixin, View):
    """The Celery beat schedules (stored in the database): switch a job on or off, or run it now."""

    permission_required = SYSTEM_PERMISSION

    def get(self, request):
        from django_celery_beat.models import PeriodicTask
        tasks = PeriodicTask.objects.exclude(task__startswith='celery.').select_related('crontab').order_by('name')
        return render(request, 'notifications/jobs.html', {
            'rows': [(t, schedule_text(t)) for t in tasks], 'broker': broker_reachable(),
            'eager': settings.CELERY_TASK_ALWAYS_EAGER, 'timezone': settings.TIME_ZONE})

    def post(self, request, pk, action):
        from django_celery_beat.models import PeriodicTask
        from config.celery import app
        task = get_object_or_404(PeriodicTask, pk=pk)
        if action == 'toggle':
            task.enabled = not task.enabled
            task.save(update_fields=['enabled'])
            log_event(request=request, action=AuditEvent.Action.UPDATE, obj=task,
                      changes={'enabled': task.enabled})
            messages.success(request, f'{task.name}: {"on" if task.enabled else "off"}.')
        elif action == 'run':
            kwargs = json.loads(task.kwargs or '{}')
            if broker_reachable() and not settings.CELERY_TASK_ALWAYS_EAGER:
                app.send_task(task.task, kwargs=kwargs)
                messages.success(request, f'{task.name}: handed to the background worker. Its results show in the '
                                          f'logs (SMS log, Data issues …) in a moment.')
            else:
                app.loader.import_default_modules()   # the web process has not loaded the task modules
                result = app.tasks[task.task].apply(kwargs=kwargs)
                messages.success(request, f'{task.name}: run here (no background worker reachable). Result: '
                                          f'{result.result}')
            log_event(request=request, action=AuditEvent.Action.CREATE, obj=task, reason='Run now')
        return redirect('system:jobs')

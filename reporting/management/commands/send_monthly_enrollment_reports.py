from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event
from core.utils import last_sunday, month_bounds
from facilities.scope import scope_description

from reporting.models import ReportRun
from reporting.services import period_report


class Command(BaseCommand):
    help = ('Send the monthly enrollment report to each recipient, limited to their own access scope. '
            'Schedule daily (e.g. cron at 09:00 Africa/Dar_es_Salaam); it only sends on the last Sunday of the month '
            'unless --force is given.')

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Send even if today is not the last Sunday.')
        parser.add_argument('--month', help='Report month as YYYY-MM (default: current month).')

    def handle(self, *args, force=False, month=None, **options):
        today = timezone.localdate()
        year, mon = (int(x) for x in month.split('-')) if month else (today.year, today.month)
        if not force and today != last_sunday(today.year, today.month):
            self.stdout.write(f'Not the last Sunday (that is {last_sunday(today.year, today.month)}); nothing sent.')
            return
        start, end = month_bounds(year, mon)
        User = get_user_model()
        sent = failed = skipped = 0
        for user in User.objects.filter(is_active=True).exclude(email=''):
            # Permissions and scope are re-checked at generation time for every recipient.
            if not user.has_perm('reporting.receive_monthly_report') or not user.has_perm('reporting.view_reports'):
                continue
            if ReportRun.objects.filter(recipient=user, period_start=start, status='sent',
                                        report_type='monthly_enrollment').exists() and not force:
                skipped += 1
                continue
            run = ReportRun.objects.create(period_start=start, period_end=end, recipient=user,
                                           scope_description=scope_description(user)[:500], status='generating')
            try:
                report = period_report(user, start, end)
                body = render_to_string('reporting/monthly_email.txt', {
                    'user': user, 'report': report, 'scope': run.scope_description, 'site_url': settings.SITE_URL,
                    'generated': timezone.localtime(), 'tz': settings.TIME_ZONE})
                run.generated_at = timezone.now()
                user.refresh_from_db()
                if not user.is_active:
                    run.status, run.error = 'cancelled', 'Recipient deactivated before delivery'
                    run.save()
                    continue
                send_mail(f'Monthly enrollment report {start:%B %Y}', body, settings.DEFAULT_FROM_EMAIL, [user.email])
                run.status, run.sent_at = 'sent', timezone.now()
                run.summary = {'programme_total': report['programme_total'], 'research_total': report['research_total']}
                sent += 1
            except Exception as exc:  # noqa: BLE001 - log any delivery failure for administrators
                run.status, run.error = 'failed', str(exc)[:2000]
                failed += 1
            run.save()
            log_event(actor=None, action=AuditEvent.Action.REPORT, obj=run, changes={'status': run.status})
        self.stdout.write(self.style.SUCCESS(f'Monthly report {start:%Y-%m}: {sent} sent, {failed} failed, {skipped} already sent.'))

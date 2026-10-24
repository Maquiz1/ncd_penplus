from django.core.management.base import BaseCommand

from notifications.services import process_queue, queue_reminders


class Command(BaseCommand):
    help = ('Queue appointment reminders (day before and same day) and send queued SMS. '
            'Schedule e.g. every 15 minutes between 07:00 and 18:00 with cron.')

    def add_arguments(self, parser):
        parser.add_argument('--no-reminders', action='store_true', help='Only send what is already queued.')
        parser.add_argument('--limit', type=int, default=500)

    def handle(self, *args, no_reminders=False, limit=500, **options):
        queued = 0 if no_reminders else queue_reminders()
        counts = process_queue(limit=limit)
        self.stdout.write(self.style.SUCCESS(
            f'Reminders queued: {queued}; sent: {counts["sent"]}, failed: {counts["failed"]}, '
            f'cancelled (no longer relevant): {counts["cancelled"]}.'))

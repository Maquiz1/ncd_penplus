from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from clinical.services import apply_ltfu_rule


class Command(BaseCommand):
    help = ('Mark active programme enrollments as default / lost to follow-up when the patient has had no attended '
            'visit for LTFU_MONTHS months (default 3) after the last attended visit. Schedule daily, e.g. via cron.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List the enrollments that would change.')
        parser.add_argument('--months', type=int, default=None, help=f'Override LTFU_MONTHS ({settings.LTFU_MONTHS}).')

    def handle(self, *args, dry_run=False, months=None, **options):
        system, _ = get_user_model().objects.get_or_create(
            username='system', defaults={'first_name': 'System', 'last_name': 'rules', 'is_active': False})
        changed = apply_ltfu_rule(system, months=months, dry_run=dry_run)
        for enrollment, due in changed[:50]:
            self.stdout.write(f'  {enrollment.patient} {enrollment.programme}: LTFU from {due:%d %b %Y}')
        verb = 'would become' if dry_run else 'became'
        self.stdout.write(self.style.SUCCESS(f'{len(changed)} enrollment(s) {verb} default / lost to follow-up.'))

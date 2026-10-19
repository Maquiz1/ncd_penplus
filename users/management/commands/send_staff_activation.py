from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from users.services import pending_activation, send_bulk_activation


class Command(BaseCommand):
    help = 'Send set-password links to active staff accounts that have not been activated yet.'

    def add_arguments(self, parser):
        parser.add_argument('--migrated', action='store_true', help='Only staff migrated from the EDCS.')
        parser.add_argument('--username', action='append', default=[], help='Limit to these usernames (repeatable).')
        parser.add_argument('--dry-run', action='store_true', help='List who would receive a link; send nothing.')

    def handle(self, *args, migrated=False, username=(), dry_run=False, **options):
        users = get_user_model().objects.all()
        if username:
            users = users.filter(username__in=username)
        pending = pending_activation(users, migrated_only=migrated)
        if dry_run:
            for user in pending:
                self.stdout.write(f'  would send to {user.username} <{user.email}>')
            self.stdout.write(self.style.WARNING(f'DRY RUN: {len(pending)} account(s) pending activation.'))
            return
        sent, failed = send_bulk_activation(pending)
        for user, reason in failed:
            self.stderr.write(f'  {user.username}: not sent ({reason})')
        self.stdout.write(self.style.SUCCESS(f'Activation links sent: {len(sent)}; failed: {len(failed)}.'))

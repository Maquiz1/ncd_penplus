import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event
from integrations.edcs import Importer, MySQLReader


class DryRunRollback(Exception):
    pass


class Command(BaseCommand):
    help = ('Migrate the previous PEN-Plus EDCS (MySQL, read-only) into this system. '
            'Run with --dry-run first; safe to re-run (already imported rows are skipped).')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Import inside a transaction, report, then roll back.')
        parser.add_argument('--steps', nargs='+', choices=Importer.ADD_ON_STEPS,
                            help='Only run these add-on steps on top of an earlier full migration.')
        parser.add_argument('--report-dir', default=str(Path(settings.BASE_DIR) / 'import_reports'))

    def handle(self, *args, dry_run=False, report_dir=None, steps=None, **options):
        call_command('setup_roles', stdout=open('/dev/null', 'w'))
        try:
            reader = MySQLReader(settings.LEGACY_EDCS)
        except Exception as exc:  # noqa: BLE001 - surface connection problems clearly
            raise CommandError(f'Cannot connect to the EDCS database: {exc}')
        importer = Importer(reader, log=self.stdout.write)
        started = timezone.now()
        try:
            with transaction.atomic():
                counts, issues = importer.run(steps=steps)
                if dry_run:
                    raise DryRunRollback
                log_event(action=AuditEvent.Action.CREATE, model='integrations.ExternalRecord',
                          object_repr='PEN-Plus EDCS migration', changes=dict(counts))
        except DryRunRollback:
            counts, issues = importer.counts, importer.issues
        Path(report_dir).mkdir(parents=True, exist_ok=True)
        stamp = started.strftime('%Y%m%d-%H%M%S')
        report_path = Path(report_dir) / f'edcs-import-{"dryrun-" if dry_run else ""}{stamp}.json'
        report_path.write_text(json.dumps({'dry_run': dry_run, 'started': started.isoformat(), 'counts': counts,
                                           'issues': issues}, indent=1, default=str))
        self.stdout.write(self.style.WARNING('DRY RUN – nothing was saved.') if dry_run else
                          self.style.SUCCESS('Migration saved.'))
        for key, value in sorted(counts.items()):
            self.stdout.write(f'  {key}: {value}')
        self.stdout.write('Issues for review:')
        for key, items in sorted(issues.items()):
            self.stdout.write(f'  {key}: {len(items)}')
        self.stdout.write(f'Full report (subject numbers and old IDs only, no names): {report_path}')

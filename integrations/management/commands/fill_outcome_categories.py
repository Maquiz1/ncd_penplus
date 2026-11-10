"""Fill "Transferred to" and "Cause of death" for subjects imported from EDCS before these were stored as choices,
from the EDCS summary forms kept in RECAPEN (the latest one saying Transfer out or Death). Only empty values are
filled; safe to run more than once."""
from django.core.management.base import BaseCommand

from clinical.models import ProgrammeEnrollment
from crf.models import FormSubmission
from integrations.edcs import DEATH_CAUSE, TRANSFER_TO
from patients.models import Patient


def latest_summary(patient_id, outcome):
    rows = (FormSubmission.objects.filter(patient_id=patient_id, version__definition__code='edcs-summary',
                                          data__outcome=outcome).order_by('-submitted_at', '-pk'))
    return rows.values_list('data', flat=True).first()


class Command(BaseCommand):
    help = 'Fill transfer destination type and cause-of-death category from the EDCS summary forms.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only count what would be filled.')

    def handle(self, *args, dry_run=False, **options):
        transfers = deaths = 0
        for enrollment in ProgrammeEnrollment.objects.filter(status='transferred_out', transfer_to_type=''):
            data = latest_summary(enrollment.patient_id, '4')
            kind = TRANSFER_TO.get(str((data or {}).get('transfer_out') or ''), '')
            if not kind:
                continue
            transfers += 1
            if not dry_run:
                ProgrammeEnrollment.objects.filter(pk=enrollment.pk).update(
                    transfer_to_type=kind, transfer_to=str(data.get('transfer_other') or '')[:200])
        for patient in Patient.all_objects.filter(vital_status='deceased', cause_of_death_category=''):
            data = latest_summary(patient.pk, '5')
            category = DEATH_CAUSE.get(str((data or {}).get('cause_death') or ''), '')
            if not category:
                continue
            deaths += 1
            if not dry_run:
                Patient.all_objects.filter(pk=patient.pk).update(cause_of_death_category=category)
        verb = 'Would fill' if dry_run else 'Filled'
        self.stdout.write(self.style.SUCCESS(f'{verb}: {transfers} transfer destinations, {deaths} causes of death.'))

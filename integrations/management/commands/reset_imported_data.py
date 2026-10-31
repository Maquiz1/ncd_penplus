"""Remove all patient data so the EDCS import can be run again from scratch (until go-live).

Re-importing only adds rows it has not seen; edits and deletions made later in the old EDCS would be missed.
Until go-live the import therefore replaces the patient data: every patient and every row that depends on one
(visits, vitals, labs, medicines, enrolments, data issues…) is removed. Staff accounts, roles, facilities,
locations, catalogues and settings are kept, and patient numbers restart so the import gives the same numbers.

It refuses to run when anything was entered or changed directly in this system since the last import, so it
can never wipe real data after go-live (unless --force).
"""
from collections import defaultdict

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import ForeignKey, Max

from audit.models import AuditEvent
from integrations.edcs import SOURCE
from integrations.models import ExternalRecord
from patients.models import Patient, PatientIdSequence

CHANGES = ['create', 'update', 'delete', 'restore', 'status', 'transfer', 'review']
CHUNK = 2000
RESET_MARKER = 'integrations.reset'


def dependent_rows():
    """{model: set of pks} for every patient and every row that (directly or through other rows) points to one."""
    models = [m for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy]
    links = defaultdict(list)  # target model -> [(model, fk field)]
    for model in models:
        for field in model._meta.concrete_fields:
            if isinstance(field, ForeignKey):
                links[field.related_model._meta.concrete_model].append((model, field))
    rows = {Patient: set(Patient.all_objects.values_list('pk', flat=True))}
    changed = True
    while changed:
        changed = False
        for target, pks in list(rows.items()):
            for model, field in links.get(target, []):
                found = set()
                ids = list(pks)
                for i in range(0, len(ids), CHUNK):
                    found.update(model._base_manager.filter(**{f'{field.attname}__in': ids[i:i + CHUNK]})
                                 .values_list('pk', flat=True))
                new = found - rows.get(model, set())
                if new:
                    rows.setdefault(model, set()).update(new)
                    changed = True
    return {m: pks for m, pks in rows.items() if pks}


class Command(BaseCommand):
    help = ('Remove all subject data before a fresh EDCS import (until go-live). Refuses when data was entered '
            'directly in this system since the last import.')

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='Only report what would be removed.')
        parser.add_argument('--force', action='store_true',
                            help='Remove even when data was entered directly in this system (it is lost).')

    def handle(self, *args, check=False, force=False, **options):
        rows = dependent_rows()
        labels = {m._meta.label for m in rows}
        # Only changes after the last import or reset count (older ones were already replaced or accepted).
        last_import = max(filter(None, [
            ExternalRecord.objects.filter(source=SOURCE).aggregate(t=Max('imported_at'))['t'],
            AuditEvent.objects.filter(model=RESET_MARKER).aggregate(t=Max('timestamp'))['t']]), default=None)
        # Changes by a person logged in to this system (the import and setup commands have no actor or edcs_import).
        direct = (AuditEvent.objects.filter(model__in=labels, action__in=CHANGES, actor__isnull=False)
                  .exclude(actor__username='edcs_import'))
        if last_import:
            direct = direct.filter(timestamp__gt=last_import)
        direct_by_model = sorted(direct.values_list('model').distinct().order_by())
        n_direct = direct.count()

        self.stdout.write(f'Patients: {len(rows.get(Patient, ()))}. Rows to remove: '
                          f'{sum(len(p) for p in rows.values())} in {len(rows)} tables.')
        for model, pks in sorted(rows.items(), key=lambda item: -len(item[1]))[:12]:
            self.stdout.write(f'  {model._meta.label}: {len(pks)}')
        if n_direct:
            self.stdout.write(self.style.WARNING(
                f'{n_direct} changes were made directly in this system since the last import '
                f'({", ".join(m for (m,) in direct_by_model)}). A reset would lose them.'))
            if not force and not check:
                raise CommandError('Refusing to reset. If this data is only testing, run again with --force.')
        if check:
            return

        with transaction.atomic():
            if connection.vendor == 'mysql':
                with connection.cursor() as cursor:
                    cursor.execute('SET FOREIGN_KEY_CHECKS=0')
            try:
                for model, pks in rows.items():
                    ids = list(pks)
                    for i in range(0, len(ids), CHUNK):
                        model._base_manager.filter(pk__in=ids[i:i + CHUNK])._raw_delete(connection.alias)
                    ct = ContentType.objects.get_for_model(model)
                    for i in range(0, len(ids), CHUNK):
                        ExternalRecord.objects.filter(content_type=ct,
                                                      object_id__in=[str(pk) for pk in ids[i:i + CHUNK]]).delete()
                PatientIdSequence.objects.all().delete()  # the import gives the same patient numbers again
            finally:
                if connection.vendor == 'mysql':
                    with connection.cursor() as cursor:
                        cursor.execute('SET FOREIGN_KEY_CHECKS=1')
        AuditEvent.objects.create(action='delete', model=RESET_MARKER,
                                  object_repr='Subject data removed before a fresh EDCS import',
                                  changes={'tables': len(rows), 'patients': len(rows.get(Patient, ()))})
        self.stdout.write(self.style.SUCCESS('Subject data removed. Staff, facilities, catalogues and settings kept.'))

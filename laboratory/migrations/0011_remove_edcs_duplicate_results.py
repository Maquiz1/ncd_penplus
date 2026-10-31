"""Soft-delete exact copies of an EDCS lab result (same subject, day, test and value), which the EDCS held in
several forms. They stay in the database and can be restored."""
from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    LabOrder = apps.get_model('laboratory', 'LabOrder')
    seen = set()
    duplicates = []
    for pk, group, test, value in (LabOrder.objects.filter(clinical_notes='Imported from EDCS', is_deleted=False)
                                   .order_by('pk').values_list('pk', 'request_group', 'test_id', 'result_value')):
        key = (group, test, (value or '').strip())
        if key in seen:
            duplicates.append(pk)
        seen.add(key)
    LabOrder.objects.filter(pk__in=duplicates).update(
        is_deleted=True, deleted_at=timezone.now(),
        deletion_reason='Duplicate of the same EDCS result (same day, test and value)')


class Migration(migrations.Migration):
    dependencies = [('laboratory', '0010_group_and_review_edcs_results')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

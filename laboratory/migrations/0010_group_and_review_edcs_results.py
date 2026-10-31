"""EDCS lab results: group each subject's tests of one day into one request, and mark them reviewed
(they were confirmed in the EDCS), so they do not fill the doctor-review list."""
import uuid

from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    LabOrder = apps.get_model('laboratory', 'LabOrder')
    orders = LabOrder.objects.filter(clinical_notes='Imported from EDCS')
    for order in orders.only('pk', 'patient_id', 'ordered_at', 'resulted_at', 'resulted_by_id', 'status').iterator():
        day = timezone.localtime(order.ordered_at).date()
        changes = {'request_group': uuid.uuid5(uuid.NAMESPACE_URL, f'edcs-lab/{order.patient_id}/{day}')}
        if order.status == 'resulted':
            changes.update(status='reviewed', reviewed_at=order.resulted_at or order.ordered_at,
                           reviewed_by_id=order.resulted_by_id)
        LabOrder.objects.filter(pk=order.pk).update(**changes)


class Migration(migrations.Migration):
    dependencies = [('laboratory', '0009_alter_monitoringgap_reason_and_more')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

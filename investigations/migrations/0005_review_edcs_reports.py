"""ECG / echo reports imported from the EDCS were already reviewed there: mark them reviewed so they do not fill
the "reports to review" list."""
from django.db import migrations
from django.db.models import F


def forwards(apps, schema_editor):
    InvestigationOrder = apps.get_model('investigations', 'InvestigationOrder')
    InvestigationOrder.objects.filter(status='completed', description__endswith='(imported from EDCS)').update(
        status='reviewed', reviewed_at=F('performed_at'), reviewed_by=F('reported_by'))


class Migration(migrations.Migration):
    dependencies = [('investigations', '0004_alter_investigationtype_preparation')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

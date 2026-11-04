"""EDCS form entries were already accepted in the EDCS, so they do not belong in the form review queue."""
from django.db import migrations
from django.db.models import F


def forwards(apps, schema_editor):
    apps.get_model('crf', 'FormSubmission').objects.filter(created_by__username='edcs_import', status='submitted').update(
        status='verified', reviewed_by_id=F('submitted_by_id'), reviewed_at=F('submitted_at'))


class Migration(migrations.Migration):
    dependencies = [('crf', '0002_formsubmission_deleted_at_formsubmission_deleted_by_and_more')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

from django.db import migrations

# The programme and the clinic are one thing under the PEN-Plus clinic, with one name.
NAMES = {'dm-care': 'Diabetes Clinic', 'cardiac-care': 'Cardiac Clinic', 'scd-care': 'SCD Clinic',
         'other-ncd-care': 'Other NCD Clinic'}


def rename(apps, schema_editor):
    ClinicalProgramme = apps.get_model('clinical', 'ClinicalProgramme')
    for code, name in NAMES.items():
        ClinicalProgramme.objects.filter(code=code).update(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('clinical', '0015_diagnosis_suspected_status'),
    ]

    operations = [
        migrations.RunPython(rename, migrations.RunPython.noop),
    ]

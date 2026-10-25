from django.db import migrations


def rename(old, new):
    def run(apps, schema_editor):
        Patient = apps.get_model('patients', 'Patient')
        for patient in Patient._base_manager.filter(patient_number__startswith=old):
            patient.patient_number = new + patient.patient_number[len(old):]
            patient.save(update_fields=['patient_number'])
    return run


class Migration(migrations.Migration):
    """Patient IDs change from NCDYY-NNNN to NPYY-NNNN (NCD PEN-Plus); the year and sequence stay the same."""

    dependencies = [('patients', '0006_patient_residence_street_alter_patient_address')]

    operations = [migrations.RunPython(rename('NCD', 'NP'), rename('NP', 'NCD'))]

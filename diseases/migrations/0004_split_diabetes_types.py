from django.db import migrations


def split(apps, schema_editor):
    """EDCS had five diabetes types; "Unspecified / gestational" becomes three, as in EDCS."""
    Condition = apps.get_model('diseases', 'Condition')
    Diagnosis = apps.get_model('clinical', 'Diagnosis')
    other = Condition.objects.filter(code='dm-other').first()
    if other is None:  # a new database: setup_diseases creates all of them
        return
    Condition.objects.filter(pk=other.pk).update(name='Diabetes, type not yet specified',
                                                 keywords='not yet specified\nunspecified')
    common = {'disease_id': other.disease_id, 'indicator_code': other.indicator_code, 'is_default': False}
    gestational, _ = Condition.objects.get_or_create(code='gdm', defaults={
        **common, 'name': 'Gestational diabetes', 'keywords': 'gestational', 'order': other.order})
    other_type, _ = Condition.objects.get_or_create(code='dm-other-type', defaults={
        **common, 'name': 'Other diabetes', 'keywords': 'other diabetes', 'order': other.order + 1})
    Diagnosis.objects.filter(condition=other, description__icontains='gestational').update(condition=gestational)
    Diagnosis.objects.filter(condition=other, description__icontains='other diabetes').update(condition=other_type)


class Migration(migrations.Migration):

    dependencies = [
        ('diseases', '0003_alter_condition_excludes'),
        ('clinical', '0015_diagnosis_suspected_status'),
    ]

    operations = [migrations.RunPython(split, migrations.RunPython.noop)]

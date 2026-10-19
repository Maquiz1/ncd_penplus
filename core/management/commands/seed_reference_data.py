from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from clinical.models import ClinicalProgramme, Disease
from crf.models import FormDefinition, FormField, FormSection, FormVersion
from crf.services import publish_version
from facilities.models import District, Facility, Region, StaffAssignment, Zone
from laboratory.models import LabTest
from pharmacy.models import Medication

GEOGRAPHY = {
    'Eastern Zone': {'Dar es Salaam': ['Ilala', 'Kinondoni', 'Temeke'], 'Pwani': ['Kibaha']},
    'Lake Zone': {'Mwanza': ['Nyamagana', 'Ilemela']},
    'Northern Zone': {'Kilimanjaro': ['Moshi Municipal']},
}
FACILITIES = [
    ('DEMO-ILA-HC', 'Demo Ilala Health Centre', 'Ilala', 'health_centre', True),
    ('DEMO-KIN-HOSP', 'Demo Kinondoni District Hospital', 'Kinondoni', 'hospital', True),
    ('DEMO-TEM-DISP', 'Demo Temeke Dispensary', 'Temeke', 'dispensary', False),
    ('DEMO-NYA-HOSP', 'Demo Nyamagana Hospital', 'Nyamagana', 'hospital', True),
    ('DEMO-MOS-HC', 'Demo Moshi Health Centre', 'Moshi Municipal', 'health_centre', False),
]
DISEASES = [('diabetes', 'Diabetes'), ('cardiac', 'Cardiac disease'), ('scd', 'Sickle cell disease')]
PROGRAMMES = [('dm-care', 'Diabetes care programme', 'diabetes'),
              ('cardiac-care', 'Cardiac care programme', 'cardiac'),
              ('scd-care', 'Sickle cell care programme', 'scd')]
LAB_TESTS = [
    ('hba1c', 'HbA1c', 'Blood (EDTA)', '%', '4.0–5.6'),
    ('fbg', 'Fasting blood glucose', 'Blood', 'mmol/L', '3.9–5.5'),
    ('rbg', 'Random blood glucose', 'Blood', 'mmol/L', '< 11.1'),
    ('creat', 'Serum creatinine', 'Serum', 'µmol/L', '53–115'),
    ('lipids', 'Lipid profile (total cholesterol)', 'Serum', 'mmol/L', '< 5.2'),
    ('cbc', 'Full blood count (Hb)', 'Blood (EDTA)', 'g/dL', '12–17'),
    ('hb-electro', 'Hb electrophoresis', 'Blood (EDTA)', '', 'HbAA'),
    ('urine-alb', 'Urine albumin (dipstick)', 'Urine', '', 'Negative'),
    ('bnp', 'NT-proBNP', 'Serum', 'pg/mL', '< 125'),
]
MEDICATIONS = [
    ('metformin-500', 'Metformin', '500 mg', 'tablet', 'tablet', 500),
    ('glibenclamide-5', 'Glibenclamide', '5 mg', 'tablet', 'tablet', 200),
    ('insulin-nph', 'Insulin NPH (isophane)', '100 IU/mL 10 mL', 'injection', 'vial', 20),
    ('insulin-soluble', 'Insulin soluble (regular)', '100 IU/mL 10 mL', 'injection', 'vial', 20),
    ('amlodipine-5', 'Amlodipine', '5 mg', 'tablet', 'tablet', 300),
    ('enalapril-10', 'Enalapril', '10 mg', 'tablet', 'tablet', 300),
    ('hctz-25', 'Hydrochlorothiazide', '25 mg', 'tablet', 'tablet', 200),
    ('furosemide-40', 'Furosemide', '40 mg', 'tablet', 'tablet', 200),
    ('atorvastatin-20', 'Atorvastatin', '20 mg', 'tablet', 'tablet', 200),
    ('aspirin-75', 'Aspirin', '75 mg', 'tablet', 'tablet', 300),
    ('bpg-2-4', 'Benzathine benzylpenicillin', '2.4 MU', 'injection', 'vial', 20),
    ('hydroxyurea-500', 'Hydroxyurea', '500 mg', 'capsule', 'capsule', 200),
    ('folic-acid-5', 'Folic acid', '5 mg', 'tablet', 'tablet', 300),
    ('penicillin-v-250', 'Phenoxymethylpenicillin (Pen V)', '250 mg', 'tablet', 'tablet', 200),
    ('paracetamol-500', 'Paracetamol', '500 mg', 'tablet', 'tablet', 500),
]
# (code, name, applicability, diseases, stages, locations, sections[(title, [(key, label, type, required, extra)])])
FORMS = [
    ('vital-signs', 'Vital signs', 'universal', [], [], ['facility', 'home', 'community'], [
        ('Measurements', [
            ('sbp', 'Systolic blood pressure', 'integer', True, {'unit': 'mmHg', 'min_value': 50, 'max_value': 300}),
            ('dbp', 'Diastolic blood pressure', 'integer', True, {'unit': 'mmHg', 'min_value': 30, 'max_value': 200}),
            ('pulse', 'Pulse', 'integer', False, {'unit': 'bpm', 'min_value': 20, 'max_value': 250}),
            ('weight', 'Weight', 'decimal', False, {'unit': 'kg', 'min_value': 1, 'max_value': 350}),
        ])]),
    ('general-prescreening', 'General pre-screening', 'universal', [], ['pre_screening'], [], [
        ('Residence', [
            ('lives_in_area', 'Lives in the study/catchment area?', 'yesno', True, {}),
            ('months_in_area', 'Expected months in area', 'integer', False,
             {'show_if_key': 'lives_in_area', 'show_if_value': 'yes', 'min_value': 0, 'max_value': 600}),
        ])]),
    ('cv-risk', 'Cardiovascular risk factors', 'selected', ['diabetes', 'cardiac'], [], [], [
        ('Risk factors', [
            ('smoking', 'Tobacco use', 'select', True, {'options': 'never|Never\ncurrent|Current\nformer|Former'}),
            ('alcohol', 'Alcohol use', 'yesno', True, {}),
            ('family_history', 'Family history of', 'multiselect', False,
             {'options': 'diabetes|Diabetes\nhypertension|Hypertension\nstroke|Stroke\nheart_disease|Heart disease'}),
        ])]),
    ('diabetes-assessment', 'Diabetes assessment', 'single', ['diabetes'], [], [], [
        ('History', [
            ('dm_type', 'Diabetes type', 'select', True, {'options': 't1|Type 1\nt2|Type 2\ngdm|Gestational\nother|Other'}),
            ('on_insulin', 'On insulin?', 'yesno', True, {}),
            ('insulin_dose', 'Total daily insulin dose', 'decimal', False,
             {'unit': 'units', 'show_if_key': 'on_insulin', 'show_if_value': 'yes'}),
            ('foot_exam', 'Foot examination findings', 'textarea', False, {}),
        ])]),
    ('cardiac-assessment', 'Cardiac assessment', 'single', ['cardiac'], [], [], [
        ('Assessment', [
            ('nyha', 'NYHA class', 'select', True, {'options': 'I\nII\nIII\nIV'}),
            ('oedema', 'Peripheral oedema?', 'yesno', True, {}),
        ])]),
    ('scd-assessment', 'Sickle cell assessment', 'single', ['scd'], [], [], [
        ('Assessment', [
            ('crises_12m', 'Pain crises in last 12 months', 'integer', True, {'min_value': 0, 'max_value': 100}),
            ('on_hydroxyurea', 'On hydroxyurea?', 'yesno', True, {}),
            ('transfusions_12m', 'Transfusions in last 12 months', 'integer', False, {'min_value': 0}),
        ])]),
]


class Command(BaseCommand):
    help = ('Load reference data: roles, demo geography and facilities, diseases, programmes, lab tests, medicines and '
            'starter forms. Facilities are DEMO placeholders: replace with the validated official list.')

    def add_arguments(self, parser):
        parser.add_argument('--demo-users', action='store_true',
                            help='Also create one demo account per role (development only).')
        parser.add_argument('--password', default='', help='Password for demo accounts.')

    @transaction.atomic
    def handle(self, *args, demo_users=False, password='', **options):
        call_command('setup_roles', stdout=self.stdout)
        districts = {}
        for zone_name, regions in GEOGRAPHY.items():
            zone, _ = Zone.objects.get_or_create(name=zone_name)
            for region_name, district_names in regions.items():
                region, _ = Region.objects.get_or_create(name=region_name, defaults={'zone': zone})
                for name in district_names:
                    districts[name], _ = District.objects.get_or_create(region=region, name=name)
        for code, name, district, kind, research in FACILITIES:
            Facility.objects.get_or_create(code=code, defaults={
                'name': name, 'district': districts[district], 'facility_type': kind, 'is_research_site': research})
        diseases = {}
        for code, name in DISEASES:
            diseases[code], _ = Disease.objects.get_or_create(code=code, defaults={'name': name})
        for code, name, disease in PROGRAMMES:
            ClinicalProgramme.objects.get_or_create(code=code, defaults={'name': name, 'disease': diseases[disease]})
        for code, name, specimen, unit, ref in LAB_TESTS:
            LabTest.objects.get_or_create(code=code, defaults={'name': name, 'specimen_type': specimen, 'unit': unit,
                                                               'reference_range': ref})
        for code, name, strength, form, unit, reorder in MEDICATIONS:
            Medication.objects.get_or_create(code=code, defaults={
                'name': name, 'strength': strength, 'dosage_form': form, 'dispensing_unit': unit,
                'reorder_level': reorder})
        for code, name, applicability, disease_codes, stages, locations, sections in FORMS:
            definition, created = FormDefinition.objects.get_or_create(code=code, defaults={
                'name': name, 'applicability': applicability, 'stages': stages, 'locations': locations})
            if not created:
                continue
            definition.diseases.set([diseases[c] for c in disease_codes])
            version = FormVersion.objects.create(definition=definition, number=1)
            for s_order, (title, fields) in enumerate(sections, start=1):
                section = FormSection.objects.create(version=version, title=title, order=s_order)
                for f_order, (key, label, ftype, required, extra) in enumerate(fields, start=1):
                    FormField.objects.create(section=section, key=key, label=label, field_type=ftype,
                                             required=required, order=f_order, **extra)
            publish_version(version, None)
        self.stdout.write(self.style.SUCCESS('Reference data loaded.'))
        if demo_users:
            self.create_demo_users(password)

    def create_demo_users(self, password):
        if len(password) < 10:
            self.stderr.write('Demo users need --password of at least 10 characters; skipped.')
            return
        User = get_user_model()
        facility = Facility.objects.get(code='DEMO-ILA-HC')
        for group in Group.objects.all():
            username = group.name.lower().replace(' / ', '_').replace(' ', '_').replace('/', '_')
            user, created = User.objects.get_or_create(username=username, defaults={
                'first_name': group.name, 'last_name': 'Demo', 'email': f'{username}@example.org',
                'job_title': group.name})
            if created:
                user.set_password(password)
                user.save()
                user.groups.add(group)
                level = 'national' if group.name in ('Geographic Coordinator', 'Monitor / Auditor', 'Report Viewer') else 'facility'
                StaffAssignment.objects.create(user=user, level=level,
                                               facility=facility if level == 'facility' else None)
                self.stdout.write(f'  demo user {username} ({level} scope)')

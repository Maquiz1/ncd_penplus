from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from clinical.models import ClinicalProgramme, Disease
from facilities.models import District, Facility, Region, Ward, Zone
from laboratory.models import LabCategory, LabPanel, LabTest
from pharmacy.models import Medication
from research.models import Study

# Wards as used by the PEN-Plus EDCS (Kondoa: Dodoma region, Karatu: Arusha region).
WARDS = {
    'Karatu': ['Baray', 'Buger', 'Daa', 'Endabash', 'Endamaghang', 'Endamarariek', 'Ganako', 'Kansay', 'Karatu',
               "Mang'ola", 'Mbulumbulu', 'Oldeani', 'Qurus', 'Rhotia'],
    'Kondoa': ['Bereke', 'Bolisa', 'Bumbuta', 'Busi', 'Changaa', 'Chemchem', 'Haubi', 'Hondo Mairo', 'Itaswi',
               'Itololo', 'Kalamba', 'Kikilo', 'Kikore', 'Kilimani', 'Kingale', 'Kinyasi', 'Kisese', 'Kolo',
               'Kondoa Mjini', 'Masange', 'Mnenia', 'Pahi', 'Salanka', 'Serya', 'Soera', 'Suruke', 'Thawi'],
}
# (zone, region, district)
GEOGRAPHY = [('Central Zone', 'Dodoma', 'Kondoa'), ('Central Zone', 'Dodoma', 'Dodoma City'),
             ('Northern Zone', 'Arusha', 'Karatu'), ('Northern Zone', 'Kilimanjaro', 'Moshi Municipal')]
# (code, name, district, type, research site)
FACILITIES = [
    ('KONDOA-DH', 'Kondoa District Hospital', 'Kondoa', 'hospital', True),
    ('KARATU-DH', 'Karatu District Hospital', 'Karatu', 'hospital', True),
    ('BMH', 'Benjamin Mkapa Hospital (referral / mentoring)', 'Dodoma City', 'hospital', False),
    ('KCMC', 'Kilimanjaro Christian Medical Centre (referral / mentoring)', 'Moshi Municipal', 'hospital', False),
]
EXTRA_DISEASES = [('other-ncd', 'Other NCD (respiratory, liver, kidney, other)')]
EXTRA_PROGRAMMES = [('other-ncd-care', 'Other NCD care', 'other-ncd')]
# Protocol Table 3 and the EDCS medication list, in addition to the starter catalogue.
MEDICATIONS = [
    ('atenolol-50', 'Atenolol', '50 mg', 'tablet', 'tablet', 100),
    ('carvedilol-6-25', 'Carvedilol', '6.25 mg', 'tablet', 'tablet', 100),
    ('carvedilol-12-5', 'Carvedilol', '12.5 mg', 'tablet', 'tablet', 100),
    ('spironolactone-25', 'Spironolactone', '25 mg', 'tablet', 'tablet', 100),
    ('methyldopa-250', 'Methyldopa', '250 mg', 'tablet', 'tablet', 100),
    ('hydralazine-25', 'Hydralazine', '25 mg', 'tablet', 'tablet', 100),
    ('isdn-10', 'Isosorbide dinitrate', '10 mg', 'tablet', 'tablet', 100),
    ('warfarin-5', 'Warfarin', '5 mg', 'tablet', 'tablet', 100),
    ('kcl-600', 'Potassium chloride', '600 mg', 'tablet', 'tablet', 100),
    ('omeprazole-20', 'Omeprazole', '20 mg', 'capsule', 'capsule', 100),
    ('lactulose', 'Lactulose', '3.3 g/5 mL', 'syrup', 'bottle', 10),
    ('losartan-50', 'Losartan', '50 mg', 'tablet', 'tablet', 100),
    ('amlodipine-10', 'Amlodipine', '10 mg', 'tablet', 'tablet', 100),
    ('nifedipine-20', 'Nifedipine', '20 mg', 'tablet', 'tablet', 100),
    ('digoxin-0-25', 'Digoxin', '0.25 mg', 'tablet', 'tablet', 50),
    ('clopidogrel-75', 'Clopidogrel', '75 mg', 'tablet', 'tablet', 50),
    ('insulin-mixtard', 'Insulin mixtard 30/70', '100 IU/mL 10 mL', 'injection', 'vial', 20),
    ('tramadol-50', 'Tramadol', '50 mg', 'capsule', 'capsule', 50),
    ('bisacodyl-5', 'Bisacodyl', '5 mg', 'tablet', 'tablet', 50),
    ('insulin-syringe', 'Insulin syringe', '1 mL', 'other', 'piece', 200),
    ('glucose-strips', 'Glucometer test strips', '', 'other', 'strip', 200),
]
# Lab catalogue. Units follow the values found in the EDCS (e.g. creatinine ≈ 1.0 → mg/dL) and are only a starting
# point: units and ranges are edited in Laboratory → Lab catalogue, never in code.
LAB_CATEGORIES = [('Haematology', 1), ('Clinical chemistry', 2), ('Diabetes', 3), ('Serology', 4),
                  ('Sickle cell testing', 5), ('Legacy (EDCS, to review)', 9)]
# (code, name, category, result type, unit, low, high, text range / choices)
LAB_TESTS = [
    ('hb', 'Haemoglobin (Hb)', 'Haematology', 'numeric', 'g/dL', '12', '17', ''),
    ('wbc', 'White blood cells (WBC)', 'Haematology', 'numeric', '×10⁹/L', '4', '11', ''),
    ('plt', 'Platelets (Plt)', 'Haematology', 'numeric', '×10⁹/L', '150', '400', ''),
    ('mcv', 'Mean cell volume (MCV)', 'Haematology', 'numeric', 'fL', '80', '100', ''),
    ('esr', 'ESR', 'Haematology', 'numeric', 'mm/hr', '0', '20', ''),
    ('blood-smear', 'Peripheral blood smear', 'Haematology', 'text', '', None, None, ''),
    ('na', 'Sodium (Na)', 'Clinical chemistry', 'numeric', 'mmol/L', '135', '145', ''),
    ('k', 'Potassium (K)', 'Clinical chemistry', 'numeric', 'mmol/L', '3.5', '5.1', ''),
    ('bun', 'Blood urea nitrogen (BUN)', 'Clinical chemistry', 'numeric', 'mg/dL', '7', '20', ''),
    ('creat', 'Creatinine', 'Clinical chemistry', 'numeric', 'mg/dL', '0.6', '1.3', ''),
    ('bnp', 'BNP', 'Clinical chemistry', 'numeric', 'pg/mL', None, '100', ''),
    ('inr', 'INR', 'Clinical chemistry', 'numeric', 'ratio', '0.8', '1.2', ''),
    ('alt', 'ALT (ALAT)', 'Clinical chemistry', 'numeric', 'U/L', '7', '56', ''),
    ('ast', 'AST (ASAT)', 'Clinical chemistry', 'numeric', 'U/L', '10', '40', ''),
    ('proteinuria', 'Proteinuria', 'Clinical chemistry', 'text', '', None, None, 'Negative'),
    ('lipids', 'Lipid panel', 'Clinical chemistry', 'text', 'mmol/L', None, None, ''),
    ('fbg', 'Fasting blood glucose', 'Diabetes', 'numeric', 'mmol/L', '3.9', '5.5', ''),
    ('rbg', 'Random blood glucose', 'Diabetes', 'numeric', 'mmol/L', None, '11.1', ''),
    ('hba1c', 'HbA1c', 'Diabetes', 'numeric', '%', '4', '5.6', ''),
    ('hiv', 'HIV test', 'Serology', 'choice', '', None, None, '!Positive\nNegative\nIndeterminate'),
    ('hbsag', 'Hepatitis B (HBsAg)', 'Serology', 'choice', '', None, None, '!Positive\nNegative'),
    ('hcv', 'Hepatitis C', 'Serology', 'choice', '', None, None, '!Positive\nNegative'),
    ('sickling', 'Sickling test', 'Sickle cell testing', 'choice', '', None, None, '!Positive\nNegative'),
    ('scd-result', 'SCD test result', 'Sickle cell testing', 'choice', '', None, None,
     'Presumptive diagnosis\nSickling test\n!SS\nSA\n!SBThal\n!SC\nOther'),
    ('scd-confirm', 'SCD confirmatory test', 'Sickle cell testing', 'choice', '', None, None,
     'HPLC\nHBE\nIEF\nBasique\nAcide\nHemotype SC\nSickle SCAN'),
    ('lft-legacy', 'LFTs (legacy, test not specified)', 'Legacy (EDCS, to review)', 'text', '', None, None, ''),
    ('fe-legacy', 'Iron studies (legacy, test not specified)', 'Legacy (EDCS, to review)', 'text', '', None, None, ''),
]
LAB_PANELS = [('fbc', 'Full blood count', ['hb', 'wbc', 'plt', 'mcv']),
              ('rft', 'Renal function', ['creat', 'bun']),
              ('electrolytes', 'Electrolytes', ['na', 'k']),
              ('lft', 'Liver function', ['alt', 'ast']),
              ('dm-control', 'Diabetes control', ['fbg', 'hba1c']),
              ('heart-failure', 'Heart failure monitoring', ['creat', 'na', 'k', 'bnp']),
              ('anticoag', 'Anticoagulation', ['inr'])]

ELIGIBILITY = """Screening (protocol v1.0):
Inclusion: aged 6 years and above; consenting individuals (assent for minors).
Exclusion: known SCD, diabetes or RHD patients; non-permanent residents.
Registry: all new and existing patients with type 1 diabetes, SCD and RHD (and other severe NCDs in scope) attending the
PEN-Plus clinics are enrolled and followed up for 2–3 years."""


class Command(BaseCommand):
    help = 'Configure the Tanzania PEN-Plus project: sites, wards, referral hospitals, study, diseases and medicines.'

    def add_arguments(self, parser):
        parser.add_argument('--deactivate-demo', action='store_true', help='Mark DEMO-* placeholder facilities inactive.')
        parser.add_argument('--reset-lab-catalogue', action='store_true',
                            help='Overwrite units/ranges of catalogue tests with the defaults (only before go-live).')

    @transaction.atomic
    def handle(self, *args, deactivate_demo=False, reset_lab_catalogue=False, **options):
        call_command('seed_reference_data', stdout=self.stdout)
        call_command('setup_diseases', stdout=self.stdout)
        districts = {}
        for zone_name, region_name, district_name in GEOGRAPHY:
            zone, _ = Zone.objects.get_or_create(name=zone_name)
            region, _ = Region.objects.get_or_create(name=region_name, defaults={'zone': zone})
            districts[district_name], _ = District.objects.get_or_create(region=region, name=district_name)
        for district_name, wards in WARDS.items():
            for ward in wards:
                Ward.objects.get_or_create(district=districts[district_name], name=ward)
        sites = []
        for code, name, district, kind, research in FACILITIES:
            facility, _ = Facility.objects.update_or_create(code=code, defaults={
                'name': name, 'district': districts[district], 'facility_type': kind, 'is_research_site': research,
                'is_active': True})
            if research:
                sites.append(facility)
        for code, name in EXTRA_DISEASES:
            Disease.all_objects.get_or_create(code=code, defaults={'name': name})
        for code, name, disease in EXTRA_PROGRAMMES:
            ClinicalProgramme.objects.get_or_create(code=code, defaults={
                'name': name, 'disease': Disease.objects.get(code=disease)})
        for code, name, strength, form, unit, reorder in MEDICATIONS:
            Medication.objects.get_or_create(code=code, defaults={
                'name': name, 'strength': strength, 'dosage_form': form, 'dispensing_unit': unit,
                'reorder_level': reorder})
        categories = {name: LabCategory.objects.get_or_create(name=name, defaults={'order': order})[0]
                      for name, order in LAB_CATEGORIES}
        for code, name, category, rtype, unit, low, high, extra in LAB_TESTS:
            values = {'name': name, 'category': categories[category], 'result_type': rtype, 'unit': unit,
                      'reference_low': low, 'reference_high': high,
                      'choices': extra if rtype == 'choice' else '',
                      'reference_range': extra if rtype == 'text' else ''}
            if reset_lab_catalogue:
                LabTest.objects.update_or_create(code=code, defaults=values)
            else:
                LabTest.objects.get_or_create(code=code, defaults=values)
        for code, name, tests in LAB_PANELS:
            panel, _ = LabPanel.objects.get_or_create(code=code, defaults={'name': name})
            panel.tests.add(*LabTest.objects.filter(code__in=tests))
        call_command('setup_investigations', stdout=self.stdout)
        study, created = Study.objects.get_or_create(code='penplus', defaults={
            'title': 'Improving access to Package of Essential services for severe Non-communicable Diseases in '
                     'Tanzania – PEN PLUS PROJECT',
            'protocol_number': 'Protocol v1.0, 15 October 2022 (NatHREC)',
            'status': Study.Status.ACTIVE, 'current_consent_version': '1.0',
            'start_date': '2022-12-01', 'eligibility_criteria': ELIGIBILITY,
            'description': 'Pre- and post-intervention implementation study of WHO PEN / PEN-Plus at Kondoa and '
                           'Karatu district hospitals. Chief Investigator: Dr Mary Theodory Mayige (NIMR).'})
        study.sites.set(sites)
        study.diseases.set(Disease.objects.filter(code__in=['diabetes', 'cardiac', 'scd']))
        if deactivate_demo:
            count = Facility.objects.filter(code__startswith='DEMO-').update(is_active=False)
            self.stdout.write(f'Deactivated {count} demo facilities.')
        self.stdout.write(self.style.SUCCESS(
            f'PEN-Plus configured: {len(sites)} sites, {Ward.objects.count()} wards, study {study.code} '
            f'({"created" if created else "updated"}).'))

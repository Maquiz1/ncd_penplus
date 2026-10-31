"""Migration of the previous PEN-Plus EDCS (PHP/MySQL) into this system.

Every EDCS record hangs off `clients.id`; other tables reference it as `client_id`
(visit) or `patient_id` (all forms). Here `clients.id` becomes `Patient.old_id`, and
per-visit rows attach to the encounter created from `visit.id` (their `vid`).
Each imported row is registered in ExternalRecord so the import can be re-run safely.
"""
import calendar
import datetime
import json
import re
import uuid
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.utils import timezone

from clinical.services import LTFU_REASON
from clinical.models import (ClinicalProgramme, Diagnosis, Disease, EnrollmentEvent, FamilyMember, ProgrammeEnrollment,
                             Screening)
from core.roles import EDCS_POSITIONS
from core.utils import add_months
from crf.models import FormDefinition, FormField, FormSection, FormSubmission, FormVersion
from crf.services import publish_version
from issues.detectors import raise_issue, run_all
from issues.models import DataIssue
from encounters.models import Admission, Appointment, Encounter, Vitals
from facilities.models import District, Facility, StaffAssignment, Ward
from investigations.models import InvestigationOrder
from laboratory.models import LabCategory, LabOrder, LabTest
from patients.models import Patient, PatientIdentifier
from patients.services import record_death, register_patient
from pharmacy.models import Medication, MedicationRecord
from users.models import Position
from research.models import ConsentRecord, Study, StudyEligibilityAssessment, StudyEnrollment

from .models import ExternalRecord

SOURCE = 'penplus_edcs'
DICTIONARY = json.loads((Path(__file__).parent / 'edcs_dictionary.json').read_text())
SITES = {1: 'KONDOA-DH', 2: 'KARATU-DH'}
POSITION_ROLES = {1: 'Data Manager', 2: 'Facility Coordinator', 3: 'PI / Co-Investigator',
                  5: 'Pharmacist / Dispenser', 6: 'Clinician', 7: 'Nurse / Triage Officer', 8: 'Laboratory Staff'}
ALL_SITE_POSITIONS = {1, 3}
STAGES = {'RV': 'registration', 'SV': 'screening', 'EV': 'baseline', 'FV': 'follow_up', 'TV': 'outcome',
          'UV': 'unscheduled'}
META_COLUMNS = {'id', 'study_id', 'visit_code', 'visit_day', 'vid', 'seq_no', 'patient_id', 'client_id', 'staff_id',
                'status', 'created_on', 'site_id'}
# Tables kept as read-only legacy forms (everything not mapped to a structured record is preserved here).
LEGACY_FORMS = {
    'screening': 'Screening', 'demographic': 'Demographics', 'main_diagnosis': 'Subject category',
    'history': 'Subject & family history', 'symptoms': 'Symptoms & examination', 'results': 'Results (ECG / echo / SCD)',
    'cardiac': 'Main diagnosis – cardiac', 'diabetic': 'Main diagnosis – diabetes', 'sickle_cell': 'Main diagnosis – sickle cell',
    'hospitalization': 'Hospitalization', 'hospitalization_details': 'Hospitalization details',
    'hospitalization_table': 'Hospitalization episodes', 'dgns_complctns_comorbdts': 'Diagnosis, complications & comorbidities',
    'risks': 'Risks', 'lab_details': 'Lab details & assessments', 'treatment_plan': 'Treatment plan',
    'sickle_cell_status_table': 'Sickle cell family status', 'summary': 'Visit summary & outcome',
    'social_economic': 'Socio-economic status',
}
REGISTRATION_FIELDS = {  # clients columns kept as a legacy registration form (identity fields live on Patient)
    'employment_status': ('Employment status', [('1', 'Employed'), ('2', 'Self-employed'), ('3', 'Employed but on leave'),
                                                ('4', 'Unemployed'), ('5', 'Student')]),
    'education_level': ('Education level', [('1', 'Not attended school'), ('2', 'Primary'), ('3', 'Secondary'),
                                            ('4', 'Certificate'), ('5', 'Diploma'), ('6', 'Undergraduate degree'),
                                            ('7', 'Postgraduate degree'), ('8', 'N/A')]),
    'occupation': ('Occupational exposures', [('1', 'Yes'), ('2', 'No')]),
    'exposure': ('Exposure (if yes)', None),
    'relation': ('Relation of supporter to subject', [('1', 'Mzazi'), ('2', 'Kaka / Dada'), ('3', 'Mwenza'),
                                                      ('4', 'Ndugu wengine'), ('5', 'Rafiki'), ('96', 'Wengine')]),
    'relation_patient': ('Relation (other)', None),
    'gender_gurdian': ('Guardian sex', [('1', 'Male'), ('2', 'Female')]),
    'age_gurdian': ('Guardian age', None),
    'occupation_gurdian': ('Guardian employment status', None),
    'education_gurdian': ('Guardian education level', None),
    'cardiac': ('Subject for cardiac', [('1', 'Yes'), ('2', 'No')]),
    'diabetes': ('Subject for diabetes', [('1', 'Yes'), ('2', 'No')]),
    'sickle_cell': ('Subject for sickle cell', [('1', 'Yes'), ('2', 'No')]),
    'other': ('Other disease', [('1', 'Yes'), ('2', 'No')]),
    'other_diseases': ('Other diseases (specify)', None),
    'dignosis_type': ('Subject category', [('1', 'Cardiac'), ('2', 'Diabetes'), ('3', 'Sickle cell'), ('96', 'Other')]),
    'screened': ('Screened', None), 'eligible': ('Eligible', None), 'enrolled': ('Enrolled', None),
    'end_study': ('Ended study', None), 'comments': ('Comments', None),
}
LAB_COLUMNS = {
    'lab_details': {'na': 'na', 'k': 'k', 'bun': 'bun', 'cre': 'creat', 'bnp': 'bnp', 'inr': 'inr', 'na_diabetes': 'na',
                    'k_diabetes': 'k', 'cre_diabetes': 'creat', 'proteinuria': 'proteinuria', 'lipid_panel': 'lipids',
                    'wbc': 'wbc', 'hb': 'hb', 'mcv': 'mcv', 'plt': 'plt', 'fe_studies': 'fe-legacy', 'lfts': 'lft-legacy'},
    'symptoms': {'fasting': 'fbg', 'random_fs': 'rbg', 'hba1c': 'hba1c', 'hb': 'hb', 'wbc': 'wbc', 'plt': 'plt'},
}
SCD_RESULT = {'1': 'Presumptive diagnosis', '2': 'Sickling test', '3': 'SS', '4': 'SA', '5': 'SBThal', '6': 'SC',
              '96': 'Other'}
SCD_CONFIRM = {'1': 'HPLC', '2': 'HBE', '3': 'IEF', '4': 'Basique', '5': 'Acide', '6': 'Hemotype SC', '7': 'Sickle SCAN'}
CARDIAC_FLAGS = [
    ('cardiomyopathy', 'Cardiomyopathy', 'sub_cardiomyopathy', 'cardiomyopathy_other'),
    ('heumatic', 'Rheumatic heart disease', 'sub_heumatic', 'heumatic_other'),
    ('congenital', 'Congenital heart disease', 'sub_congenital', 'congenital_other'),
    ('severe_hypertension', 'Severe / uncontrolled hypertension', None, None),
    ('hypertensive_heart', 'Hypertensive heart disease', None, None),
    ('coronary_artery', 'Coronary artery disease', None, None),
    ('heart_failure', 'Right heart failure', 'sub_heart_failure', 'heart_failure_other'),
    ('pericardial', 'Pericardial disease', 'sub_pericardial', 'pericardial_other'),
    ('arrhythmia', 'Arrhythmia', 'sub_arrhythmia', 'arrhythmia_other'),
    ('stroke', 'Stroke', 'sub_stroke', None),
    ('thromboembolic', 'Thromboembolic disease', 'sub_thromboembolic', 'thromboembolic_other'),
    ('diagnosis_other', 'Other cardiac diagnosis', None, 'diagnosis_specify'),
]
DM_TYPES = {'1': 'Type 1 diabetes mellitus', '2': 'Type 2 diabetes mellitus', '3': 'Gestational diabetes',
            '4': 'Diabetes mellitus, type not yet specified', '96': 'Other diabetes'}
SCD_TYPES = {'1': 'Sickle cell disease', '2': 'Other haemoglobinopathy'}
CATEGORY_DISEASE = {'1': 'cardiac', '2': 'diabetes', '3': 'scd', '96': 'other-ncd'}
OUTCOMES = {'1': ('active', ''), '2': ('ltfu', 'Defaulted (EDCS)'), '3': ('withdrawn', 'Stopped treatment (EDCS)'),
            '4': ('transferred_out', 'Transferred out (EDCS)'), '5': ('deceased', 'Died (EDCS)')}
TERMINAL_OUTCOMES = {'2', '3', '4', '5'}  # default, stopped/withdrawn, transfer out, death
STUDY_STATUS = {'active': 'active', 'ltfu': 'ltfu', 'other': 'other', 'withdrawn': 'withdrawn', 'transferred_out': 'transferred',
                'deceased': 'deceased'}
MED_ACTIONS = {'1': 'continue', '2': 'start', '3': 'stop', '4': 'not_eligible'}
# EDCS uses these as 'not done / missing' in lab fields (e.g. 93 of 96 sodium values are 98 or 99).
def edcs_lab_request(patient_pk, date):
    """Stable request id for the EDCS lab results of one subject on one day."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f'edcs-lab/{patient_pk}/{date}')


MISSING_LAB_VALUES = {'0', '00', '000', '0.0', '98', '99', '98.0', '99.0', '-', '--', '.', 'n/a', 'na'}
# Old EDCS test_list names (normalised prefix) that are tests we already have.
EDCS_TEST_MATCH = {'fullbloodpicture': 'cbc', 'hblevel': 'hb', 'sicklingtest': 'sickling', 'peripheralbloodsmear': 'blood-smear',
                   'esr': 'esr', 'hivtest': 'hiv', 'hepatitisb': 'hbsag', 'hepatitisc': 'hcv', 'creatinine': 'creat',
                   'alat': 'alt', 'asat': 'ast', 'rbg': 'rbg', 'fbg': 'fbg', 'hba1c': 'hba1c'}
# A hospital ID shared by this many patients is a placeholder (e.g. "Kth001"), not a real identifier.
PLACEHOLDER_ID_THRESHOLD = 5
ECHO_FIELDS = ['echo', 'lv', 'mitral', 'rv', 'pericardial', 'ivc', 'thrombus', 'congenital_defect', 'echo_specify',
               'echo_other2']


def text(value):
    return '' if value is None else str(value).strip()


def parse_date(value):
    match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', text(value))
    if not match:
        return None
    try:
        date = datetime.date(*(int(g) for g in match.groups()))
    except ValueError:
        return None
    return date if 1900 <= date.year <= timezone.localdate().year + 2 else None


def parse_any_date(value):
    """parse_date, also accepting the day-first forms typed in some EDCS fields (12-05-2022, 12/05/2022)."""
    date = parse_date(value)
    if date:
        return date
    match = re.search(r'\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b', text(value))
    if not match:
        return None
    try:
        date = datetime.date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None
    return date if 1900 <= date.year <= timezone.localdate().year + 2 else None


def legacy_test_values(row):
    """Result type, unit and range for an old EDCS test_list row (its "units" often describe the answer)."""
    units = re.sub(r'\s+', ' ', text(row.get('units'))).strip()
    folded = units.casefold()
    for pair, options in (('positive or negative', 'Negative\n!Positive'), ('reactive or non-reactive', 'Non-reactive\n!Reactive'),
                          ('detected or not detected', 'Not detected\n!Detected')):
        if pair in folded:
            return {'result_type': 'choice', 'choices': options}
    low, high = decimal(row.get('minimum')), decimal(row.get('maximum'), max_value=Decimal('100000'))
    if low is not None and high is not None and low < high and len(units) <= 30:
        return {'result_type': 'numeric', 'unit': units, 'reference_low': low, 'reference_high': high}
    if units in ('', '-'):
        return {'result_type': 'text'}
    return {'result_type': 'text', 'reference_range': units[:100]}


def at_nine(date):
    return timezone.make_aware(datetime.datetime.combine(date, datetime.time(9, 0)))


def decimal(value, max_value=None):
    try:
        number = Decimal(text(value).replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0 or (max_value is not None and number > max_value):
        return None
    return number.quantize(Decimal('0.1'))


def option_label(table, column, code):
    for value, label in DICTIONARY.get(table, {}).get(column, {}).get('options', []):
        if value == text(code):
            return label
    return text(code)


class MySQLReader:
    """Read-only access to the EDCS database."""

    def __init__(self, config):
        import pymysql
        self.conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, charset='utf8mb4', **config)
        with self.conn.cursor() as cursor:
            cursor.execute('SET SESSION TRANSACTION READ ONLY')

    def rows(self, table):
        with self.conn.cursor() as cursor:
            cursor.execute(f'SELECT * FROM `{table}` ORDER BY id')
            return list(cursor.fetchall())

    def columns(self, table):
        with self.conn.cursor() as cursor:
            cursor.execute(f'SHOW COLUMNS FROM `{table}`')
            return [row['Field'] for row in cursor.fetchall()]


class Importer:
    def __init__(self, reader, log=print):
        self.reader = reader
        self.log = log
        self.counts = Counter()
        self.issues = defaultdict(list)
        self.cache = {}
        self.today = timezone.localdate()

    # ------------------------------------------------------------ helpers
    def rows(self, table):
        if table not in self.cache:
            self.cache[table] = self.reader.rows(table)
        return self.cache[table]

    def mapped(self, table, source_id, model):
        ct = ContentType.objects.get_for_model(model)
        record = ExternalRecord.objects.filter(source=SOURCE, table=table, source_id=str(source_id),
                                               content_type=ct).first()
        if record is None:
            return None
        manager = getattr(model, 'all_objects', model.objects)
        return manager.filter(pk=record.object_id).first()

    def remember(self, table, source_id, obj, notes=''):
        ExternalRecord.objects.get_or_create(
            source=SOURCE, table=table, source_id=str(source_id), content_type=ContentType.objects.get_for_model(obj),
            defaults={'object_id': str(obj.pk), 'notes': notes[:255]})

    def issue(self, kind, detail):
        self.issues[kind].append(detail)

    def staff(self, staff_id):
        return self.staff_map.get(text(staff_id)) or self.import_user

    def facility(self, site_id):
        return self.facilities.get(text(site_id)) or self.facilities['1']

    def patient(self, client_id):
        return self.patients.get(text(client_id))

    # ------------------------------------------------------------ run
    # Steps that only add new kinds of records and can be re-run on their own (`--steps`).
    ADD_ON_STEPS = ['lab_catalogue', 'lab_requests', 'admissions', 'family_members']

    def run(self, steps=None):
        """Full migration, or only the named ADD_ON_STEPS on top of an earlier full migration."""
        self.prepare()
        if steps:
            # Already-imported staff, patients and visits are only looked up (re-runs never duplicate).
            self.import_staff()
            self.import_patients()
            self.import_visits()
            if 'lab_requests' in steps and 'lab_catalogue' not in steps:
                steps = ['lab_catalogue'] + list(steps)
            for step in steps:
                getattr(self, f'import_{step}')()
            return self.counts, self.issues
        self.import_staff()
        self.import_patients()
        self.import_visits()
        self.import_screening()
        self.import_diagnoses()
        self.import_enrollments()
        self.import_vitals()
        self.import_labs()
        self.import_investigations()
        self.flag_echo_findings()
        self.import_medications()
        self.import_lab_catalogue()
        self.import_lab_requests()
        self.import_admissions()
        self.import_family_members()
        self.note_old_stock()
        self.mark_edcs_origin()
        self.apply_outcome_timeline()
        self.fill_missed_months()
        self.import_legacy_forms()
        self.continue_legacy_forms()
        self.apply_deaths()
        self.apply_soft_deletes()
        self.find_duplicates()
        self.raise_data_issues()
        return self.counts, self.issues

    # ------------------------------------------------------------ staff
    def prepare(self):
        User = get_user_model()
        self.import_user, _ = User.objects.get_or_create(
            username='edcs_import', defaults={'first_name': 'EDCS', 'last_name': 'import', 'is_active': False})
        self.facilities = {str(k): Facility.objects.get(code=v) for k, v in SITES.items()}
        self.study = Study.objects.get(code='penplus')
        self.diseases = {d.code: d for d in Disease.objects.all()}
        self.programmes = {p.disease.code: p for p in ClinicalProgramme.objects.select_related('disease')}
        self.lab_tests = {t.code: t for t in LabTest.objects.all()}

    def import_staff(self):
        User = get_user_model()
        positions = {text(r['id']): text(r['name']) for r in self.rows('position')}
        self.staff_map = {}
        for row in self.rows('user'):
            user = self.mapped('user', row['id'], User)
            if user is None:
                username = text(row['username']) or f'edcs{row["id"]}'
                if User.objects.filter(username=username).exists():
                    username = f'{username}_edcs'
                user = User(username=username, first_name=text(row['firstname'])[:150],
                            last_name=text(row['lastname'])[:150], email=text(row.get('email_address')),
                            phone=text(row.get('phone_number'))[:30], is_active=text(row['status']) == '1',
                            position=Position.objects.filter(code=EDCS_POSITIONS.get(int(row['position'] or 0))).first(),
                            job_title=positions.get(text(row['position']), '')[:100])
                if user.position_id:
                    user.job_title = ''
                user.set_unusable_password()
                user.save()
                role = POSITION_ROLES.get(int(row['position'] or 0))
                if role:
                    user.groups.add(Group.objects.get(name=role))
                else:
                    self.issue('staff_without_role', f'EDCS user {row["id"]} position {row["position"]}')
                sites = self.facilities.values() if int(row['position'] or 0) in ALL_SITE_POSITIONS \
                    else [self.facility(row['site_id'])]
                for facility in sites:
                    StaffAssignment.objects.create(user=user, level='facility', facility=facility,
                                                   created_by=self.import_user)
                self.remember('user', row['id'], user)
                self.counts['staff created'] += 1
            self.staff_map[text(row['id'])] = user

    # ------------------------------------------------------------ patients
    def import_patients(self):
        wards = {text(r['id']): (text(r['name']), text(r['district_id'])) for r in self.rows('wards')}
        districts = {text(r['id']): text(r['name']) for r in self.rows('districts')}
        our_districts = {d.name.casefold(): d for d in District.objects.all()}
        our_wards = {(w.district_id, w.name.casefold().replace(' ', '')): w for w in Ward.objects.select_related('district')}
        self.patients = {}
        id_use = Counter(text(r['hospital_id']).casefold() for r in self.rows('clients') if text(r['hospital_id']))
        placeholders = {v for v, n in id_use.items() if n >= PLACEHOLDER_ID_THRESHOLD} | {'0', '00', '000'}
        for row in self.rows('clients'):
            existing = Patient.all_objects.filter(old_id=row['id']).first()
            if existing:
                self.patients[text(row['id'])] = existing
                continue
            facility = self.facility(row['site_id'])
            registration = parse_date(row['clinic_date']) or parse_date(row['created_on']) or self.today
            dob, estimated = parse_date(row['dob']), False
            if dob is None:
                age = decimal(row.get('age')) or decimal(row.get('age2'))
                if age is not None and age < 120:
                    dob, estimated = datetime.date(registration.year - int(age), 7, 1), True
                else:
                    dob, estimated = datetime.date(1900, 1, 1), True
                    self.issue('missing_date_of_birth', f'old ID {row["id"]}')
            district = our_districts.get(districts.get(text(row['district']), '').casefold())
            ward_name, ward_district = wards.get(text(row['ward']), ('', ''))
            ward_district_obj = our_districts.get(districts.get(ward_district, '').casefold())
            ward = our_wards.get((ward_district_obj.pk if ward_district_obj else None,
                                  ward_name.casefold().replace(' ', ''))) if ward_name else None
            guardian = ' '.join(p for p in (text(row['guardian_name']), text(row['middlename_gurdian']),
                                            text(row['lastname_gurdian'])) if p)
            patient = Patient(
                first_name=text(row['firstname']) or 'Unknown', middle_name=text(row['middlename']),
                last_name=text(row['lastname']) or 'Unknown',
                sex={'1': 'male', '2': 'female'}.get(text(row['gender']), 'other'),
                date_of_birth=dob, dob_estimated=estimated, phone=text(row['phone_number'])[:30],
                alternate_phone=text(row['phone_number2'])[:30], address=text(row['physical_address'])[:255],
                residence_district=district or (ward.district if ward else None), residence_ward=ward,
                next_of_kin_name=guardian[:200], next_of_kin_phone=text(row['guardian_phone'])[:30],
                registration_facility=facility, registration_date=registration)
            patient = register_patient(patient, self.staff(row['staff_id']))
            patient.old_id = row['id']
            patient.save(update_fields=['old_id'])
            for id_type, value in (('hospital', text(row['hospital_id'])), ('edcs_study', text(row['study_id']))):
                if not value:
                    continue
                if id_type == 'hospital' and value.casefold() in placeholders:
                    self.issue('placeholder_hospital_id_not_imported', f'{value}: old ID {row["id"]} ({patient.patient_number})')
                    continue
                issuer = facility
                if PatientIdentifier.objects.filter(id_type=id_type, value=value, issuer=facility).exists():
                    issuer = None
                    self.issue(f'duplicate_{id_type}_id', f'{value}: old ID {row["id"]} ({patient.patient_number})')
                PatientIdentifier.objects.create(patient=patient, id_type=id_type, value=value[:100], issuer=issuer,
                                                 created_by=self.import_user)
            self.remember('clients', row['id'], patient)
            self.patients[text(row['id'])] = patient
            self.counts['subjects created'] += 1

    # ------------------------------------------------------------ visits
    def import_visits(self):
        self.encounters = {}
        for row in self.rows('visit'):
            patient = self.patient(row['client_id'])
            if patient is None:
                self.issue('visit_without_patient', f'visit {row["id"]} client {row["client_id"]}')
                continue
            if text(row['status']) != '1':
                self.counts['visits skipped (deleted in EDCS)'] += 1
                continue
            existing = self.mapped('visit', row['id'], Encounter)
            if existing:
                self.encounters[text(row['id'])] = existing
                continue
            if self.mapped('visit', row['id'], Appointment):
                continue
            code = text(row['visit_code'])
            stage = STAGES.get(code, 'follow_up')
            expected, actual = parse_date(row['expected_date']), parse_date(row['visit_date'])
            facility = self.facility(row['site_id'])
            status = text(row['visit_status'])
            when = actual or expected
            if when is None:
                self.issue('visit_without_valid_date', f'visit {row["id"]} ({row["expected_date"]!r}/{row["visit_date"]!r})')
                continue
            note = f'EDCS {row["visit_name"] or code} #{row["seq_no"]}'[:255]
            appointment = None
            if code != 'UV' or status != '1':
                if status == '1':
                    a_status = 'attended'
                elif status == '2':
                    a_status = 'missed'
                else:
                    a_status = 'scheduled' if (expected or when) >= self.today else 'expected'
                appointment = Appointment.objects.create(
                    patient=patient, facility=facility, scheduled_date=expected or when, stage=stage, status=a_status,
                    notes=note, reason_for_change=text(row['reasons'])[:255], created_by=self.import_user)
                self.remember('visit', row['id'], appointment)
                self.counts['appointments created'] += 1
            if status == '1':
                encounter = Encounter.objects.create(
                    patient=patient, facility=facility, appointment=appointment, encounter_date=at_nine(when),
                    stage=stage, status='completed', staff=self.import_user, plan_notes=text(row['next_notes']),
                    clinical_notes=text(row['comments']), created_by=self.import_user)
                self.remember('visit', row['id'], encounter)
                self.encounters[text(row['id'])] = encounter
                self.counts['encounters created'] += 1

    def encounter_for(self, row):
        return self.encounters.get(text(row.get('vid')))

    def row_date(self, row, *columns):
        for column in columns:
            date = parse_date(row.get(column))
            if date:
                return date
        encounter = self.encounter_for(row)
        return timezone.localtime(encounter.encounter_date).date() if encounter else None

    def active_rows(self, table):
        for row in self.rows(table):
            if text(row.get('status')) == '1':
                patient = self.patient(row.get('patient_id'))
                if patient is None:
                    self.issue(f'{table}_without_patient', f'{table} {row["id"]}')
                    continue
                yield row, patient

    # ------------------------------------------------------------ screening
    def import_screening(self):
        for row, patient in self.active_rows('screening'):
            date = self.row_date(row, 'screening_date', 'conset_date') or patient.registration_date
            for column, disease in (('dm', 'diabetes'), ('rhd', 'cardiac'), ('scd', 'scd')):
                value = text(row.get(column))
                if value not in ('1', '2') or self.mapped(f'screening.{column}', row['id'], Screening):
                    continue
                screening = Screening.objects.create(
                    patient=patient, disease=self.diseases[disease], facility=self.facility(row['site_id']),
                    source='facility', location_type='facility', screening_date=date,
                    result='positive' if value == '1' else 'negative', consent_obtained=text(row['consent']) == '1',
                    notes=text(row.get('remarks')), screened_by=self.staff(row['staff_id']),
                    encounter=self.encounter_for(row), created_by=self.import_user)
                self.remember(f'screening.{column}', row['id'], screening)
                self.counts['screenings created'] += 1

    # ------------------------------------------------------------ diagnoses
    def add_diagnosis(self, key, row, patient, disease, description):
        if self.mapped(key, row['id'], Diagnosis):
            return
        diagnosis = Diagnosis.objects.create(
            patient=patient, disease=self.diseases[disease], description=description[:255], status='confirmed',
            diagnosis_date=self.row_date(row, 'diagnosis_date', 'visit_date') or patient.registration_date,
            encounter=self.encounter_for(row), clinician=self.staff(row.get('staff_id')),
            notes=text(row.get('comments')), created_by=self.import_user)
        self.remember(key, row['id'], diagnosis)
        self.counts['diagnoses created'] += 1

    def import_diagnoses(self):
        for row, patient in self.active_rows('cardiac'):
            for flag, name, sub, other in CARDIAC_FLAGS:
                if text(row.get(flag)) != '1':
                    continue
                detail = ''
                if sub and text(row.get(sub)):
                    detail = option_label('cardiac', sub, row[sub])
                    if text(row[sub]) == '96':
                        detail = f'Other: {text(row.get(other))}' if other and text(row.get(other)) else 'Other'
                elif other and text(row.get(other)):
                    detail = text(row[other])
                self.add_diagnosis(f'cardiac.{flag}', row, patient, 'cardiac', f'{name} – {detail}' if detail else name)
        for row, patient in self.active_rows('diabetic'):
            code = text(row.get('diagnosis'))
            label = text(row.get('diagnosis_other')) if code == '96' and text(row.get('diagnosis_other')) else \
                DM_TYPES.get(code, 'Diabetes mellitus')
            self.add_diagnosis('diabetic', row, patient, 'diabetes', label)
        for row, patient in self.active_rows('sickle_cell'):
            code = text(row.get('diagnosis'))
            label = text(row.get('diagnosis_other')) if code == '2' and text(row.get('diagnosis_other')) else \
                SCD_TYPES.get(code, 'Sickle cell disease')
            self.add_diagnosis('sickle_cell', row, patient, 'scd', label)
        diagnosed = set(Diagnosis.objects.values_list('patient_id', 'disease__code'))
        for row, patient in self.active_rows('main_diagnosis'):
            for column, disease in (('cardiac', 'cardiac'), ('diabetes', 'diabetes'), ('sickle_cell', 'scd')):
                if text(row.get(column)) == '1' and (patient.pk, disease) not in diagnosed:
                    self.add_diagnosis(f'main_diagnosis.{column}', row, patient, disease,
                                       f'{self.diseases[disease].name} (category recorded in EDCS)')
                    diagnosed.add((patient.pk, disease))

    # ------------------------------------------------------------ enrollments and outcomes
    def latest_outcomes(self):
        latest = {}
        for row, patient in self.active_rows('summary'):
            code = text(row.get('outcome'))
            date = self.row_date(row, 'summary_date', 'visit_date')
            if code not in OUTCOMES or date is None:
                continue
            if patient.pk not in latest or date >= latest[patient.pk][1]:
                latest[patient.pk] = (code, date, row)
        return latest

    def import_enrollments(self):
        outcomes = self.latest_outcomes()
        self.deaths = {}
        enrollment_dates = {}
        for row in self.rows('visit'):
            if text(row.get('visit_code')) == 'EV' and text(row.get('status')) == '1':
                date = parse_date(row['visit_date']) or parse_date(row['expected_date'])
                if date:
                    enrollment_dates.setdefault(text(row['client_id']), date)
        screenings = {text(r['patient_id']): r for r in self.rows('screening') if text(r.get('status')) == '1'}
        participant_ids = Counter()
        for row in self.rows('clients'):
            patient = self.patient(row['id'])
            if patient is None or text(row.get('enrolled')) != '1':
                continue
            facility = self.facility(row['site_id'])
            start = enrollment_dates.get(text(row['id'])) or patient.registration_date
            code, outcome_date, summary = outcomes.get(patient.pk, ('1', None, None))
            status, reason = OUTCOMES[code]
            if code == '5':
                death_date = parse_date(summary.get('death_date')) or outcome_date
                cause = text(summary.get('death_other')) or option_label('summary', 'cause_death', summary.get('cause_death'))
                self.deaths[patient.pk] = (death_date, cause or 'Recorded as death in EDCS')
            diseases = [d for column, d in (('cardiac', 'cardiac'), ('diabetes', 'diabetes'), ('sickle_cell', 'scd'))
                        if text(row.get(column)) == '1']
            if not diseases and CATEGORY_DISEASE.get(text(row.get('dignosis_type'))):
                diseases = [CATEGORY_DISEASE[text(row['dignosis_type'])]]
            if not diseases:
                self.issue('enrolled_without_condition', f'old ID {row["id"]} ({patient.patient_number})')
            for disease in diseases:
                if self.mapped(f'clients.programme.{disease}', row['id'], ProgrammeEnrollment):
                    continue
                enrollment = ProgrammeEnrollment.objects.create(
                    patient=patient, programme=self.programmes[disease], facility=facility, enrollment_date=start,
                    status=status, outcome_date=outcome_date if status != 'active' else None,
                    outcome_reason=reason, responsible_team='PEN-Plus clinic', created_by=self.import_user)
                self.remember(f'clients.programme.{disease}', row['id'], enrollment)
                self.counts['programme enrollments created'] += 1
            if self.mapped('clients', row['id'], StudyEnrollment):
                continue
            screen = screenings.get(text(row['id']))
            eligibility = StudyEligibilityAssessment.objects.create(
                patient=patient, study=self.study, assessed_on=start, is_eligible=True,
                criteria_notes='Eligible and enrolled in the PEN-Plus EDCS.', assessed_by=self.staff(row['staff_id']),
                created_by=self.import_user)
            consent_date = parse_date(screen.get('conset_date')) if screen else None
            consent_note = '' if screen and text(screen.get('consent')) == '1' else \
                'Enrolled in EDCS without a consent entry: verify the paper consent form.'
            if consent_note:
                self.issue('consent_to_verify', f'old ID {row["id"]} ({patient.patient_number})')
            consent = ConsentRecord.objects.create(
                patient=patient, study=self.study, consent_version='1.0', decision='consented',
                consent_date=at_nine(consent_date or start), obtained_by=self.staff(row['staff_id']),
                notes=consent_note, created_by=self.import_user)
            participant = text(row['study_id']) or f'EDCS-{row["id"]}'
            participant_ids[participant] += 1
            if participant_ids[participant] > 1 or StudyEnrollment.objects.filter(study=self.study,
                                                                                    participant_id=participant).exists():
                participant = f'{participant}-dup{row["id"]}'
                self.issue('duplicate_study_id', f'{row["study_id"]}: old ID {row["id"]} stored as {participant}')
            study_status = 'completed' if text(row.get('end_study')) == '1' and status == 'active' else \
                STUDY_STATUS[status]
            enrollment = StudyEnrollment.objects.create(
                patient=patient, study=self.study, participant_id=participant, facility=facility,
                eligibility=eligibility, consent=consent, enrollment_date=start, status=study_status,
                outcome_date=outcome_date if study_status != 'active' else None,
                outcome_reason=reason or ('Study completed (EDCS)' if study_status == 'completed' else ''),
                created_by=self.import_user)
            self.remember('clients', row['id'], enrollment)
            self.counts['study enrollments created'] += 1

    # ------------------------------------------------------------ vitals
    def import_vitals(self):
        for row, patient in self.active_rows('vital'):
            encounter = self.encounter_for(row)
            if encounter is None:
                self.issue('vitals_without_visit', f'vital {row["id"]}')
                continue
            if self.mapped('vital', row['id'], Vitals):
                continue
            values = dict(height_cm=decimal(row.get('height'), 250), weight_kg=decimal(row.get('weight'), 400),
                          muac_cm=decimal(row.get('muac'), 99), systolic_bp=decimal(row.get('systolic'), 300),
                          diastolic_bp=decimal(row.get('dystolic'), 250), pulse=decimal(row.get('pr'), 300))
            if not any(v is not None for v in values.values()):
                continue
            for column in ('pr', 'weight', 'height', 'systolic', 'dystolic'):
                if text(row.get(column)) in ('98', '99'):
                    self.issue('vitals_value_98_or_99_review', f'vital {row["id"]} {column}={row[column]}')
            for key in ('systolic_bp', 'diastolic_bp', 'pulse'):
                values[key] = int(values[key]) if values[key] is not None else None
            vitals = Vitals.objects.create(encounter=encounter, taken_at=encounter.encounter_date,
                                           created_by=self.staff(row.get('staff_id')), **values)
            self.remember('vital', row['id'], vitals)
            self.counts['vital signs created'] += 1

    # ------------------------------------------------------------ labs
    def add_lab(self, key, row, patient, test_code, raw, date):
        if self.mapped(key, row['id'], LabOrder):
            return
        encounter = self.encounter_for(row)
        test = self.lab_tests[test_code]
        # Tests of the same subject and day form one request; EDCS results were already confirmed there.
        group = edcs_lab_request(patient.pk, date)
        same = LabOrder.objects.filter(test=test, result_value=raw[:100].strip())
        if same.filter(request_group=group).exists() or (encounter and same.filter(encounter=encounter).exists()):
            self.counts['duplicate lab results skipped (same result in several EDCS forms)'] += 1
            return
        user = self.staff(row.get('staff_id'))
        when = at_nine(date)
        order = LabOrder(patient=patient, encounter=encounter, facility=self.facility(row.get('site_id')), test=test,
                         ordered_by=user, ordered_at=when, created_by=self.import_user,
                         request_group=group, clinical_notes='Imported from EDCS')
        order.record_result(raw[:100], user, when=when)
        order.status, order.reviewed_by, order.reviewed_at = LabOrder.Status.REVIEWED, user, when
        order.save()
        self.remember(key, row['id'], order)
        self.counts['lab results created'] += 1

    def import_labs(self):
        for table, columns in LAB_COLUMNS.items():
            for row, patient in self.active_rows(table):
                date = self.row_date(row, 'lab_date', 'visit_date')
                if date is None:
                    continue
                for column, test_code in columns.items():
                    raw = text(row.get(column))
                    if raw.casefold() in MISSING_LAB_VALUES:
                        if raw:
                            self.counts['lab values skipped (0/98/99 missing codes)'] += 1
                        continue
                    if raw:
                        self.add_lab(f'{table}.{column}', row, patient, test_code, raw, date)
        for row, patient in self.active_rows('results'):
            date = self.row_date(row, 'visit_date')
            if date is None:
                continue
            code = text(row.get('scd_test'))
            if code in SCD_RESULT:
                label = text(row.get('scd_test_other')) if code == '96' and text(row.get('scd_test_other')) else SCD_RESULT[code]
                self.add_lab('results.scd_test', row, patient, 'scd-result', label, date)
            code = text(row.get('confirmatory_test_type'))
            if code in SCD_CONFIRM:
                self.add_lab('results.confirmatory_test_type', row, patient, 'scd-confirm', SCD_CONFIRM[code], date)

    # ------------------------------------------------------------ ECG / echo
    def import_investigations(self):
        for row, patient in self.active_rows('results'):
            date = self.row_date(row, 'visit_date')
            if date is None:
                continue
            exams = []
            if text(row.get('ecg_performed')) == '1':
                findings = [f'{label}: {value}' for label, value in (
                    ('Rhythm / type', option_label('results', 'ecg_date', row.get('ecg_date'))),
                    ('ECG', text(row.get('ecg'))), ('Other', text(row.get('ecg_other')))) if value]
                exams.append(('ecg', 'ECG', findings, date))
            if text(row.get('echo_performed')) == '1':
                findings = []
                for column in ECHO_FIELDS:
                    value = text(row.get(column))
                    if value:
                        label = DICTIONARY.get('results', {}).get(column, {}).get('label', column)
                        findings.append(f'{label}: {option_label("results", column, value)}')
                exams.append(('echo', 'Echocardiography', findings, parse_date(row.get('echo_date')) or date))
            for modality, name, findings, when in exams:
                key = f'results.{modality}'
                if self.mapped(key, row['id'], InvestigationOrder):
                    continue
                user = self.staff(row.get('staff_id'))
                order = InvestigationOrder.objects.create(
                    patient=patient, encounter=self.encounter_for(row), facility=self.facility(row.get('site_id')),
                    modality=modality, description=f'{name} (imported from EDCS)', status='reviewed',
                    requested_by=user, requested_at=at_nine(when), performed_at=at_nine(when), reported_by=user,
                    reviewed_by=user, reviewed_at=at_nine(when),
                    findings='\n'.join(findings) or 'Performed; no findings recorded in EDCS.',
                    created_by=self.import_user)
                self.remember(key, row['id'], order)
                self.counts['ECG/echo reports created'] += 1

    # ------------------------------------------------------------ medications
    def import_medications(self):
        names = {text(r['id']): text(r['name']) for r in self.rows('medications')}
        catalogue = {m.name.casefold(): m for m in Medication.objects.all()}
        for row, patient in self.active_rows('medication_treatments'):
            if self.mapped('medication_treatments', row['id'], MedicationRecord):
                continue
            name = names.get(text(row.get('medication_name')), text(row.get('medication_name'))) or 'Unknown'
            date = self.row_date(row, 'date', 'visit_date', 'start_date') or patient.registration_date
            record = MedicationRecord.objects.create(
                patient=patient, encounter=self.encounter_for(row), medication=catalogue.get(name.casefold()),
                medication_name=name[:150], action=MED_ACTIONS.get(text(row.get('medication_action')), ''),
                dose=(text(row.get('dose_description')) or text(row.get('dose_duration')))[:150],
                start_date=parse_date(row.get('start_date')), end_date=parse_date(row.get('end_date')),
                record_date=date, notes='Imported from EDCS', created_by=self.staff(row.get('staff_id')))
            self.remember('medication_treatments', row['id'], record)
            self.counts['medication history records created'] += 1

    # ------------------------------------------------------------ old lab catalogue, lab requests
    def import_lab_catalogue(self):
        """EDCS test_list: link tests we already have; add the others inactive so the lab can switch them on."""
        categories = {text(r['id']): text(r['name']).title() for r in self.rows('sub_category')}
        legacy = LabCategory.objects.get_or_create(name='Legacy (EDCS, to review)', defaults={'order': 9})[0]
        self.legacy_tests = {}
        for row in self.rows('test_list'):
            name = re.sub(r'\s+', ' ', text(row['name'])).strip()
            key = re.sub(r'[^a-z0-9]', '', name.casefold())
            test = self.mapped('test_list', row['id'], LabTest)
            if test is None:
                code = next((c for k, c in EDCS_TEST_MATCH.items() if key.startswith(k)), None)
                test = self.lab_tests.get(code) if code else None
            if test is None:
                category = LabCategory.objects.get_or_create(
                    name=categories.get(text(row.get('sub_category')), legacy.name),
                    defaults={'order': 8})[0]
                test = LabTest.objects.create(code=f'edcs-{row["id"]}', name=name[:150], category=category,
                                              is_active=False, **legacy_test_values(row))
                self.lab_tests[test.code] = test
                self.counts['old EDCS lab tests added (inactive, to review)'] += 1
            self.remember('test_list', row['id'], test, notes=name)
            self.legacy_tests[text(row['id'])] = test

    def import_lab_requests(self):
        for row, patient in self.active_rows('lab_requests'):
            test = self.legacy_tests.get(text(row.get('test_name')))
            raw = text(row.get('test_value'))
            if test is None or not raw or raw.casefold() in MISSING_LAB_VALUES:
                continue
            date = self.row_date(row, 'lab_date', 'visit_date', 'created_on')
            if date is None:
                self.issue('lab_request_without_date', f'lab_requests {row["id"]}: {test.name} = {raw}')
                continue
            self.add_lab('lab_requests', row, patient, test.code, raw, date)

    # ------------------------------------------------------------ hospitalization episodes, siblings
    def import_admissions(self):
        """hospitalization_table: one row per admission episode -> Admission records (PP10 / SCD indicators)."""
        for row, patient in self.active_rows('hospitalization_table'):
            if self.mapped('hospitalization_table', row['id'], Admission):
                continue
            reason = re.sub(r'\s+', ' ', text(row.get('admission_reason'))).strip()
            diagnosis = re.sub(r'\s+', ' ', text(row.get('discharge_diagnosis'))).strip()
            admitted = parse_any_date(row.get('admission_date')) or parse_any_date(row.get('hospitalization_date'))
            discharged = parse_any_date(row.get('discharge_date'))
            if diagnosis and parse_any_date(diagnosis) and len(diagnosis) <= 12:
                # The discharge date was typed into the discharge diagnosis box.
                discharged, diagnosis = discharged or parse_any_date(diagnosis), ''
            if not (reason or diagnosis or admitted):
                continue  # empty episode row
            if admitted is None:
                raise_issue(f'edcs:admission-date:{row["id"]}', 'admission_without_valid_date', DataIssue.Category.CLINICAL,
                            'Hospitalization without a valid admission date',
                            f'EDCS hospitalization episode {row["id"]}: admission date "{text(row.get("admission_date"))}", '
                            f'reason "{reason}", discharge "{diagnosis}". Add it under Admissions with the correct date.',
                            patient=patient)
                self.counts['hospitalization episodes left for review (no valid date)'] += 1
                continue
            if discharged and discharged < admitted:
                self.issue('discharge_before_admission', f'hospitalization_table {row["id"]} ({patient.patient_number})')
                discharged = None
            admission = Admission.objects.create(
                patient=patient, facility=self.facility(row.get('site_id')), encounter=self.encounter_for(row),
                ward='Not recorded (EDCS)', admitted_at=at_nine(admitted), reason=(reason or 'Not recorded')[:255],
                discharged_at=at_nine(discharged) if discharged else None,
                discharge_summary=f'Discharge diagnosis: {diagnosis}' if diagnosis else '',
                created_by=self.staff(row.get('staff_id')))
            self.remember('hospitalization_table', row['id'], admission)
            self.counts['admissions created (hospitalization episodes)'] += 1

    def import_family_members(self):
        """sickle_cell_status_table: one row per sibling (age, sex, sickle cell status; 99 = unknown)."""
        for row, patient in self.active_rows('sickle_cell_status_table'):
            if self.mapped('sickle_cell_status_table', row['id'], FamilyMember):
                continue
            age = text(row.get('age'))
            member = FamilyMember.objects.create(
                patient=patient, relation=FamilyMember.Relation.SIBLING,
                sex={'1': 'male', '2': 'female'}.get(text(row.get('sex')), ''),
                age_years=int(age) if age.isdigit() and int(age) < 120 else None,
                scd_status={'1': 'affected', '2': 'not_affected'}.get(text(row.get('sickle_status')), 'unknown'),
                recorded_on=self.row_date(row, 'visit_date', 'entry_date') or patient.registration_date,
                notes=text(row.get('other'))[:255], created_by=self.staff(row.get('staff_id')))
            self.remember('sickle_cell_status_table', row['id'], member)
            self.counts['siblings recorded (sickle cell family status)'] += 1

    def note_old_stock(self):
        """EDCS batch / batch_records look like trial entries (e.g. 10,000-unit batches of "xxxxx"); stock is not
        migrated. Facilities enter an opening stock count instead (Receive stock)."""
        batches = list(self.rows('batch'))
        if batches:
            self.issue('old_stock_not_imported', f'{len(batches)} EDCS batches and {len(list(self.rows("batch_records")))} '
                                                 'stock records left in the EDCS; enter opening stock per facility.')

    # ------------------------------------------------------------ legacy forms (lossless)
    def legacy_version(self, table, title, fields):
        definition, created = FormDefinition.objects.get_or_create(code=f'edcs-{table.replace("_", "-")}', defaults={
            'name': f'EDCS – {title}', 'applicability': 'universal', 'is_active': False, 'is_required': False,
            'description': f'Read-only data migrated from EDCS table "{table}". Values are the original EDCS codes.'})
        version = definition.versions.filter(status='published').first()
        if version:
            return version
        version = FormVersion.objects.create(definition=definition, number=1, change_notes='Imported from EDCS')
        section = FormSection.objects.create(version=version, title=title)
        for order, (key, label, options) in enumerate(fields, start=1):
            FormField.objects.create(section=section, key=key[:60], label=label[:255], order=order,
                                     field_type='select' if options else 'text',
                                     options='\n'.join(f'{v}|{l}' for v, l in options) if options else '')
        return publish_version(version, None)

    def add_submission(self, table, row, patient, version, columns):
        if self.mapped(table, row['id'], FormSubmission):
            return
        data = {column: text(row.get(column)) for column in columns if text(row.get(column))}
        if not data:
            return
        submission = FormSubmission.objects.create(
            version=version, patient=patient, encounter=self.encounter_for(row), status='submitted', data=data,
            submitted_by=self.staff(row.get('staff_id')), submitted_at=at_nine(self.row_date(row, 'visit_date', 'created_on', 'clinic_date')
                                                                              or patient.registration_date),
            created_by=self.import_user)
        self.remember(table, row['id'], submission)
        self.counts['legacy form entries created'] += 1

    def import_legacy_forms(self):
        fields = [(c, label, options) for c, (label, options) in REGISTRATION_FIELDS.items()]
        version = self.legacy_version('clients', 'Registration (socio-demographic)', fields)
        for row in self.rows('clients'):
            patient = self.patient(row['id'])
            if patient:
                self.add_submission('clients', row, patient, version, list(REGISTRATION_FIELDS))
        for table, title in LEGACY_FORMS.items():
            columns = [c for c in self.reader.columns(table) if c not in META_COLUMNS]
            info = DICTIONARY.get(table, {})
            fields = [(c, info.get(c, {}).get('label') or c.replace('_', ' ').capitalize(), info.get(c, {}).get('options'))
                      for c in columns]
            version = self.legacy_version(table, title, fields)
            for row, patient in self.active_rows(table):
                self.add_submission(table, row, patient, version, columns)

    def continue_legacy_forms(self):
        """Make the EDCS forms live in this system and map EDCS screening to pre-screening."""
        from integrations.legacy_forms import run
        report = run(self.import_user)
        self.counts['EDCS forms made live'] = len(report['live_forms'])
        if report['prescreenings']:
            self.counts['pre-screenings created from EDCS screening'] += report['prescreenings']

    # ------------------------------------------------------------ final steps
    def apply_deaths(self):
        for patient_pk, (date, cause) in self.deaths.items():
            patient = Patient.all_objects.get(pk=patient_pk)
            if patient.vital_status == 'deceased':
                continue
            record_death(patient, date or self.today, cause[:255], self.import_user)
            self.counts['deaths recorded'] += 1

    def apply_soft_deletes(self):
        for row in self.rows('clients'):
            patient = self.patient(row['id'])
            if patient and text(row.get('status')) == '0' and not patient.is_deleted:
                patient.soft_delete(self.import_user, 'Deleted in EDCS before migration')
                self.counts['subjects soft-deleted (deleted in EDCS)'] += 1

    def find_duplicates(self):
        seen = defaultdict(list)
        for p in Patient.all_objects.filter(old_id__isnull=False):
            seen[(p.first_name.casefold(), p.last_name.casefold(), p.date_of_birth)].append(p)
        for group in seen.values():
            if len(group) > 1:
                self.issue('possible_duplicate_patients', ', '.join(f'{p.patient_number} (old {p.old_id})' for p in group))


    # ------------------------------------------------------------ outcome timeline, returns and missed months
    def outcome_timelines(self):
        """Per patient: [(date, outcome code)] from summary rows, oldest first."""
        timelines = defaultdict(list)
        for row, patient in self.active_rows('summary'):
            code = text(row.get('outcome'))
            date = self.row_date(row, 'summary_date', 'visit_date')
            if code in OUTCOMES and date:
                timelines[patient.pk].append((date, code, row))
        for items in timelines.values():
            items.sort(key=lambda item: (item[0], item[2]['id']))
        return timelines

    def care_segments(self, start, timeline, attended=(), ltfu_months=None):
        """Periods in care and the status changes between them.

        A period ends at an EDCS outcome (default, stopped/withdrawn, transfer out, death) or by the programme
        rule: no attended visit for `ltfu_months` months after the last attended visit (the patient becomes LTFU
        on that date). A later attended visit starts a new period: the patient returned to care. Summary rows
        are not attendance (many belong to missed visits). Returns ([(start, end, end_month_included)], events,
        final status).
        """
        reason = LTFU_REASON.format(months=ltfu_months)
        items = sorted([(d, 0, 'visit', None) for d in attended if d >= start]
                       + [(d, 1, 'outcome', code) for d, code, _row in timeline if d >= start])
        state = {'active': True, 'start': start, 'status': 'active', 'last': start}
        segments, events = [], []

        def lapse(until):
            if ltfu_months and state['active'] and until is not None:
                due = add_months(state['last'], ltfu_months)
                if until > due:
                    segments.append((state['start'], due, True))
                    events.append((due, 'active', 'ltfu', 'status', reason))
                    state.update(active=False, status='ltfu')

        for date, _order, kind, code in items:
            lapse(date)
            if kind == 'visit':
                if not state['active'] and state['status'] != 'deceased':
                    events.append((date, state['status'], 'active', 'return', 'Returned to care (attended visit)'))
                    state.update(active=True, start=date, status='active')
                if state['active']:
                    state['last'] = date
            elif code in TERMINAL_OUTCOMES:
                new_status = OUTCOMES[code][0]
                if state['active']:
                    segments.append((state['start'], date, False))
                    events.append((date, 'active', new_status, 'status', OUTCOMES[code][1]))
                    state.update(active=False, status=new_status)
                elif state['status'] not in ('deceased', new_status) and code != '2':
                    # A recorded outcome after the patient stopped coming (e.g. found by tracing) replaces the
                    # automatic LTFU: transfer out, stopped treatment or death, on the date it was recorded.
                    events.append((date, state['status'], new_status, 'status', OUTCOMES[code][1]))
                    state['status'] = new_status
        lapse(self.today)
        if state['active']:
            segments.append((state['start'], None, False))
        return segments, events, state['status']

    def attended_dates(self, patient_ids):
        dates = defaultdict(list)
        for pid, when in (Encounter.objects.filter(patient_id__in=patient_ids).exclude(status='cancelled')
                          .values_list('patient_id', 'encounter_date')):
            dates[pid].append(timezone.localtime(when).date())
        return dates

    def mark_edcs_origin(self):
        ids = ExternalRecord.objects.filter(source=SOURCE, table='visit',
                                            content_type=ContentType.objects.get_for_model(Appointment))
        updated = Appointment.objects.filter(pk__in=[int(i) for i in ids.values_list('object_id', flat=True)],
                                             origin='manual').update(origin='edcs')
        if updated:
            self.counts['appointments marked as migrated from EDCS'] += updated

    def imported_enrollments(self):
        ct = ContentType.objects.get_for_model(ProgrammeEnrollment)
        ids = ExternalRecord.objects.filter(source=SOURCE, table__startswith='clients.programme.',
                                            content_type=ct).values_list('object_id', flat=True)
        return ProgrammeEnrollment.objects.filter(pk__in=[int(i) for i in ids]).select_related('patient')

    def apply_outcome_timeline(self):
        timelines = self.outcome_timelines()
        enrollments = list(self.imported_enrollments())
        attended = self.attended_dates({e.patient_id for e in enrollments})
        for enrollment in enrollments:
            if enrollment.events.exclude(created_by=self.import_user).exists():
                continue  # staff have changed this enrollment in the new system: leave it alone
            enrollment.events.filter(created_by=self.import_user).delete()  # importer's own history, recomputed
            timeline = timelines.get(enrollment.patient_id, [])
            _segments, events, final = self.care_segments(enrollment.enrollment_date, timeline,
                                                          attended.get(enrollment.patient_id, []),
                                                          settings.LTFU_MONTHS)
            for date, from_status, to_status, kind, reason in events:
                EnrollmentEvent.objects.create(enrollment=enrollment, kind=kind, event_date=date,
                                               from_status=from_status, to_status=to_status, reason=reason,
                                               created_by=self.import_user)
                self.counts['enrollment history events'] += 1
                if kind == 'return':
                    self.counts['returns to care found'] += 1
                if to_status == 'ltfu' and reason.startswith('Default / lost'):
                    self.counts['default/LTFU by the 3-month rule'] += 1
            closing = [e for e in events if e[2] == final] if final != 'active' else []
            enrollment.status = final
            enrollment.outcome_date = closing[-1][0] if closing else None
            enrollment.outcome_reason = closing[-1][4] if closing else ''
            enrollment.save(update_fields=['status', 'outcome_date', 'outcome_reason', 'updated_at'])
            # Research follows care: the same rule and outcome, on the same date. A study already completed in the
            # EDCS (end of study while still in care) stays completed.
            study = StudyEnrollment.objects.filter(patient=enrollment.patient, study=self.study).first()
            if study:
                target = STUDY_STATUS.get(final, final)
                if final == 'active' and study.status == 'completed':
                    continue
                study.status = target
                study.outcome_date = enrollment.outcome_date
                study.outcome_reason = (f'Follows clinical care: {enrollment.outcome_reason}'
                                        if enrollment.outcome_reason else '')[:255]
                study.save(update_fields=['status', 'outcome_date', 'outcome_reason', 'updated_at'])

    def fill_missed_months(self):
        """Monthly follow-up: a month in care with no EDCS visit is recorded as a missed visit.

        In care means between enrollment (or a return) and the end of that period; a period ended by the
        3-month rule includes the month of the third missed visit.
        """
        timelines = self.outcome_timelines()
        starts = {}
        for enrollment in self.imported_enrollments():
            starts[enrollment.patient_id] = min(starts.get(enrollment.patient_id, enrollment.enrollment_date),
                                                enrollment.enrollment_date)
        attended = self.attended_dates(starts)
        gap_ct = ContentType.objects.get_for_model(Appointment)
        existing_gaps = defaultdict(dict)
        for appointment in Appointment.objects.filter(patient_id__in=starts, origin=Appointment.Origin.EDCS_GAP):
            existing_gaps[appointment.patient_id][(appointment.scheduled_date.year, appointment.scheduled_date.month)] = appointment
        covered = defaultdict(set)
        for pid, date in (Appointment.objects.filter(patient_id__in=starts).exclude(origin=Appointment.Origin.EDCS_GAP)
                          .values_list('patient_id', 'scheduled_date')):
            covered[pid].add((date.year, date.month))
        for pid, dates in attended.items():
            covered[pid].update((d.year, d.month) for d in dates)
        this_month = (self.today.year, self.today.month)
        for patient_id, start in starts.items():
            patient = Patient.all_objects.get(pk=patient_id)
            segments, _events, _final = self.care_segments(start, timelines.get(patient_id, []),
                                                           attended.get(patient_id, []), settings.LTFU_MONTHS)
            wanted = set()
            for seg_start, seg_end, include_end in segments:
                end_month = (seg_end.year, seg_end.month) if seg_end else this_month
                month = add_months(seg_start.replace(day=1), 1)
                while (month.year, month.month) < end_month or (include_end and (month.year, month.month) == end_month):
                    if (month.year, month.month) < this_month and (month.year, month.month) not in covered[patient_id]:
                        wanted.add((month.year, month.month))
                    month = add_months(month, 1)
            for key in sorted(wanted - set(existing_gaps[patient_id])):
                year, month = key
                day = min(start.day, calendar.monthrange(year, month)[1])
                appointment = Appointment.objects.create(
                    patient=patient, facility=patient.current_facility, scheduled_date=datetime.date(year, month, day),
                    stage='follow_up', status='missed', origin=Appointment.Origin.EDCS_GAP, created_by=self.import_user,
                    notes='No visit recorded in the EDCS for this month')
                self.remember('gap', f'{patient.old_id}:{year}-{month:02d}', appointment)
                self.counts['missed visits added for months without an EDCS visit'] += 1
            for key, appointment in existing_gaps[patient_id].items():
                untouched = appointment.status == 'missed' and not appointment.encounters.exists()
                if key not in wanted and untouched:
                    ExternalRecord.objects.filter(source=SOURCE, table='gap', content_type=gap_ct,
                                                  object_id=str(appointment.pk)).delete()
                    appointment.delete()
                    self.counts['missed-visit entries removed (subject was already LTFU / out of care)'] += 1

    # ------------------------------------------------------------ data issues for staff to resolve
    def raise_data_issues(self):
        created = Counter()
        id_use = Counter(text(r['hospital_id']).casefold() for r in self.rows('clients') if text(r['hospital_id']))
        placeholders = {v for v, n in id_use.items() if n >= PLACEHOLDER_ID_THRESHOLD} | {'0', '00', '000'}
        for row in self.rows('clients'):
            patient = self.patient(row['id'])
            if patient is None:
                continue
            value = text(row['hospital_id'])
            if value and value.casefold() in placeholders:
                _, new = raise_issue(f'edcs:placeholder-id:{row["id"]}', 'placeholder_hospital_id',
                                     DataIssue.Category.IDENTITY, f'Hospital ID "{value}" looks like a placeholder',
                                     f'The EDCS hospital ID "{value}" is used by {id_use[value.casefold()]} subjects, '
                                     f'so it was not imported. Add the real hospital/MRN number.', patient=patient)
                created['placeholder_hospital_id'] += new
            enrolled_condition = any(text(row.get(c)) == '1' for c in ('cardiac', 'diabetes', 'sickle_cell')) or \
                CATEGORY_DISEASE.get(text(row.get('dignosis_type')))
            if text(row.get('enrolled')) == '1' and not enrolled_condition:
                _, new = raise_issue(f'edcs:no-condition:{row["id"]}', 'enrolled_without_condition',
                                     DataIssue.Category.CLINICAL, 'Enrolled without a condition',
                                     'Enrolled in the EDCS but no cardiac/diabetes/sickle cell category was recorded, '
                                     'so no programme enrollment was created. Add the diagnosis and programme.',
                                     patient=patient)
                created['enrolled_without_condition'] += new
        for row, patient in self.active_rows('vital'):
            flagged = [f'{c}={row[c]}' for c in ('pr', 'weight', 'height', 'systolic', 'dystolic')
                       if text(row.get(c)) in ('98', '99')]
            date = self.row_date(row, 'visit_date')
            if self.encounter_for(row) is None:
                values = ', '.join(f'{c}={text(row.get(c))}' for c in ('systolic', 'dystolic', 'pr', 'weight', 'height')
                                   if text(row.get(c)))
                _, new = raise_issue(f'edcs:vital-novisit:{row["id"]}', 'vitals_without_visit', DataIssue.Category.VITALS,
                                     'Vital signs not linked to a visit',
                                     f'EDCS vital signs (row {row["id"]}, {date or "no date"}) belong to a visit that is '
                                     f'missing or deleted, so they were not imported: {values or "no values"}.',
                                     patient=patient, related_date=date)
                created['vitals_without_visit'] += new
            elif flagged:
                vitals = self.mapped('vital', row['id'], Vitals)
                _, new = raise_issue(f'edcs:vital-98-99:{row["id"]}', 'vitals_98_99', DataIssue.Category.VITALS,
                                     'Vital sign recorded as 98 or 99',
                                     f'{", ".join(flagged)}. In the EDCS 98/99 often mean "not done". Confirm the value '
                                     f'or clear it.', patient=patient, related_date=date,
                                     obj=vitals.encounter if vitals else None)
                created['vitals_98_99'] += new
        for row in self.rows('visit'):
            patient = self.patient(row['client_id'])
            if text(row.get('status')) != '1':
                continue
            if patient is None:
                _, new = raise_issue(f'edcs:orphan-visit:{row["id"]}', 'visit_without_patient', DataIssue.Category.MIGRATION,
                                     f'EDCS visit {row["id"]} has no subject',
                                     f'Visit {row["visit_code"]} #{row["seq_no"]} refers to client {row["client_id"]}, '
                                     f'which does not exist in the EDCS. Not imported.',
                                     facility=self.facility(row.get('site_id')))
                created['visit_without_patient'] += new
            elif parse_date(row['visit_date']) is None and parse_date(row['expected_date']) is None:
                _, new = raise_issue(f'edcs:visit-date:{row["id"]}', 'visit_without_valid_date', DataIssue.Category.VISITS,
                                     'EDCS visit with an invalid date',
                                     f'Visit {row["visit_code"]} #{row["seq_no"]}: expected "{text(row["expected_date"])}", '
                                     f'actual "{text(row["visit_date"])}". Not imported; add it manually if it happened.',
                                     patient=patient)
                created['visit_without_valid_date'] += new
        for row in self.rows('screening'):
            if text(row.get('status')) == '1' and self.patient(row.get('patient_id')) is None:
                _, new = raise_issue(f'edcs:orphan-screening:{row["id"]}', 'screening_without_patient',
                                     DataIssue.Category.MIGRATION, f'EDCS screening {row["id"]} has no subject',
                                     f'Screening dated {text(row.get("screening_date"))} refers to client '
                                     f'{row.get("patient_id")}, which does not exist. Not imported.',
                                     facility=self.facility(row.get('site_id')))
                created['screening_without_patient'] += new
        for enrollment in StudyEnrollment.objects.filter(participant_id__contains='-dup').select_related('patient'):
            _, new = raise_issue(f'edcs:dup-study-id:{enrollment.pk}', 'duplicate_study_id', DataIssue.Category.CONSENT,
                                 f'Study ID {enrollment.participant_id.split("-dup")[0]} used twice in the EDCS',
                                 f'Stored as {enrollment.participant_id}. Assign the correct participant ID.',
                                 patient=enrollment.patient, obj=enrollment.patient)
            created['duplicate_study_id'] += new
        for name, count in {**created, **run_all()}.items():
            if count:
                self.counts[f'data issues raised: {name}'] += count


    # ------------------------------------------------------------ echo pathology flag (PEN-Plus PP14)
    ECHO_PATHOLOGY = ('LV severely depressed', 'Mitral stenosis', 'RV severely dilated', 'Pericardial effusion',
                      'IVC dilated', 'Thrombus', 'Congenital defect')

    def flag_echo_findings(self):
        """Abnormal if 'Echo (Normal): No', any pathology 'Yes' or another finding described; normal if 'Echo (Normal):
        Yes' with no pathology; unknown when no findings were recorded."""
        for echo in InvestigationOrder.objects.filter(modality='echo', is_abnormal__isnull=True,
                                                      description__icontains='imported from EDCS'):
            lines = echo.findings.splitlines()
            pathology = any(line.startswith(label) and line.endswith(': Yes') for line in lines for label in self.ECHO_PATHOLOGY)
            described = any(line.startswith('Other Echo specify:') or line.startswith('Other:') and
                            not line.endswith((': 1', ': 2', ': 3')) for line in lines)
            if 'Echo:(Normal): No' in lines or pathology or described:
                flag = True
            elif 'Echo:(Normal): Yes' in lines:
                flag = False
            else:
                continue
            InvestigationOrder.objects.filter(pk=echo.pk).update(is_abnormal=flag)
            self.counts['echos flagged normal / abnormal'] += 1

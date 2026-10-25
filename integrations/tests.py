import datetime

from django.utils import timezone

from django.core.management import call_command
from django.test import TestCase

from clinical.models import Diagnosis, ProgrammeEnrollment, Screening
from crf.models import FormSubmission
from encounters.models import Appointment, Encounter, Vitals
from investigations.models import InvestigationOrder
from laboratory.models import LabOrder
from patients.models import Patient
from pharmacy.models import MedicationRecord
from research.models import StudyEnrollment

from .edcs import Importer
from .models import ExternalRecord


FUTURE = timezone.localdate() + datetime.timedelta(days=60)


def visit(id, client, code, status, expected, actual='', seq=1):
    return {'id': id, 'client_id': client, 'visit_code': code, 'visit_name': code, 'visit_status': status,
            'expected_date': expected, 'visit_date': actual, 'status': 1, 'site_id': 1, 'seq_no': seq,
            'reasons': '', 'comments': '', 'next_notes': ''}


def client(id, study_id, hospital_id, status=1, **extra):
    row = {'id': id, 'study_id': study_id, 'hospital_id': hospital_id, 'clinic_date': '2023-05-02',
           'firstname': f'First{id}', 'middlename': '', 'lastname': f'Last{id}', 'dob': '2010-01-15', 'age': '13',
           'age2': None, 'gender': '1', 'phone_number': '0712000000', 'phone_number2': '', 'guardian_name': 'Mama',
           'middlename_gurdian': '', 'lastname_gurdian': 'Mlezi', 'guardian_phone': '0713000000',
           'physical_address': 'Kondoa mjini', 'region': 3, 'district': 16, 'ward': 134, 'site_id': 1,
           'staff_id': 7, 'status': status, 'enrolled': 1, 'eligible': 1, 'screened': 1, 'end_study': 0,
           'cardiac': 1, 'diabetes': 2, 'sickle_cell': 2, 'dignosis_type': '1', 'employment_status': '5',
           'education_level': '2', 'created_on': '2023-05-02'}
    row.update(extra)
    return row


class FakeReader:
    def __init__(self, tables):
        self.tables = tables

    def rows(self, table):
        return self.tables.get(table, [])

    def columns(self, table):
        rows = self.tables.get(table)
        return list(rows[0]) if rows else ['id', 'visit_date', 'patient_id', 'status']


def edcs_data():
    form = {'study_id': '1-001', 'visit_code': 'EV', 'visit_day': 'Day 0', 'seq_no': 1, 'site_id': 1, 'staff_id': 7,
            'status': 1, 'created_on': '2023-06-01'}
    return {
        'position': [{'id': 6, 'name': 'Site Doctors'}, {'id': 3, 'name': 'Project Principle Investigator'}],
        'user': [{'id': 7, 'username': 'drjuma', 'firstname': 'Juma', 'lastname': 'Daktari', 'position': 6,
                  'phone_number': '', 'email_address': 'juma@example.org', 'status': 1, 'site_id': 1},
                 {'id': 8, 'username': 'pi', 'firstname': 'Mary', 'lastname': 'PI', 'position': 3,
                  'phone_number': '', 'email_address': 'pi@example.org', 'status': 1, 'site_id': 1}],
        'wards': [{'id': 134, 'name': 'Bereke', 'district_id': 16}],
        'districts': [{'id': 16, 'name': 'Kondoa', 'region_id': 3}],
        'clients': [client(1, '1-001', 'KDH-55'), client(2, '1-002', 'Kth001'), client(3, '1-003', 'Kth001'),
                    client(4, '1-004', 'Kth001'), client(5, '1-005', 'Kth001'), client(6, '1-006', 'Kth001', status=0),
                    client(7, '1-007', 'KDH-77', clinic_date='2023-01-10', enrolled=1, cardiac=2, diabetes=1)],
        'visit': [visit(10, 1, 'EV', 1, '2023-06-01', '2023-06-01'), visit(11, 1, 'FV', 2, '2023-07-01', seq=2),
                  visit(12, 1, 'FV', 1, '2023-08-01', '22023-08-02', seq=3), visit(13, 1, 'FV', 0, FUTURE.isoformat(), seq=4),
                  visit(14, 99, 'FV', 1, '2023-08-01', '2023-08-01'),
                  visit(20, 7, 'EV', 1, '2023-01-10', '2023-01-10'), visit(21, 7, 'FV', 1, '2023-03-05', '2023-03-05', seq=2),
                  visit(22, 7, 'FV', 1, '2023-07-08', '2023-07-08', seq=3), visit(23, 7, 'UV', 1, '2023-07-20', '2023-07-20', seq=4)],
        'screening': [{'id': 1, 'patient_id': 1, 'screening_date': '2023-05-02', 'conset_date': '2023-05-02',
                       'consent': '1', 'rhd': '1', 'dm': '2', 'scd': '', 'site_id': 1, 'staff_id': 7, 'status': 1,
                       'remarks': '', 'vid': None}],
        'cardiac': [{**form, 'id': 1, 'patient_id': 1, 'vid': 10, 'visit_date': '2023-06-01', 'diagnosis_date': '2023-06-01',
                     'heumatic': '1', 'sub_heumatic': '3', 'heart_failure': '2', 'comments': ''}],
        'vital': [{**form, 'id': 1, 'patient_id': 1, 'vid': 10, 'visit_date': '2023-06-01', 'height': '140',
                   'weight': '35.5', 'muac': '19', 'systolic': '110', 'dystolic': '70', 'pr': '88', 'bmi': ''}],
        'lab_details': [{**form, 'id': 1, 'patient_id': 1, 'vid': 10, 'visit_date': '2023-06-01', 'lab_date': '2023-06-01',
                         'hb': '8.3', 'na': '99', 'cre': '0.9', 'lfts': 'Urea 39.2'}],
        'results': [{**form, 'id': 1, 'patient_id': 1, 'vid': 10, 'visit_date': '2023-06-01', 'ecg_performed': '2',
                     'echo_performed': '1', 'echo_date': '2023-06-01', 'mitral': '1', 'lv': '2', 'scd_test': ''}],
        'summary': [{**form, 'id': 1, 'patient_id': 1, 'vid': 12, 'visit_date': '2023-08-01', 'summary_date': '2023-08-01',
                     'outcome': '5', 'death_date': '2023-08-20', 'cause_death': '1', 'death_other': ''},
                    {**form, 'id': 2, 'patient_id': 7, 'vid': 20, 'visit_date': '2023-01-10', 'summary_date': '2023-01-10', 'outcome': '1'},
                    {**form, 'id': 3, 'patient_id': 7, 'vid': 21, 'visit_date': '2023-03-05', 'summary_date': '2023-03-05', 'outcome': '2'},
                    {**form, 'id': 4, 'patient_id': 7, 'vid': 22, 'visit_date': '2023-07-08', 'summary_date': '2023-07-08', 'outcome': '1'}],
        'medications': [{'id': 6, 'name': 'Benzathine PCN'}],
        'medication_treatments': [{**form, 'id': 1, 'patient_id': 1, 'vid': 10, 'visit_date': '2023-06-01',
                                   'medication_name': '6', 'medication_action': '2', 'dose_description': '1.2 MU monthly',
                                   'start_date': '2023-06-01', 'end_date': '', 'date': '2023-06-01'}],
        'history': [{**form, 'id': 1, 'patient_id': 1, 'vid': 10, 'visit_date': '2023-06-01', 'hiv': '2', 'smoking': '2'}],
    }


class EDCSImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_penplus', stdout=open('/dev/null', 'w'))
        cls.counts, cls.issues = Importer(FakeReader(edcs_data()), log=lambda *a: None).run()

    def test_patients_get_new_ids_and_keep_old_id(self):
        p = Patient.all_objects.get(old_id=1)
        self.assertTrue(p.patient_number.startswith('NP23-'))
        self.assertEqual(p.current_facility.code, 'KONDOA-DH')
        self.assertEqual(p.residence_ward.name, 'Bereke')
        self.assertEqual(set(p.identifiers.values_list('id_type', 'value')), {('hospital', 'KDH-55'), ('edcs_study', '1-001')})
        self.assertEqual(p.next_of_kin_name, 'Mama Mlezi')

    def test_placeholder_hospital_ids_not_imported(self):
        self.assertFalse(Patient.all_objects.get(old_id=2).identifiers.filter(id_type='hospital').exists())
        self.assertEqual(len(self.issues['placeholder_hospital_id_not_imported']), 5)

    def test_deleted_client_is_soft_deleted(self):
        self.assertFalse(Patient.objects.filter(old_id=6).exists())
        self.assertTrue(Patient.all_objects.get(old_id=6).is_deleted)

    def test_visits_become_appointments_and_encounters(self):
        p = Patient.all_objects.get(old_id=1)
        statuses = dict(p.appointments.values_list('scheduled_date', 'status'))
        self.assertEqual(statuses[datetime.date(2023, 6, 1)], 'attended')
        self.assertEqual(statuses[datetime.date(2023, 7, 1)], 'missed')
        self.assertEqual(statuses[FUTURE], 'cancelled')  # future visit cancelled by death
        self.assertEqual(Encounter.objects.filter(patient=p).count(), 2)
        self.assertIn('visit 14 client 99', self.issues['visit_without_patient'][0])

    def test_clinical_records(self):
        p = Patient.all_objects.get(old_id=1)
        self.assertEqual(Screening.objects.get(patient=p, disease__code='cardiac').result, 'positive')
        self.assertEqual(Screening.objects.get(patient=p, disease__code='diabetes').result, 'negative')
        self.assertIn('Rheumatic heart disease – Mixed mitral', Diagnosis.objects.get(patient=p).description)
        vitals = Vitals.objects.get(encounter__patient=p)
        self.assertEqual((vitals.systolic_bp, vitals.muac_cm), (110, 19))

    def test_labs_skip_missing_codes_and_keep_units(self):
        p = Patient.all_objects.get(old_id=1)
        results = {o.test.code: o for o in LabOrder.objects.filter(patient=p)}
        self.assertNotIn('na', results)
        self.assertEqual((results['hb'].result_value, results['hb'].result_unit, results['hb'].is_abnormal),
                         ('8.3', 'g/dL', True))
        self.assertEqual(results['creat'].result_unit, 'mg/dL')
        self.assertEqual(results['lft-legacy'].result_value, 'Urea 39.2')
        echo = InvestigationOrder.objects.get(patient=p)
        self.assertIn('Mitral stenosis: Yes', echo.findings)

    def test_outcome_death_and_enrollments(self):
        p = Patient.all_objects.get(old_id=1)
        self.assertEqual((p.vital_status, p.date_of_death), ('deceased', datetime.date(2023, 8, 20)))
        self.assertEqual(ProgrammeEnrollment.objects.get(patient=p).status, 'deceased')
        study = StudyEnrollment.objects.get(patient=p)
        self.assertEqual((study.participant_id, study.status), ('1-001', 'deceased'))
        self.assertEqual(study.consent.decision, 'consented')

    def test_medication_history_and_legacy_forms(self):
        p = Patient.all_objects.get(old_id=1)
        record = MedicationRecord.objects.get(patient=p)
        self.assertEqual((record.medication_name, record.action, record.dose), ('Benzathine PCN', 'start', '1.2 MU monthly'))
        history = FormSubmission.objects.get(patient=p, version__definition__code='edcs-history')
        self.assertEqual(history.data['hiv'], '2')
        self.assertFalse(history.version.definition.is_active)
        registration = FormSubmission.objects.get(patient=p, version__definition__code='edcs-clients')
        self.assertNotIn('guardian_name', registration.data)

    def test_staff_recreated_without_passwords(self):
        from django.contrib.auth import get_user_model
        doctor = get_user_model().objects.get(username='drjuma')
        self.assertFalse(doctor.has_usable_password())
        self.assertTrue(doctor.groups.filter(name='Clinician').exists())
        pi = get_user_model().objects.get(username='pi')
        self.assertEqual(pi.assignments.count(), 2)
        self.assertFalse(pi.has_perm('patients.view_patient_identity'))

    def test_rerun_is_idempotent(self):
        before = ExternalRecord.objects.count(), Patient.all_objects.count(), LabOrder.objects.count()
        counts, _ = Importer(FakeReader(edcs_data()), log=lambda *a: None).run()
        after = ExternalRecord.objects.count(), Patient.all_objects.count(), LabOrder.objects.count()
        self.assertEqual(before, after)
        self.assertFalse({k: v for k, v in counts.items() if 'created' in k})


class MonthlyScheduleAndIssuesTests(TestCase):
    """Patient 7: enrolled Jan 2023, defaulted Mar 2023, returned Jul 2023, two visits in Jul 2023, none since:
    LTFU on 20 Oct 2023 by the 3-month rule."""

    @classmethod
    def setUpTestData(cls):
        call_command('setup_penplus', stdout=open('/dev/null', 'w'))
        Importer(FakeReader(edcs_data()), log=lambda *a: None).run()
        cls.patient = Patient.all_objects.get(old_id=7)

    def months(self, **filters):
        return {(a.scheduled_date.year, a.scheduled_date.month)
                for a in Appointment.objects.filter(patient=self.patient, **filters)}

    def test_missing_months_in_care_become_missed_visits(self):
        gaps = self.months(origin='edcs_gap')
        self.assertIn((2023, 2), gaps)            # between enrollment and default
        self.assertTrue({(2023, 4), (2023, 5), (2023, 6)}.isdisjoint(gaps))  # defaulted: not expected to attend
        self.assertTrue({(2023, 8), (2023, 9), (2023, 10)} <= gaps)   # 3 missed visits after the last attended
        self.assertFalse([g for g in gaps if g > (2023, 10)])           # then LTFU: no further expected visits
        today = timezone.localdate()
        self.assertNotIn((today.year, today.month), gaps)
        self.assertTrue(all(a.status == 'missed' for a in Appointment.objects.filter(origin='edcs_gap')))

    def test_no_gaps_after_death(self):
        died = Patient.all_objects.get(old_id=1)
        self.assertFalse(Appointment.objects.filter(patient=died, origin='edcs_gap',
                                                    scheduled_date__gt=datetime.date(2023, 8, 20)).exists())

    def test_default_then_return_history(self):
        from clinical.models import EnrollmentEvent
        enrollment = ProgrammeEnrollment.objects.get(patient=self.patient)
        events = list(EnrollmentEvent.objects.filter(enrollment=enrollment).order_by('event_date')
                      .values_list('from_status', 'to_status', 'kind'))
        self.assertEqual(events, [('active', 'ltfu', 'status'), ('ltfu', 'active', 'return'),
                                  ('active', 'ltfu', 'status')])
        self.assertEqual((enrollment.status, enrollment.outcome_date), ('ltfu', datetime.date(2023, 10, 20)))
        self.assertIn('3 months', enrollment.outcome_reason)
        # research follows care: same status and date
        study = StudyEnrollment.objects.get(patient=self.patient)
        self.assertEqual((study.status, study.outcome_date), ('ltfu', datetime.date(2023, 10, 20)))

    def test_recorded_outcome_after_ltfu_replaces_it(self):
        importer = Importer(FakeReader({}), log=lambda *a: None)
        start = datetime.date(2024, 1, 5)
        timeline = [(datetime.date(2024, 9, 1), '4', {})]   # transfer out found by tracing, 8 months later
        _segments, events, final = importer.care_segments(start, timeline, [start], 3)
        self.assertEqual(final, 'transferred_out')
        self.assertEqual([(e[1], e[2]) for e in events], [('active', 'ltfu'), ('ltfu', 'transferred_out')])
        timeline = [(datetime.date(2024, 9, 1), '2', {})]   # recorded default after rule LTFU: stays LTFU
        self.assertEqual(importer.care_segments(start, timeline, [start], 3)[2], 'ltfu')

    def test_two_visits_in_a_month_raise_an_issue(self):
        from issues.models import DataIssue
        issue = DataIssue.objects.get(code='visits_same_month', patient=self.patient)
        self.assertEqual(issue.related_date, datetime.date(2023, 7, 1))
        self.assertTrue(DataIssue.objects.filter(code='placeholder_hospital_id').count() >= 4)

    def test_issue_scan_is_idempotent(self):
        from issues.models import DataIssue
        before = DataIssue.objects.count(), Appointment.objects.count()
        Importer(FakeReader(edcs_data()), log=lambda *a: None).run()
        self.assertEqual(before, (DataIssue.objects.count(), Appointment.objects.count()))


class LegacyHelperTests(TestCase):
    def test_day_first_dates_and_old_test_rows(self):
        import datetime

        from .edcs import legacy_test_values, parse_any_date
        self.assertEqual(parse_any_date('12-05-2022'), datetime.date(2022, 5, 12))
        self.assertEqual(parse_any_date('2023-11-14'), datetime.date(2023, 11, 14))
        self.assertIsNone(parse_any_date('11111-11-11'))
        self.assertEqual(legacy_test_values({'units': 'Positive or Negative'})['choices'], 'Negative\n!Positive')
        self.assertEqual(legacy_test_values({'units': 'ng/ml', 'minimum': '0.5', 'maximum': '2.0'})['result_type'], 'numeric')
        self.assertEqual(legacy_test_values({'units': '-', 'minimum': '0', 'maximum': '0'}), {'result_type': 'text'})

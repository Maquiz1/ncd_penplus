import datetime

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from clinical.models import ClinicalProgramme, Diagnosis, Disease, EnrollmentEvent, ProgrammeEnrollment
from core.tests import BaseTestCase
from laboratory.models import LabOrder, LabTest
from pharmacy.models import MedicationRecord

from . import clinical, core
from .engine import Cohort

Y = timezone.localdate().year - 1   # a full past year


def d(month, day=15, year=Y):
    return datetime.date(year, month, day)


class IndicatorTests(BaseTestCase):
    def setUp(self):
        call_command('setup_diseases', stdout=open('/dev/null', 'w'))
        self.dm = ClinicalProgramme.objects.get(code='dm-care')
        self.cardiac = ClinicalProgramme.objects.get(code='cardiac-care')
        self.scd = ClinicalProgramme.objects.get(code='scd-care')
        # A: T1D girl, enrolled Q1, LTFU in Q3
        self.a = self.make_patient(first_name='A', sex='female', date_of_birth=datetime.date(Y - 12, 1, 1))
        e = self.enroll(self.a, self.dm, d(2), 'Type 1 diabetes mellitus')
        EnrollmentEvent.objects.create(enrollment=e, from_status='active', to_status='ltfu', event_date=d(8))
        e.status, e.outcome_date = 'ltfu', d(8)
        e.save()
        # B: RHD with right heart failure (both counted, as recorded), enrolled Q2
        self.b = self.make_patient(first_name='B', sex='male', date_of_birth=datetime.date(Y - 40, 1, 1))
        self.enroll(self.b, self.cardiac, d(5), 'Rheumatic heart disease – Mixed mitral')
        Diagnosis.objects.create(patient=self.b, disease=Disease.objects.get(code='cardiac'), description='Heart failure',
                                 status='confirmed', diagnosis_date=d(5), clinician=self.admin)
        Diagnosis.objects.filter(patient=self.b, description__startswith='Rheumatic').update(
            condition=__import__('diseases.models', fromlist=['Condition']).Condition.objects.get(code='rhd-other'))
        # C: SCD child, enrolled Q1, died Q4
        self.c = self.make_patient(first_name='C', sex='female', date_of_birth=datetime.date(Y - 3, 1, 1))
        self.enroll(self.c, self.scd, d(1), 'Sickle cell disease')
        MedicationRecord.objects.create(patient=self.c, medication_name='Hydroxyurea', action='continue', record_date=d(9))
        MedicationRecord.objects.create(patient=self.c, medication_name='Pen V', action='continue', record_date=d(9))
        self.c.vital_status, self.c.date_of_death = 'deceased', d(11)
        self.c.save()
        hba1c = LabTest.objects.get_or_create(code='hba1c', defaults={'name': 'HbA1c', 'result_type': 'numeric', 'unit': '%'})[0]
        order = LabOrder(patient=self.a, facility=self.ilala, test=hba1c, ordered_by=self.admin)
        order.record_result('7.2', self.admin, when=timezone.make_aware(datetime.datetime(Y, 6, 1, 9)))
        order.save()
        self.cohort = Cohort(self.admin)

    def enroll(self, patient, programme, date, description):
        Diagnosis.objects.create(patient=patient, disease=programme.disease, description=description, status='confirmed',
                                 diagnosis_date=date, clinician=self.admin)
        return ProgrammeEnrollment.objects.create(patient=patient, programme=programme, facility=self.ilala,
                                                  enrollment_date=date)

    def row(self, quarter, code):
        return next(r for r in core.quarter_rows(self.cohort, Y, quarter) if r['code'] == code)

    def n(self, quarter, code):
        value = self.row(quarter, code)['value']
        return len(value) if isinstance(value, set) else value

    def test_status_on_past_dates(self):
        self.assertEqual(self.n(1, 'PP1'), 2)        # A and C
        self.assertEqual(self.n(2, 'PP1'), 3)        # + B
        self.assertEqual(self.n(3, 'PP1'), 2)        # A became LTFU in Q3
        self.assertEqual(self.n(4, 'PP1'), 1)        # C died in Q4
        self.assertEqual(self.n(4, 'PP0'), 3)

    def test_flows_and_conditions(self):
        self.assertEqual(self.n(1, 'PP5'), 2)
        self.assertEqual(self.n(2, 'PP5.rhd'), 1)
        self.assertEqual(self.n(2, 'PP1.hf'), 1)     # several cardiac conditions per patient are all counted
        self.assertEqual(self.n(2, 'PP1.rhd'), 1)    # a sub-type counts under its main condition
        self.assertEqual(self.n(3, 'PP6'), 1)
        self.assertEqual(self.n(3, 'PP6.t1d'), 1)
        self.assertEqual(self.n(4, 'PP8'), 1)
        self.assertEqual(self.n(4, 'PP8.sca'), 1)
        self.assertEqual(self.n(2, 'PP2'), 2)        # A (12) and C (3) under 18
        self.assertEqual(self.n(2, 'PP3'), 2)

    def test_year_table_ytd_and_percentages(self):
        table = {row['code']: (cells, ytd) for row, cells, ytd in core.year_table(self.cohort, Y)}
        self.assertEqual(table['PP5'][1]['n'], 3)    # flow: sum of quarters
        self.assertEqual(table['PP1'][1]['n'], 1)    # stock: last quarter
        self.assertEqual(table['C1'][0][1]['pct'], 100.0)

    def test_clinical_indicators_and_inactive_hidden(self):
        date = d(9, 30)
        by_key = {i.key: i.evaluate(self.cohort, date) for i in clinical.INDICATORS}
        self.assertEqual(len(by_key['dm-a1c-6m']['numerator']), 0)     # A is LTFU on that date
        self.assertEqual(len(by_key['scd-hydroxyurea']['numerator']), 1)
        self.assertEqual(len(by_key['scd-under5-prophylaxis']['numerator']), 1)
        visible = {i.disease for i in clinical.visible(clinical.INDICATORS, {'diabetes', 'cardiac', 'scd'})}
        self.assertEqual(visible, {'diabetes', 'cardiac', 'scd'})
        june = clinical.find('dm-a1c-6m').evaluate(self.cohort, d(6, 30))
        self.assertEqual((len(june['numerator']), len(june['denominator'])), (1, 1))

    def test_deactivated_disease_not_counted(self):
        Disease.objects.filter(code='scd').update(is_active=False)
        cohort = Cohort(self.admin)
        self.assertEqual(len(cohort.ever_enrolled(d(12, 31))), 2)

    def test_pages_render_and_drill(self):
        self.client.force_login(self.admin)
        for url in (reverse('indicators:quarterly') + f'?year={Y}', reverse('indicators:clinical') + f'?date={Y}-09-30',
                    reverse('indicators:socio'), reverse('reporting:home'), reverse('reporting:study_progress'),
                    reverse('reporting:follow_up'), reverse('reporting:data_quality'), reverse('reporting:report')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        page = self.client.get(reverse('indicators:drill'), {'source': 'core', 'code': 'PP6', 'year': Y, 'q': 3})
        self.assertContains(page, self.a.patient_number)
        csv = self.client.get(reverse('indicators:quarterly'), {'year': Y, 'format': 'csv'})
        self.assertIn('Patients active at PEN-Plus clinic', csv.content.decode())
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(reverse('indicators:quarterly')).status_code, 200)
        nurse = self.make_user('n_i', 'Laboratory Staff', facility=self.ilala)
        self.client.force_login(nurse)
        self.assertEqual(self.client.get(reverse('indicators:quarterly')).status_code, 403)

from django.urls import reverse

from clinical.models import ClinicalProgramme, Diagnosis, Disease, ProgrammeEnrollment
from core.tests import BaseTestCase


class ReportTests(BaseTestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command('setup_diseases', stdout=open('/dev/null', 'w'))
        self.patient = self.make_patient()
        ProgrammeEnrollment.objects.create(patient=self.patient, programme=ClinicalProgramme.objects.get(code='dm-care'),
                                           facility=self.ilala, status='active')
        Diagnosis.objects.create(patient=self.patient, disease=Disease.objects.get(code='diabetes'),
                                 description='Type 2 diabetes mellitus', status='confirmed', clinician=self.admin)
        self.manager = self.make_user('dm_rep', 'Data Manager', facility=self.ilala)

    def test_reports_render_with_filters_pdf_and_csv(self):
        self.client.force_login(self.manager)
        names = ['recruitment', 'conditions', 'trends', 'study_progress', 'follow_up', 'report', 'data_quality']
        for name in names:
            url = reverse(f'reporting:{name}')
            for query in ('', '?period=last_month', '?period=custom&start=2020-01-01&end=2030-12-31&disease=diabetes',
                          f'?site={self.ilala.pk}&period=all', '?format=pdf'):
                with self.subTest(url=url + query):
                    response = self.client.get(url + query)
                    self.assertEqual(response.status_code, 200)
                    if 'format=pdf' in query:
                        self.assertEqual(response['Content-Type'], 'application/pdf')
        for name in ('recruitment', 'conditions', 'trends', 'follow_up'):
            response = self.client.get(reverse(f'reporting:{name}') + '?format=csv&period=all&disease=diabetes')
            self.assertIn('text/csv', response['Content-Type'])

    def test_condition_report_counts_and_patient_list(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('reporting:conditions') + '?disease=diabetes&period=all&condition=t2d')
        self.assertContains(response, 'Type 2')
        self.assertContains(response, self.patient.patient_number)
        csv_body = self.client.get(reverse('reporting:conditions') + '?disease=diabetes&period=all&format=csv').content.decode()
        self.assertIn('Type 2', csv_body)

    def test_recruitment_counts_enrolment(self):
        self.client.force_login(self.manager)
        body = self.client.get(reverse('reporting:recruitment') + '?period=all&format=csv').content.decode()
        total = [line for line in body.splitlines() if line.startswith('Total')][0].split(',')
        self.assertEqual(total[5], '1')   # enrolled


class DiseaseReportTests(BaseTestCase):
    def test_disease_report_by_site_and_condition(self):
        from clinical.models import ClinicalProgramme, Diagnosis, ProgrammeEnrollment
        from clinical.models import Disease
        p = self.make_patient()
        ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala,
                                           programme=ClinicalProgramme.objects.get(code='dm-care'))
        Diagnosis.objects.create(patient=p, disease=Disease.objects.get(code='diabetes'),
                                 description='Type 1 diabetes mellitus', status='confirmed', clinician=self.admin)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('reporting:disease'), {'disease': 'diabetes', 'period': 'all'})
        self.assertContains(response, 'Diabetes report')
        self.assertContains(response, self.ilala.name)
        self.assertEqual(response.context['data']['site_total']['enrolled'], 1)
        csv = self.client.get(reverse('reporting:disease'), {'disease': 'diabetes', 'period': 'all', 'format': 'csv'})
        self.assertEqual(csv.status_code, 200)

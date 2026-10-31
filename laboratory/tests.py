import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.tests import BaseTestCase

from .models import LabOrder, LabPanel, LabTest

User = get_user_model()


class LabCatalogueTests(BaseTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command('setup_penplus', stdout=open('/dev/null', 'w'))

    def setUp(self):
        self.patient = self.make_patient()
        self.lab = self.make_user('labx', 'Laboratory Staff', facility=self.ilala)
        self.manager = self.make_user('dmx', 'Data Manager', level='national')

    def order_fbc(self):
        self.client.force_login(self.clinician_ilala)
        self.client.post(reverse('investigations:request', args=[self.patient.pk]), {
            'panels': [LabPanel.objects.get(code='fbc').pk], 'facility': self.ilala.pk, 'priority': 'routine'})
        return list(LabOrder.objects.filter(patient=self.patient))

    def test_panel_order_creates_grouped_tests(self):
        orders = self.order_fbc()
        self.assertEqual({o.test.code for o in orders}, {'hb', 'wbc', 'plt', 'mcv'})
        self.assertEqual(len({o.request_group for o in orders}), 1)

    def test_collect_and_result_whole_panel_with_unit_snapshot(self):
        orders = self.order_fbc()
        first = orders[0]
        self.client.force_login(self.lab)
        self.client.post(reverse('laboratory:lab_action', args=[first.pk, 'collect']),
                         {'specimen_id': 'S1', 'collected_at': '2026-10-19T09:00'})
        self.assertEqual(set(LabOrder.objects.values_list('status', flat=True)), {'collected'})
        by_code = {o.test.code: o for o in LabOrder.objects.select_related('test')}
        self.client.post(reverse('laboratory:lab_action', args=[first.pk, 'result']), {
            f'r{by_code["hb"].pk}': '8.1', f'r{by_code["wbc"].pk}': '6.0', f'r{by_code["plt"].pk}': '250',
            f'r{by_code["mcv"].pk}': '85'})
        hb = LabOrder.objects.get(test__code='hb')
        self.assertEqual((hb.status, hb.result_unit, hb.is_abnormal, hb.reference_snapshot),
                         ('resulted', 'g/dL', True, '12–17 g/dL'))
        self.assertFalse(LabOrder.objects.get(test__code='wbc').is_abnormal)

    def test_unit_change_keeps_old_results_unless_relabelled(self):
        test = LabTest.objects.get(code='creat')
        order = LabOrder(patient=self.patient, facility=self.ilala, test=test, ordered_by=self.admin)
        order.record_result('1.1', self.admin)
        order.save()
        self.client.force_login(self.manager)
        data = {'code': 'creat', 'name': test.name, 'category': test.category_id or '', 'result_type': 'numeric',
                'unit': 'µmol/L', 'reference_low': '53', 'reference_high': '115', 'order': 10, 'is_active': 'on'}
        self.client.post(reverse('laboratory:test_update', args=[test.pk]), data)
        order.refresh_from_db()
        test.refresh_from_db()
        self.assertEqual((test.unit, order.result_unit), ('µmol/L', 'mg/dL'))  # old result keeps its unit
        LabTest.objects.filter(pk=test.pk).update(unit='mg/dL')
        self.client.post(reverse('laboratory:test_update', args=[test.pk]),
                         {**data, 'unit': 'mg/dl', 'apply_unit_to_existing': 'on'})
        order.refresh_from_db()
        self.assertEqual(order.result_unit, 'mg/dl')  # explicit relabel of results recorded with the old unit

    def test_choice_result_abnormal_flag(self):
        hiv = LabTest.objects.get(code='hiv')
        self.assertEqual(hiv.evaluate('Positive'), (None, True))
        self.assertEqual(hiv.evaluate('Negative'), (None, False))

    def test_catalogue_pages(self):
        self.client.force_login(self.manager)
        test = LabTest.objects.first()
        for url in (reverse('laboratory:catalogue'), reverse('laboratory:test_create'),
                    reverse('laboratory:test_update', args=[test.pk]), reverse('laboratory:panel_create'),
                    reverse('laboratory:category_create'), reverse('patients:duplicates')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(reverse('laboratory:test_create')).status_code, 403)

    def test_requests_listed_once_and_reviewed_together(self):
        orders = self.order_fbc()
        LabOrder.objects.update(status='resulted', result_value='5')
        self.client.force_login(self.clinician_ilala)
        response = self.client.get(reverse('laboratory:lab_worklist'), {'tab': 'review'})
        self.assertEqual(len(response.context['requests']), 1)
        self.assertEqual(response.context['tabs'][2][2], 1)  # doctor review count
        self.assertContains(self.client.get(reverse('laboratory:request_detail', args=[orders[0].request_group])),
                            'Haemoglobin')
        self.client.post(reverse('laboratory:lab_action', args=[orders[0].pk, 'review']))
        self.assertEqual(set(LabOrder.objects.values_list('status', flat=True)), {'reviewed'})

    def test_repeat_request_needs_confirmation(self):
        self.order_fbc()
        url = reverse('investigations:request', args=[self.patient.pk])
        data = {'panels': [LabPanel.objects.get(code='fbc').pk], 'facility': self.ilala.pk}
        response = self.client.post(url, data)
        self.assertContains(response, 'Already requested')
        self.assertEqual(LabOrder.objects.filter(patient=self.patient).count(), 4)
        self.client.post(url, {**data, 'confirm_repeat': 'on'})
        self.assertEqual(LabOrder.objects.filter(patient=self.patient).count(), 8)

    def test_request_limited_to_what_the_person_may_order(self):
        from investigations.models import InvestigationOrder, InvestigationType
        call_command('setup_investigations', stdout=open('/dev/null', 'w'))
        url = reverse('investigations:request', args=[self.patient.pk])
        self.client.force_login(self.make_user('reader', 'Report Viewer', facility=self.ilala))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.clinician_ilala)
        echo = InvestigationType.objects.get(code='echo')
        with mock.patch.object(User, 'has_perm', lambda user, perm, obj=None: perm != 'investigations.add_investigationorder'):
            response = self.client.post(url, {'types': [echo.pk], 'facility': self.ilala.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(InvestigationOrder.objects.filter(patient=self.patient).exists())

    def test_lab_and_radiology_sent_together_with_visit(self):
        from encounters.models import Encounter
        from investigations.models import InvestigationOrder, InvestigationType
        call_command('setup_investigations', stdout=open('/dev/null', 'w'))
        visit = Encounter.objects.create(patient=self.patient, facility=self.ilala, staff=self.admin)
        self.client.force_login(self.clinician_ilala)
        response = self.client.post(reverse('investigations:request', args=[self.patient.pk]), {
            'tests': [LabTest.objects.get(code='hba1c').pk], 'types': [InvestigationType.objects.get(code='echo').pk],
            'facility': self.ilala.pk, 'encounter': visit.pk})
        self.assertRedirects(response, visit.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(LabOrder.objects.get(patient=self.patient).priority, 'routine')
        self.assertEqual(InvestigationOrder.objects.get(patient=self.patient).description, 'Echocardiography')

    def test_page_preselects_from_link_and_hides_legacy_tests(self):
        self.assertTrue(LabTest.objects.filter(code='fe-legacy', is_active=True).exists())
        self.client.force_login(self.clinician_ilala)
        response = self.client.get(reverse('investigations:request', args=[self.patient.pk]), {'test': 'hba1c'})
        self.assertNotContains(response, 'legacy, test not specified')
        self.assertIn(f"test-{LabTest.objects.get(code='hba1c').pk}", response.context['selected'])

    def test_quick_picks_follow_the_subjects_programme(self):
        from clinical.models import ClinicalProgramme, ProgrammeEnrollment
        url = reverse('investigations:request', args=[self.patient.pk])
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(url).context['picks'], [])
        ProgrammeEnrollment.objects.create(patient=self.patient, facility=self.ilala,
                                           programme=ClinicalProgramme.objects.get(code='dm-care'))
        labels = [p['label'] for p in self.client.get(url).context['picks']]
        self.assertIn('Diabetes control', labels)
        self.assertNotIn('Heart failure monitoring', labels)

    def test_overdue_monitoring_test_is_the_first_quick_pick(self):
        from clinical.models import ClinicalProgramme, ProgrammeEnrollment
        ProgrammeEnrollment.objects.create(patient=self.patient, facility=self.ilala,
                                           enrollment_date=timezone.localdate() - datetime.timedelta(days=200),
                                           programme=ClinicalProgramme.objects.get(code='dm-care'))
        self.client.force_login(self.clinician_ilala)
        first = self.client.get(reverse('investigations:request', args=[self.patient.pk])).context['picks'][0]
        self.assertEqual((first['label'], first['badge']), ('HbA1c', 'Overdue'))

    def test_order_from_subject_page_links_todays_visit(self):
        from encounters.models import Encounter
        visit = Encounter.objects.create(patient=self.patient, facility=self.ilala, staff=self.admin)
        self.client.force_login(self.clinician_ilala)
        response = self.client.get(reverse('investigations:request', args=[self.patient.pk]))
        self.assertEqual(response.context['form'].initial['encounter'], visit.pk)

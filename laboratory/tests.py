from django.core.management import call_command
from django.urls import reverse

from core.tests import BaseTestCase

from .models import LabOrder, LabPanel, LabTest


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
        self.client.post(reverse('laboratory:lab_add', args=[self.patient.pk]), {
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

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.tests import BaseTestCase
from pharmacy.models import ConsumableUse, Medication, StockMovement, StockShortfall
from pharmacy.services import receive_stock

from .models import InvestigationOrder, InvestigationType


class InvestigationTests(BaseTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command('setup_investigations', stdout=open('/dev/null', 'w'))

    def setUp(self):
        self.patient = self.make_patient()
        self.echo = InvestigationType.objects.get(code='echo')
        self.imaging = self.make_user('img', 'Imaging / ECG / Echo Staff', facility=self.ilala)
        self.gel = Medication.objects.get(code='ultrasound-gel')

    def request(self):
        self.client.force_login(self.clinician_ilala)
        self.client.post(reverse('investigations:request', args=[self.patient.pk]), {
            'types': [self.echo.pk], 'clinical_notes': 'Dyspnoea', 'facility': self.ilala.pk})
        return InvestigationOrder.objects.get(patient=self.patient)

    def report(self, inv, **results):
        self.client.force_login(self.imaging)
        data = {'performed_at': timezone.now().strftime('%Y-%m-%dT%H:%M'), 'findings': '', 'conclusion': ''}
        data.update({f'r_{k}': v for k, v in results.items()})
        return self.client.post(reverse('investigations:report', args=[inv.pk]), data, follow=True)

    def test_request_copies_modality_and_structured_report_flags_abnormal(self):
        inv = self.request()
        self.assertEqual((inv.modality, inv.status), ('echo', 'requested'))
        self.client.force_login(self.imaging)
        self.assertContains(self.client.get(reverse('investigations:report', args=[inv.pk])), 'RHD (WHF criteria)')
        receive_stock(self.gel, self.ilala, 'G1', timezone.localdate().replace(year=timezone.localdate().year + 1),
                      100, self.admin)
        self.report(inv, lvef='35', rhd_whf='Definite RHD')
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'completed')
        self.assertEqual(inv.results, {'lvef': '35', 'rhd_whf': 'Definite RHD'})
        self.assertTrue(inv.is_abnormal)
        # 20 mL gel deducted once, even if the report is edited again
        self.assertEqual(StockMovement.objects.get(investigation=inv).quantity, -20)
        self.report(inv, lvef='40', rhd_whf='Definite RHD')
        self.assertEqual(StockMovement.objects.filter(investigation=inv).count(), 1)
        self.assertContains(self.client.get(reverse('investigations:detail', args=[inv.pk])), 'Definite RHD')

    def test_normal_report_and_stock_out_recorded_without_blocking(self):
        inv = self.request()
        response = self.report(inv, lvef='62', rhd_whf='No RHD')
        inv.refresh_from_db()
        self.assertFalse(inv.is_abnormal)
        self.assertContains(response, 'stock was short')
        shortfall = StockShortfall.objects.get(investigation=inv)
        self.assertEqual((shortfall.item, shortfall.quantity_missing), (self.gel, 20))

    def test_report_needs_some_result(self):
        inv = self.request()
        self.report(inv)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'requested')

    def test_catalogue_pages_and_national_only_editing(self):
        nat = self.make_user('dmn', 'Data Manager', level='national')
        site = self.make_user('dms', 'Data Manager', facility=self.ilala)
        self.client.force_login(nat)
        field = self.echo.fields.first()
        for url in (reverse('investigations:catalogue'), reverse('investigations:type_detail', args=[self.echo.pk]),
                    reverse('investigations:type_create'), reverse('investigations:type_update', args=[self.echo.pk]),
                    reverse('investigations:field_create', args=[self.echo.pk]),
                    reverse('investigations:field_update', args=[field.pk]),
                    reverse('pharmacy:consumable_add') + f'?investigation_type={self.echo.pk}',
                    reverse('pharmacy:shortfall_list'), reverse('investigations:worklist')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(site)
        self.assertEqual(self.client.get(reverse('investigations:catalogue')).status_code, 200)
        self.assertEqual(self.client.get(reverse('investigations:type_create')).status_code, 403)
        self.assertEqual(self.client.get(reverse('pharmacy:consumable_add') + f'?investigation_type={self.echo.pk}').status_code, 403)

    def test_consumable_rule_add_and_toggle(self):
        self.client.force_login(self.admin)
        ecg = InvestigationType.objects.get(code='ecg-12-lead')
        lancets = Medication.objects.get(code='lancets')
        self.client.post(reverse('pharmacy:consumable_add') + f'?investigation_type={ecg.pk}',
                         {'item': lancets.pk, 'quantity': 2})
        rule = ConsumableUse.objects.get(investigation_type=ecg, item=lancets)
        self.assertEqual(rule.quantity, 2)
        self.client.post(reverse('pharmacy:consumable_toggle', args=[rule.pk]))
        rule.refresh_from_db()
        self.assertFalse(rule.is_active)


class LabConsumptionTests(BaseTestCase):
    def test_lab_result_deducts_fefo_across_batches(self):
        from laboratory.models import LabOrder, LabTest
        call_command('setup_investigations', stdout=open('/dev/null', 'w'))
        test = LabTest.objects.filter(code='hba1c').first() or LabTest.objects.create(code='hba1c', name='HbA1c', unit='%')
        cartridge = Medication.objects.get(code='hba1c-cartridge')
        ConsumableUse.objects.get_or_create(item=cartridge, lab_test=test, defaults={'quantity': 1})
        today = timezone.localdate()
        soon = receive_stock(cartridge, self.ilala, 'S', today.replace(year=today.year + 1), 1, self.admin)
        later = receive_stock(cartridge, self.ilala, 'L', today.replace(year=today.year + 2), 5, self.admin)
        patient = self.make_patient()
        orders = [LabOrder.objects.create(patient=patient, facility=self.ilala, test=test, ordered_by=self.admin,
                                   status='collected') for _ in range(2)]
        self.client.force_login(self.admin)
        for order in orders:
            self.client.post(reverse('laboratory:lab_action', args=[order.pk, 'result']),
                             {f'r{order.pk}': '7.1', 'result_notes': ''})
        soon.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual((soon.quantity_on_hand, later.quantity_on_hand), (0, 4))

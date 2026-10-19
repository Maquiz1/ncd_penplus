import datetime

from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent
from core.tests import BaseTestCase
from encounters.models import Encounter
from patients.services import record_death

from .models import Medication, Prescription, StockBatch, StockMovement
from .services import StockError, adjust_stock, available_batches, dispense, low_stock_rows, receive_stock


class PharmacyTestCase(BaseTestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.metformin = Medication.objects.get(code='metformin-500')
        self.patient = self.make_patient()
        self.pharmacist = self.make_user('pharm', 'Pharmacist / Dispenser', facility=self.ilala)

    def prescribe(self, quantity=60, facility=None, **kwargs):
        return Prescription.objects.create(patient=self.patient, facility=facility or self.ilala,
                                           medication=self.metformin, dose='500 mg', frequency='BD',
                                           quantity=quantity, prescriber=self.admin, **kwargs)

    def receive(self, batch_number, quantity, days_to_expiry=365, facility=None):
        return receive_stock(self.metformin, facility or self.ilala, batch_number,
                             self.today + datetime.timedelta(days=days_to_expiry), quantity, self.admin)


class StockLedgerTests(PharmacyTestCase):
    def test_receipt_creates_batch_and_ledger_entry(self):
        batch = self.receive('B1', 100)
        self.assertEqual(batch.quantity_on_hand, 100)
        movement = batch.movements.get()
        self.assertEqual((movement.movement_type, movement.quantity, movement.balance_after), ('receipt', 100, 100))
        batch = self.receive('B1', 50)
        self.assertEqual(batch.quantity_on_hand, 150)

    def test_same_batch_number_with_different_expiry_rejected(self):
        self.receive('B1', 100, days_to_expiry=200)
        with self.assertRaises(StockError):
            self.receive('B1', 10, days_to_expiry=300)

    def test_adjustments_require_reason_and_cannot_go_negative(self):
        batch = self.receive('B1', 10)
        with self.assertRaises(StockError):
            adjust_stock(batch, -5, '', self.admin)
        with self.assertRaises(StockError):
            adjust_stock(batch, -11, 'Count', self.admin)
        adjust_stock(batch, -3, 'Damaged', self.admin, write_off=True)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_on_hand, 7)
        self.assertEqual(batch.movements.filter(movement_type='write_off').get().reason, 'Damaged')

    def test_movements_are_append_only(self):
        movement = self.receive('B1', 10).movements.get()
        movement.quantity = 999
        with self.assertRaises(ValueError):
            movement.save()
        with self.assertRaises(ValueError):
            movement.delete()

    def test_balance_always_matches_ledger(self):
        batch = self.receive('B1', 100)
        rx = self.prescribe(30)
        dispense(rx, batch, 30, self.admin)
        adjust_stock(batch, 5, 'Recount', self.admin)
        batch.refresh_from_db()
        total = sum(batch.movements.values_list('quantity', flat=True))
        self.assertEqual(batch.quantity_on_hand, total)
        self.assertEqual(batch.quantity_on_hand, 75)


class DispensingTests(PharmacyTestCase):
    def test_fefo_order_and_expired_batches_excluded(self):
        later = self.receive('LATE', 50, days_to_expiry=300)
        sooner = self.receive('SOON', 50, days_to_expiry=30)
        expired = StockBatch.objects.create(medication=self.metformin, facility=self.ilala, batch_number='OLD',
                                            expiry_date=self.today - datetime.timedelta(days=1))
        StockBatch.objects.filter(pk=expired.pk).update(quantity_on_hand=40)
        self.assertEqual(list(available_batches(self.metformin, self.ilala)), [sooner, later])

    def test_cannot_dispense_expired_batch(self):
        batch = StockBatch.objects.create(medication=self.metformin, facility=self.ilala, batch_number='OLD',
                                          expiry_date=self.today - datetime.timedelta(days=1))
        StockBatch.objects.filter(pk=batch.pk).update(quantity_on_hand=40)
        batch.refresh_from_db()
        with self.assertRaises(StockError):
            dispense(self.prescribe(), batch, 10, self.admin)

    def test_partial_then_full_dispensing(self):
        small = self.receive('SMALL', 20, days_to_expiry=30)
        big = self.receive('BIG', 100, days_to_expiry=300)
        rx = self.prescribe(60)
        dispense(rx, small, 20, self.admin)
        rx.refresh_from_db()
        self.assertEqual((rx.status, rx.quantity_remaining), ('partially_dispensed', 40))
        dispense(rx, big, 40, self.admin)
        rx.refresh_from_db()
        self.assertEqual(rx.status, 'dispensed')
        with self.assertRaises(StockError):
            dispense(rx, big, 1, self.admin)

    def test_cannot_overdispense_or_exceed_stock(self):
        batch = self.receive('B1', 10)
        rx = self.prescribe(60)
        with self.assertRaises(StockError):
            dispense(rx, batch, 11, self.admin)
        batch = self.receive('B2', 100)
        with self.assertRaises(StockError):
            dispense(rx, batch, 61, self.admin)

    def test_batch_must_be_at_dispensing_facility(self):
        batch = self.receive('KIN', 100, facility=self.kinondoni)
        with self.assertRaises(StockError):
            dispense(self.prescribe(), batch, 10, self.admin)

    def test_dispense_view_flow_is_audited(self):
        batch = self.receive('B1', 100)
        rx = self.prescribe(30)
        self.client.force_login(self.pharmacist)
        response = self.client.get(reverse('pharmacy:dispense', args=[rx.pk]))
        self.assertContains(response, 'B1')
        response = self.client.post(reverse('pharmacy:dispense', args=[rx.pk]),
                                    {'batch': batch.pk, 'quantity': 30, 'counselling_notes': 'Take with food'})
        self.assertEqual(response.status_code, 302)
        rx.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual((rx.status, batch.quantity_on_hand), ('dispensed', 70))
        self.assertTrue(AuditEvent.objects.filter(actor=self.pharmacist, model='pharmacy.Dispensing').exists())


class PrescribingTests(PharmacyTestCase):
    def test_clinician_prescribes_from_encounter(self):
        encounter = Encounter.objects.create(patient=self.patient, facility=self.ilala, staff=self.admin)
        self.client.force_login(self.clinician_ilala)
        response = self.client.post(
            reverse('pharmacy:prescription_add', args=[self.patient.pk]) + f'?encounter={encounter.pk}',
            {'medication': self.metformin.pk, 'dose': '500 mg', 'route': 'oral', 'frequency': 'BD',
             'duration_days': 30, 'quantity': 60, 'facility': self.ilala.pk, 'encounter': encounter.pk})
        self.assertRedirects(response, encounter.get_absolute_url(), fetch_redirect_response=False)
        rx = Prescription.objects.get(patient=self.patient)
        self.assertEqual((rx.prescriber, rx.encounter, rx.status), (self.clinician_ilala, encounter, 'prescribed'))

    def test_clinician_cannot_dispense_or_adjust(self):
        batch = self.receive('B1', 10)
        rx = self.prescribe()
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(reverse('pharmacy:dispense', args=[rx.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('pharmacy:stock_adjust', args=[batch.pk])).status_code, 403)

    def test_pharmacist_scope_limited_to_facility(self):
        rx_other = Prescription.objects.create(
            patient=self.make_patient(self.mwanza), facility=self.mwanza, medication=self.metformin, dose='500 mg',
            frequency='BD', quantity=10, prescriber=self.admin)
        self.client.force_login(self.pharmacist)
        self.assertEqual(self.client.get(reverse('pharmacy:dispense', args=[rx_other.pk])).status_code, 404)
        batch_other = self.receive('MW', 10, facility=self.mwanza)
        self.assertEqual(self.client.get(reverse('pharmacy:stock_adjust', args=[batch_other.pk])).status_code, 404)

    def test_cancel_requires_reason(self):
        rx = self.prescribe()
        self.client.force_login(self.clinician_ilala)
        self.client.post(reverse('pharmacy:prescription_cancel', args=[rx.pk]), {'reason': ''})
        rx.refresh_from_db()
        self.assertEqual(rx.status, 'prescribed')
        self.client.post(reverse('pharmacy:prescription_cancel', args=[rx.pk]), {'reason': 'Changed regimen'})
        rx.refresh_from_db()
        self.assertEqual((rx.status, rx.cancel_reason), ('cancelled', 'Changed regimen'))

    def test_death_cancels_open_prescriptions(self):
        rx = self.prescribe()
        record_death(self.patient, self.today, 'Stroke', self.admin)
        rx.refresh_from_db()
        self.assertEqual(rx.status, 'cancelled')


class StockReportingTests(PharmacyTestCase):
    def test_low_stock_detection(self):
        self.receive('B1', self.metformin.reorder_level - 1)
        rows = [(f, m) for f, m, _ in low_stock_rows([self.ilala])]
        self.assertIn((self.ilala, self.metformin), rows)
        self.receive('B2', 10)
        rows = [(f, m) for f, m, _ in low_stock_rows([self.ilala])]
        self.assertNotIn((self.ilala, self.metformin), rows)

    def test_pages_render(self):
        batch = self.receive('B1', 100)
        rx = self.prescribe()
        self.client.force_login(self.pharmacist)
        for url in (reverse('pharmacy:medication_list'), reverse('pharmacy:stock_list'),
                    reverse('pharmacy:stock_list') + '?view=expiring', reverse('pharmacy:stock_list') + '?view=expired',
                    reverse('pharmacy:stock_receive'), reverse('pharmacy:stock_adjust', args=[batch.pk]),
                    reverse('pharmacy:movement_list'), reverse('pharmacy:movement_list') + f'?batch={batch.pk}',
                    reverse('pharmacy:prescription_list'), reverse('pharmacy:dispense', args=[rx.pk]),
                    reverse('dashboard'), reverse('reporting:report')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.admin)
        for url in (reverse('pharmacy:medication_create'), reverse('pharmacy:medication_update', args=[self.metformin.pk]),
                    reverse('pharmacy:prescription_add', args=[self.patient.pk]),
                    reverse('pharmacy:prescription_cancel', args=[rx.pk]),
                    reverse('patients:detail', args=[self.patient.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_receive_view_rejects_expired_batch(self):
        self.client.force_login(self.pharmacist)
        response = self.client.post(reverse('pharmacy:stock_receive'), {
            'medication': self.metformin.pk, 'facility': self.ilala.pk, 'batch_number': 'X',
            'expiry_date': (self.today - datetime.timedelta(days=1)).isoformat(), 'quantity': 10,
            'received_date': self.today.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StockMovement.objects.exists())

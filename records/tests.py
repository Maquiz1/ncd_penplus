import datetime

from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent
from clinical.models import Diagnosis, Disease
from core.tests import BaseTestCase
from encounters.models import Appointment, Encounter, Vitals

from .registry import record_types


class RecordsTests(BaseTestCase):
    def setUp(self):
        self.patient = self.make_patient()
        self.manager = self.make_user('dm_r', 'Data Manager', facility=self.ilala)
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        self.appointment = Appointment.objects.create(patient=self.patient, facility=self.ilala,
                                                      scheduled_date=yesterday, status='attended')
        self.encounter = Encounter.objects.create(patient=self.patient, facility=self.ilala, staff=self.admin,
                                                  appointment=self.appointment)
        self.vitals = Vitals.objects.create(encounter=self.encounter, systolic_bp=130, diastolic_bp=80)

    def test_every_list_renders(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('records:home')).status_code, 200)
        for rtype in record_types():
            with self.subTest(rtype=rtype.key):
                self.assertEqual(self.client.get(reverse('records:list', args=[rtype.key])).status_code, 200)
                self.assertEqual(self.client.get(reverse('records:list', args=[rtype.key]) + '?tab=deleted&q=x'
                                                 '&start=2020-01-01&end=2030-01-01').status_code, 200)

    def test_delete_encounter_cascades_reopens_appointment_and_superuser_restores(self):
        self.client.force_login(self.manager)
        url = reverse('records:delete', args=['encounter', self.encounter.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'reason': ''})
        self.assertFalse(Encounter.all_objects.get(pk=self.encounter.pk).is_deleted)  # reason required
        self.client.post(url, {'reason': 'Entered on the wrong subject'})
        self.assertFalse(Encounter.objects.filter(pk=self.encounter.pk).exists())
        self.assertFalse(Vitals.objects.filter(pk=self.vitals.pk).exists())
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, 'missed')
        self.assertFalse(self.patient.encounters.exists())       # hidden everywhere
        self.assertTrue(AuditEvent.objects.filter(action='delete', model='encounters.Encounter').exists())
        # Only superusers see the deleted tab and restore
        self.assertEqual(self.client.get(reverse('records:list', args=['encounter']) + '?tab=deleted').status_code, 403)
        self.assertEqual(self.client.post(reverse('records:restore', args=['encounter', self.encounter.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse('records:restore', args=['encounter', self.encounter.pk]))
        self.assertTrue(Encounter.objects.filter(pk=self.encounter.pk).exists())
        self.assertTrue(Vitals.objects.filter(pk=self.vitals.pk).exists())
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, 'attended')

    def test_encounter_with_orders_cannot_be_deleted(self):
        from laboratory.models import LabOrder, LabTest
        LabOrder.objects.create(patient=self.patient, facility=self.ilala, test=LabTest.objects.first(),
                                ordered_by=self.admin, encounter=self.encounter)
        self.client.force_login(self.manager)
        response = self.client.post(reverse('records:delete', args=['encounter', self.encounter.pk]), {'reason': 'x'})
        self.assertContains(response, 'delete or cancel them first')
        self.assertTrue(Encounter.objects.filter(pk=self.encounter.pk).exists())

    def test_generic_edit_is_audited_and_nurse_cannot_delete(self):
        self.client.force_login(self.manager)
        url = reverse('records:edit', args=['vitals', self.vitals.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'taken_at': timezone.now().strftime('%Y-%m-%dT%H:%M'), 'systolic_bp': 140,
                               'diastolic_bp': 90})
        self.vitals.refresh_from_db()
        self.assertEqual(self.vitals.systolic_bp, 140)
        self.assertTrue(AuditEvent.objects.filter(action='update', model='encounters.Vitals').exists())
        nurse = self.make_user('nurse_r', 'Nurse / Triage Officer', facility=self.ilala)
        self.client.force_login(nurse)
        self.assertEqual(self.client.get(reverse('records:delete', args=['vitals', self.vitals.pk])).status_code, 403)

    def test_scope_limits_records(self):
        other = self.make_patient(self.mwanza)
        diagnosis = Diagnosis.objects.create(patient=other, disease=Disease.objects.get(code='diabetes'),
                                             description='T2DM', clinician=self.admin)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('records:delete', args=['diagnosis', diagnosis.pk])).status_code, 404)


class DiagnosisPromptTests(BaseTestCase):
    def test_results_prompt_then_closes_when_confirmed(self):
        from clinical.models import ClinicalProgramme, ProgrammeEnrollment
        from issues.detectors import diagnosis_prompts
        from issues.models import DataIssue
        from laboratory.models import LabOrder, LabTest
        patient = self.make_patient()
        ProgrammeEnrollment.objects.create(patient=patient, programme=ClinicalProgramme.objects.get(code='dm-care'),
                                           facility=self.ilala, status='active')
        test = LabTest.objects.filter(code='hba1c').first() or LabTest.objects.create(code='hba1c', name='HbA1c', unit='%')
        order = LabOrder.objects.create(patient=patient, facility=self.ilala, test=test, ordered_by=self.admin)
        order.record_result('9.2', self.admin)
        order.save()
        diagnosis_prompts([patient.pk])
        issue = DataIssue.objects.get(patient=patient, code='diagnosis_to_confirm_after_results')
        self.assertIn('HbA1c', issue.description)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('patients:detail', args=[patient.pk])), 'Results available')
        Diagnosis.objects.create(patient=patient, disease=Disease.objects.get(code='diabetes'), description='T2DM',
                                 status='confirmed', clinician=self.admin)
        diagnosis_prompts([patient.pk])
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'resolved')

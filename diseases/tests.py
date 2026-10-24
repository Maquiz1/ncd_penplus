from django.urls import reverse

from clinical.forms import ScreeningCreateForm
from clinical.models import Diagnosis, Disease
from core.tests import BaseTestCase

from .models import Condition, match_condition


class DiseaseManagementTests(BaseTestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command('setup_diseases', stdout=open('/dev/null', 'w'))
        self.manager = self.make_user('dm_d', 'Data Manager', level='national')

    def test_only_three_active_and_conditions_exist(self):
        self.assertEqual(set(Disease.objects.filter(is_active=True).values_list('code', flat=True)),
                         {'diabetes', 'cardiac', 'scd'})
        self.assertEqual(Condition.objects.get(code='hf').excludes.count(), 0)   # counted as recorded
        self.assertEqual(Condition.objects.filter(disease__code='cardiac', parent__isnull=True).count(), 13)
        self.assertTrue(Condition.objects.filter(code='rhd-pure-mitral-stenosis', parent__code='rhd').exists())

    def test_keyword_mapping(self):
        cardiac = Disease.objects.get(code='cardiac')
        self.assertEqual(match_condition('Rheumatic heart disease – Mixed mitral valve disease (MS + MR)', cardiac)[0].code,
                         'rhd-mixed-mitral-valve-disease-ms-mr')
        self.assertEqual(match_condition('Cardiomyopathy – Peripartum', cardiac)[0].code, 'cardiomyopathy-peripartum')
        self.assertEqual(match_condition('Cardiomyopathy – Arrhythmia-related', cardiac)[0].code,
                         'cardiomyopathy-arrhythmia-related')
        self.assertEqual(match_condition('Hypertensive heart disease', cardiac)[0].code, 'hhd')
        self.assertEqual(match_condition('Rheumatic heart disease – Other: tricuspid', cardiac)[0].code, 'rhd-other')
        condition, matched = match_condition('Cardiac disease (category recorded in EDCS)', cardiac)
        self.assertEqual(condition.code, 'cardiac-unspecified')
        p = self.make_patient()
        d = Diagnosis.objects.create(patient=p, disease=Disease.objects.get(code='diabetes'),
                                     description='Type 1 diabetes mellitus', clinician=self.admin)
        self.assertEqual(d.condition.code, 't1d')

    def test_deactivate_hides_from_forms(self):
        self.client.force_login(self.manager)
        scd = Disease.objects.get(code='scd')
        self.client.post(reverse('diseases:toggle', args=[scd.pk]))
        scd.refresh_from_db()
        self.assertFalse(scd.is_active)
        form = ScreeningCreateForm(patient=self.make_patient(), user=self.admin)
        self.assertNotIn(scd, form.fields['diseases'].queryset)

    def test_soft_delete_and_superuser_restore(self):
        epilepsy = Disease.objects.get(code='epilepsy')
        self.client.force_login(self.manager)
        self.client.post(reverse('diseases:delete', args=[epilepsy.pk]), {'reason': 'Not in scope'})
        self.assertFalse(Disease.objects.filter(pk=epilepsy.pk).exists())
        self.assertTrue(Disease.all_objects.get(pk=epilepsy.pk).is_deleted)
        self.assertEqual(self.client.post(reverse('diseases:restore', args=[epilepsy.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('diseases:list')), 'Deleted diseases')
        self.client.post(reverse('diseases:restore', args=[epilepsy.pk]))
        restored = Disease.objects.get(pk=epilepsy.pk)
        self.assertFalse(restored.is_active)  # comes back inactive

    def test_pages(self):
        self.client.force_login(self.manager)
        cardiac = Disease.objects.get(code='cardiac')
        for url in (reverse('diseases:list'), reverse('diseases:create'), cardiac.get_absolute_url(),
                    reverse('diseases:update', args=[cardiac.pk]), reverse('diseases:condition_create', args=[cardiac.pk]),
                    reverse('diseases:condition_update', args=[cardiac.pk, Condition.objects.get(code='rhd').pk]),
                    reverse('diseases:delete', args=[cardiac.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(reverse('diseases:create')).status_code, 403)


class MultiDiagnosisTests(BaseTestCase):
    def test_record_several_cardiac_conditions_at_once(self):
        from django.core.management import call_command
        call_command('setup_diseases', stdout=open('/dev/null', 'w'))
        patient = self.make_patient()
        cardiac = Disease.objects.get(code='cardiac')
        picks = Condition.objects.filter(code__in=['rhd-pure-mitral-regurgitation', 'hf', 'arrhythmia-atrial-fibrillation'])
        self.client.force_login(self.clinician_ilala)
        self.client.post(reverse('clinical:diagnosis_add', args=[patient.pk]), {
            'disease': cardiac.pk, 'conditions': [c.pk for c in picks], 'status': 'confirmed',
            'diagnosis_date': '2026-10-01'})
        diagnoses = {d.condition.code: d.description for d in Diagnosis.objects.filter(patient=patient)}
        self.assertEqual(set(diagnoses), {'rhd-pure-mitral-regurgitation', 'hf', 'arrhythmia-atrial-fibrillation'})
        self.assertEqual(diagnoses['rhd-pure-mitral-regurgitation'], 'Rheumatic heart disease – Pure mitral regurgitation')
        # conditions of another disease are refused
        response = self.client.post(reverse('clinical:diagnosis_add', args=[patient.pk]), {
            'disease': Disease.objects.get(code='diabetes').pk, 'conditions': [picks[0].pk], 'status': 'confirmed',
            'diagnosis_date': '2026-10-01'})
        self.assertContains(response, 'Choose only conditions of Diabetes')

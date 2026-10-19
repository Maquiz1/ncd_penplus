import datetime
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent
from clinical.models import ClinicalProgramme, Diagnosis, Disease, ProgrammeEnrollment, Screening
from core.utils import last_sunday
from crf.models import FormDefinition, FormField, FormSection, FormSubmission, FormVersion
from crf.services import DynamicCRFForm, applicable_versions, clone_version, publish_version
from encounters.models import Appointment, Encounter, FollowUpPlan
from encounters.services import generate_visits, revise_plan
from facilities.models import Facility, StaffAssignment
from patients.models import Patient, PatientTransfer
from patients.services import apply_transfer_action, next_patient_number, register_patient
from reporting.models import ReportRun
from research.models import ConsentRecord, Study, StudyEligibilityAssessment, StudyEnrollment

User = get_user_model()


class BaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_reference_data', verbosity=0, stdout=open('/dev/null', 'w'))
        cls.ilala = Facility.objects.get(code='DEMO-ILA-HC')
        cls.kinondoni = Facility.objects.get(code='DEMO-KIN-HOSP')
        cls.mwanza = Facility.objects.get(code='DEMO-NYA-HOSP')
        cls.diabetes = Disease.objects.get(code='diabetes')
        cls.cardiac = Disease.objects.get(code='cardiac')
        cls.admin = User.objects.create_superuser('admin', 'admin@example.org', 'Adm1n-pass-123')
        cls.clinician_ilala = cls.make_user('clin_ilala', 'Clinician', facility=cls.ilala)
        cls.clinician_mwanza = cls.make_user('clin_mwanza', 'Clinician', facility=cls.mwanza)
        cls.lab_ilala = cls.make_user('lab_ilala', 'Laboratory Staff', facility=cls.ilala)

    @classmethod
    def make_user(cls, username, role, facility=None, level='facility', **assignment):
        user = User.objects.create_user(username, f'{username}@example.org', 'Str0ng-pass-123')
        user.groups.add(Group.objects.get(name=role))
        if facility or level != 'facility':
            StaffAssignment.objects.create(user=user, level=level, facility=facility, **assignment)
        return user

    def make_patient(self, facility=None, **kwargs):
        data = dict(first_name='Asha', last_name='Mushi', sex='female', date_of_birth=datetime.date(1980, 5, 1),
                    phone='0712000000', registration_facility=facility or self.ilala)
        data.update(kwargs)
        return register_patient(Patient(**data), self.admin)


class PatientIdentifierTests(BaseTestCase):
    def test_format_and_sequence(self):
        self.assertEqual(next_patient_number(2026), 'NCD26-0001')
        self.assertEqual(next_patient_number(2026), 'NCD26-0002')
        self.assertEqual(next_patient_number(2027), 'NCD27-0001')

    def test_ids_never_reused_after_soft_delete(self):
        first = self.make_patient()
        first.soft_delete(self.admin, 'test')
        second = self.make_patient(first_name='Other')
        self.assertNotEqual(first.patient_number, second.patient_number)

    def test_suffix_expands_beyond_four_digits(self):
        from patients.models import PatientIdSequence
        PatientIdSequence.objects.create(year=2026, last_value=9999)
        self.assertEqual(next_patient_number(2026), 'NCD26-10000')

    def test_registration_opens_assignment(self):
        p = self.make_patient()
        self.assertTrue(re.fullmatch(r'NCD\d{2}-\d{4,}', p.patient_number))
        self.assertEqual(p.current_facility, self.ilala)
        self.assertEqual(p.facility_assignments.get(is_current=True).facility, self.ilala)


class AccessScopeTests(BaseTestCase):
    def test_facility_user_only_sees_own_facility(self):
        mine = self.make_patient(self.ilala)
        other = self.make_patient(self.mwanza, first_name='Juma')
        self.client.force_login(self.clinician_ilala)
        response = self.client.get(reverse('patients:list'))
        self.assertContains(response, mine.patient_number)
        self.assertNotContains(response, other.patient_number)
        self.assertEqual(self.client.get(reverse('patients:detail', args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('encounters:encounter_add', args=[other.pk])).status_code, 404)

    def test_region_scope_covers_its_facilities(self):
        region = self.ilala.district.region
        coordinator = self.make_user('coord', 'Geographic Coordinator', level='region', region=region)
        in_region = self.make_patient(self.kinondoni)
        out_region = self.make_patient(self.mwanza)
        visible = set(Patient.objects.in_scope(coordinator))
        self.assertIn(in_region, visible)
        self.assertNotIn(out_region, visible)

    def test_user_without_assignment_sees_nothing(self):
        self.make_patient()
        nobody = self.make_user('nobody', 'Clinician')
        self.assertEqual(Patient.objects.in_scope(nobody).count(), 0)

    def test_identity_hidden_without_permission(self):
        p = self.make_patient()
        viewer = self.make_user('pi2', 'PI / Co-Investigator', facility=self.ilala)
        self.client.force_login(viewer)
        response = self.client.get(reverse('patients:detail', args=[p.pk]))
        self.assertContains(response, p.patient_number)
        self.assertNotContains(response, 'Mushi')
        # Name search must not work without identity permission.
        response = self.client.get(reverse('patients:list'), {'q': 'Mushi'})
        self.assertNotContains(response, p.patient_number)

    def test_inactive_user_cannot_log_in(self):
        self.clinician_ilala.is_active = False
        self.clinician_ilala.save()
        self.assertFalse(self.client.login(username='clin_ilala', password='Str0ng-pass-123'))

    def test_anonymous_redirected_to_login(self):
        response = self.client.get('/')
        self.assertRedirects(response, '/accounts/login/?next=/')


class ClinicalRuleTests(BaseTestCase):
    def test_screening_does_not_create_diagnosis_or_enrollment(self):
        p = self.make_patient()
        self.client.force_login(self.clinician_ilala)
        response = self.client.post(reverse('clinical:screening_add', args=[p.pk]), {
            'diseases': [self.diabetes.pk, self.cardiac.pk], 'facility': self.ilala.pk, 'source': 'community',
            'location_type': 'community', 'screening_date': '2026-10-01', 'result': 'positive'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Screening.objects.filter(patient=p).count(), 2)
        self.assertFalse(Diagnosis.objects.filter(patient=p).exists())
        self.assertFalse(ProgrammeEnrollment.objects.filter(patient=p).exists())
        self.assertFalse(StudyEnrollment.objects.filter(patient=p).exists())

    def test_one_open_enrollment_per_programme(self):
        p = self.make_patient()
        programme = ClinicalProgramme.objects.get(code='dm-care')
        ProgrammeEnrollment.objects.create(patient=p, programme=programme, facility=self.ilala)
        duplicate = ProgrammeEnrollment(patient=p, programme=programme, facility=self.ilala)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()


class ResearchRuleTests(BaseTestCase):
    def setUp(self):
        self.study = Study.objects.create(code='dm-study', title='DM study', status='active',
                                          current_consent_version='1.0')
        self.patient = self.make_patient()

    def test_enrollment_requires_eligibility_and_consent(self):
        ineligible = StudyEligibilityAssessment.objects.create(patient=self.patient, study=self.study,
                                                               is_eligible=False, reason='Age',
                                                               assessed_by=self.admin)
        declined = ConsentRecord.objects.create(patient=self.patient, study=self.study, consent_version='1.0',
                                                decision='declined', obtained_by=self.admin)
        enrollment = StudyEnrollment(patient=self.patient, study=self.study, participant_id='P1', facility=self.ilala,
                                     eligibility=ineligible, consent=declined)
        with self.assertRaises(ValidationError) as ctx:
            enrollment.full_clean()
        self.assertIn('eligibility', ctx.exception.message_dict)
        self.assertIn('consent', ctx.exception.message_dict)

    def test_withdrawal_does_not_stop_clinical_care(self):
        programme = ProgrammeEnrollment.objects.create(patient=self.patient, facility=self.ilala,
                                                       programme=ClinicalProgramme.objects.get(code='dm-care'))
        eligible = StudyEligibilityAssessment.objects.create(patient=self.patient, study=self.study, is_eligible=True,
                                                             assessed_by=self.admin)
        consent = ConsentRecord.objects.create(patient=self.patient, study=self.study, consent_version='1.0',
                                               decision='consented', obtained_by=self.admin)
        enrollment = StudyEnrollment.objects.create(patient=self.patient, study=self.study, participant_id='P1',
                                                    facility=self.ilala, eligibility=eligible, consent=consent)
        enrollment.status, enrollment.outcome_date = 'withdrawn', timezone.localdate()
        enrollment.full_clean()
        enrollment.save()
        programme.refresh_from_db()
        self.assertEqual(programme.status, 'active')
        self.assertEqual(self.patient.status, 'active')


class TransferTests(BaseTestCase):
    def test_full_transfer_updates_current_assignment_and_keeps_history(self):
        p = self.make_patient(self.ilala)
        transfer = PatientTransfer.objects.create(patient=p, from_facility=self.ilala, to_facility=self.kinondoni,
                                                  reason='Moved')
        for action in ('approve', 'send', 'receive'):
            apply_transfer_action(transfer, action, self.admin)
        p.refresh_from_db()
        self.assertEqual(p.current_facility, self.kinondoni)
        self.assertEqual(p.registration_facility, self.ilala)
        self.assertEqual(p.facility_assignments.count(), 2)
        self.assertEqual(p.facility_assignments.get(is_current=True).facility, self.kinondoni)
        self.assertTrue(AuditEvent.objects.filter(action='transfer', model='patients.PatientTransfer').exists())

    def test_invalid_transition_rejected(self):
        p = self.make_patient()
        transfer = PatientTransfer.objects.create(patient=p, from_facility=self.ilala, to_facility=self.kinondoni,
                                                  reason='Moved')
        with self.assertRaises(ValueError):
            apply_transfer_action(transfer, 'receive', self.admin)

    def test_receiving_side_enforced(self):
        p = self.make_patient(self.ilala)
        transfer = PatientTransfer.objects.create(patient=p, from_facility=self.ilala, to_facility=self.mwanza,
                                                  reason='Moved', status='sent')
        self.client.force_login(self.clinician_ilala)
        response = self.client.post(reverse('patients:transfer_action', args=[transfer.pk, 'receive']))
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.clinician_mwanza)
        self.client.post(reverse('patients:transfer_action', args=[transfer.pk, 'receive']))
        p.refresh_from_db()
        self.assertEqual(p.current_facility, self.mwanza)


class CRFTests(BaseTestCase):
    def test_published_version_is_immutable(self):
        version = FormDefinition.objects.get(code='vital-signs').published_version
        field = version.fields().first()
        field.label = 'Changed'
        with self.assertRaises(ValidationError):
            field.save()

    def test_new_version_keeps_old_submissions(self):
        definition = FormDefinition.objects.get(code='vital-signs')
        v1 = definition.published_version
        p = self.make_patient()
        submission = FormSubmission.objects.create(version=v1, patient=p, data={'sbp': 120}, status='submitted')
        v2 = clone_version(v1, self.admin)
        FormField.objects.create(section=v2.sections.first(), key='temp', label='Temperature', field_type='decimal')
        publish_version(v2, self.admin)
        v1.refresh_from_db()
        submission.refresh_from_db()
        self.assertEqual(v1.status, 'retired')
        self.assertEqual(submission.version, v1)
        self.assertEqual(definition.published_version, v2)

    def test_server_side_validation_and_conditional_fields(self):
        version = FormDefinition.objects.get(code='general-prescreening').published_version
        form = DynamicCRFForm({'lives_in_area': 'no'}, version=version)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.answers()['months_in_area'])
        form = DynamicCRFForm({}, version=version)
        self.assertFalse(form.is_valid())
        self.assertIn('lives_in_area', form.errors)
        draft = DynamicCRFForm({}, version=version, final=False)
        self.assertTrue(draft.is_valid())

    def test_range_validation(self):
        version = FormDefinition.objects.get(code='vital-signs').published_version
        form = DynamicCRFForm({'sbp': '400', 'dbp': '80'}, version=version)
        self.assertFalse(form.is_valid())
        self.assertIn('sbp', form.errors)

    def test_disease_specific_forms_follow_applicability(self):
        p = self.make_patient()
        codes = {v.definition.code for v in applicable_versions(p)}
        self.assertIn('vital-signs', codes)
        self.assertNotIn('diabetes-assessment', codes)
        self.assertNotIn('cv-risk', codes)
        Screening.objects.create(patient=p, disease=self.diabetes, facility=self.ilala, screened_by=self.admin)
        codes = {v.definition.code for v in applicable_versions(p)}
        self.assertIn('diabetes-assessment', codes)
        self.assertIn('cv-risk', codes)
        self.assertNotIn('cardiac-assessment', codes)
        self.assertNotIn('scd-assessment', codes)

    def test_location_and_stage_rules(self):
        p = self.make_patient()
        home = {v.definition.code for v in applicable_versions(p, stage='follow_up', location='home')}
        self.assertIn('vital-signs', home)
        self.assertNotIn('general-prescreening', home)

    def test_submission_via_view_records_version_and_encounter(self):
        p = self.make_patient()
        encounter = Encounter.objects.create(patient=p, facility=self.ilala, staff=self.admin)
        version = FormDefinition.objects.get(code='vital-signs').published_version
        self.client.force_login(self.clinician_ilala)
        response = self.client.post(
            reverse('crf:submission_add', args=[p.pk, version.pk]) + f'?encounter={encounter.pk}',
            {'sbp': '130', 'dbp': '85', 'encounter': encounter.pk, 'action': 'submit'})
        self.assertEqual(response.status_code, 302)
        submission = FormSubmission.objects.get(patient=p)
        self.assertEqual((submission.version, submission.encounter, submission.status), (version, encounter, 'submitted'))
        self.assertEqual(submission.data['sbp'], 130)


class SchedulingTests(BaseTestCase):
    def test_generation_marks_history_expected_and_does_not_duplicate(self):
        p = self.make_patient()
        today = timezone.localdate()
        plan = FollowUpPlan.objects.create(patient=p, facility=self.ilala, interval_months=1,
                                           anchor_date=today - datetime.timedelta(days=95), visit_count=3)
        created = generate_visits(plan, self.admin)
        self.assertEqual(Appointment.objects.filter(plan=plan, status='expected').count(), 3)
        self.assertEqual(Appointment.objects.filter(plan=plan, status='scheduled').count(), 3)
        self.assertEqual(created, 6)
        self.assertEqual(generate_visits(plan, self.admin), 0)
        self.assertFalse(Appointment.objects.filter(plan=plan, status='missed').exists())

    def test_revision_preserves_past_and_attended_visits(self):
        p = self.make_patient()
        today = timezone.localdate()
        plan = FollowUpPlan.objects.create(patient=p, facility=self.ilala, interval_months=1,
                                           anchor_date=today - datetime.timedelta(days=40), visit_count=4)
        generate_visits(plan, self.admin)
        past = list(Appointment.objects.filter(plan=plan, scheduled_date__lt=today).values_list('pk', flat=True))
        attended = Appointment.objects.filter(plan=plan, scheduled_date__gte=today).first()
        Encounter.objects.create(patient=p, facility=self.ilala, appointment=attended, staff=self.admin)
        attended.status = 'attended'
        attended.save()
        plan.interval_months = 3
        plan.save()
        revise_plan(plan, self.admin, 'Stable patient')
        self.assertEqual(plan.version, 2)
        for pk in past:
            self.assertNotEqual(Appointment.objects.get(pk=pk).status, 'cancelled')
        attended.refresh_from_db()
        self.assertEqual(attended.status, 'attended')
        new = Appointment.objects.filter(plan=plan, plan_version=2)
        self.assertTrue(new.exists())
        self.assertTrue(all(a.scheduled_date >= today for a in new))

    def test_encounter_from_appointment_marks_attended(self):
        p = self.make_patient()
        appointment = Appointment.objects.create(patient=p, facility=self.ilala, scheduled_date=timezone.localdate())
        self.client.force_login(self.clinician_ilala)
        response = self.client.post(reverse('encounters:encounter_add', args=[p.pk]), {
            'facility': self.ilala.pk, 'appointment': appointment.pk,
            'encounter_date': timezone.localtime().strftime('%Y-%m-%dT%H:%M'), 'stage': 'follow_up',
            'location_type': 'facility', 'status': 'open'})
        self.assertEqual(response.status_code, 302, getattr(response, 'context', {}) and response.context['form'].errors)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, 'attended')


class ReportingTests(BaseTestCase):
    def test_last_sunday(self):
        self.assertEqual(last_sunday(2026, 10), datetime.date(2026, 10, 25))
        self.assertEqual(last_sunday(2026, 12), datetime.date(2026, 12, 27))
        self.assertEqual(last_sunday(2027, 2), datetime.date(2027, 2, 28))
        self.assertEqual(last_sunday(2024, 2), datetime.date(2024, 2, 25))
        for month in range(1, 13):
            day = last_sunday(2027, month)
            self.assertEqual(day.weekday(), 6)
            self.assertNotEqual((day + datetime.timedelta(days=7)).month, month)

    def test_monthly_report_respects_scope_and_active_status(self):
        coordinator = self.make_user('fc', 'Facility Coordinator', facility=self.ilala)
        inactive = self.make_user('fc2', 'Facility Coordinator', facility=self.mwanza)
        inactive.is_active = False
        inactive.save()
        p_ilala = self.make_patient(self.ilala)
        p_mwanza = self.make_patient(self.mwanza)
        programme = ClinicalProgramme.objects.get(code='dm-care')
        ProgrammeEnrollment.objects.create(patient=p_ilala, programme=programme, facility=self.ilala)
        ProgrammeEnrollment.objects.create(patient=p_mwanza, programme=programme, facility=self.mwanza)
        call_command('send_monthly_enrollment_reports', '--force', stdout=open('/dev/null', 'w'))
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(coordinator.email, recipients)
        self.assertNotIn(inactive.email, recipients)
        body = next(m.body for m in mail.outbox if m.to[0] == coordinator.email)
        self.assertIn('New clinical programme enrollments: 1 ', body)
        self.assertIn('Demo Ilala Health Centre', body)
        self.assertNotIn('Nyamagana', body)
        self.assertTrue(ReportRun.objects.filter(recipient=coordinator, status='sent').exists())

    def test_export_is_audited_and_scoped(self):
        self.make_patient(self.ilala)
        other = self.make_patient(self.mwanza)
        manager = self.make_user('dm', 'Data Manager', facility=self.ilala)
        self.client.force_login(manager)
        response = self.client.get(reverse('reporting:export_patients'))
        content = response.content.decode()
        self.assertNotIn(other.patient_number, content)
        self.assertTrue(AuditEvent.objects.filter(action='export', actor=manager).exists())


class StaffOnboardingTests(BaseTestCase):
    def test_activation_email_has_link_not_password_and_link_is_single_use(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('users:create'), {
            'username': 'newnurse', 'first_name': 'New', 'last_name': 'Nurse', 'email': 'nurse@example.org',
            'groups': [Group.objects.get(name='Nurse / Triage Officer').pk]})
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='newnurse')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn('/accounts/reset/', body)
        self.assertNotIn('password:', body.lower())
        link = re.search(r'https?://[^/]+(/accounts/reset/\S+)', body).group(1)
        self.client.logout()
        response = self.client.get(link, follow=True)
        set_url = response.redirect_chain[-1][0]
        response = self.client.post(set_url, {'new_password1': 'Very-secure-987', 'new_password2': 'Very-secure-987'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.login(username='newnurse', password='Very-secure-987'))
        self.client.logout()
        response = self.client.get(link, follow=True)
        self.assertFalse(response.context['validlink'])

    def test_deactivation_is_audited(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('users:toggle_active', args=[self.clinician_ilala.pk]))
        self.clinician_ilala.refresh_from_db()
        self.assertFalse(self.clinician_ilala.is_active)
        self.assertTrue(AuditEvent.objects.filter(action='account', object_id=str(self.clinician_ilala.pk)).exists())


class AuditAndSoftDeleteTests(BaseTestCase):
    def test_update_is_audited_with_changes(self):
        p = self.make_patient()
        self.client.force_login(self.admin)
        data = {'first_name': 'Asha', 'last_name': 'Mushi-Kweka', 'sex': 'female', 'date_of_birth': '1980-05-01',
                'phone': '0712000000'}
        self.client.post(reverse('patients:update', args=[p.pk]), data)
        event = AuditEvent.objects.filter(action='update', object_id=str(p.pk)).latest('timestamp')
        self.assertEqual(event.changes['last_name'], ['Mushi', 'Mushi-Kweka'])

    def test_audit_events_cannot_be_modified(self):
        event = AuditEvent.objects.create(action='create')
        with self.assertRaises(ValueError):
            event.save()

    def test_soft_delete_and_restore(self):
        p = self.make_patient()
        self.client.force_login(self.admin)
        self.client.post(reverse('patients:delete', args=[p.pk]), {'reason': 'Duplicate'})
        self.assertFalse(Patient.objects.filter(pk=p.pk).exists())
        self.assertTrue(Patient.all_objects.filter(pk=p.pk).exists())
        self.client.post(reverse('patients:restore', args=[p.pk]))
        self.assertTrue(Patient.objects.filter(pk=p.pk).exists())

    def test_duplicate_warning_requires_confirmation(self):
        self.make_patient()
        self.client.force_login(self.admin)
        data = {'first_name': 'Asha', 'last_name': 'Mushi', 'sex': 'female', 'date_of_birth': '1980-05-01',
                'registration_facility': self.ilala.pk, 'registration_date': '2026-10-16'}
        response = self.client.post(reverse('patients:create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Possible duplicates')
        response = self.client.post(reverse('patients:create'), {**data, 'confirm_not_duplicate': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Patient.objects.count(), 2)


class PageSmokeTests(BaseTestCase):
    """Every page renders for an administrator and for a facility clinician."""

    def setUp(self):
        self.patient = self.make_patient()
        p = self.patient
        today = timezone.localdate()
        self.encounter = Encounter.objects.create(patient=p, facility=self.ilala, staff=self.admin)
        self.appointment = Appointment.objects.create(patient=p, facility=self.ilala, scheduled_date=today)
        self.study = Study.objects.create(code='s1', title='Study', status='active')
        self.plan = FollowUpPlan.objects.create(patient=p, facility=self.ilala, anchor_date=today)
        from laboratory.models import InvestigationOrder, LabOrder, LabTest
        self.lab = LabOrder.objects.create(patient=p, facility=self.ilala, test=LabTest.objects.first(),
                                           ordered_by=self.admin)
        self.inv = InvestigationOrder.objects.create(patient=p, facility=self.ilala, modality='ecg',
                                                     description='Palpitations', requested_by=self.admin)
        self.version = FormDefinition.objects.get(code='vital-signs').published_version
        self.submission = FormSubmission.objects.create(version=self.version, patient=p, data={'sbp': 120},
                                                        status='submitted')
        self.transfer = PatientTransfer.objects.create(patient=p, from_facility=self.ilala,
                                                       to_facility=self.kinondoni, reason='Moved')
        self.draft = clone_version(self.version, self.admin)

    def urls(self):
        p, e = self.patient.pk, self.encounter.pk
        return [
            reverse('dashboard'), reverse('patients:list'), reverse('patients:create'), reverse('patients:detail', args=[p]),
            reverse('patients:update', args=[p]), reverse('patients:delete', args=[p]), reverse('patients:deleted'),
            reverse('patients:record_death', args=[p]), reverse('patients:identifier_add', args=[p]),
            reverse('patients:transfer_add', args=[p]), reverse('patients:transfers'),
            reverse('patients:transfer_action', args=[self.transfer.pk, 'approve']),
            reverse('clinical:screening_list'), reverse('clinical:enrollment_list'),
            reverse('clinical:prescreening_add', args=[p]), reverse('clinical:screening_add', args=[p]),
            reverse('clinical:diagnosis_add', args=[p]), reverse('clinical:enrollment_add', args=[p]),
            reverse('research:study_list'), reverse('research:study_create'),
            reverse('research:study_detail', args=[self.study.pk]), reverse('research:study_update', args=[self.study.pk]),
            reverse('research:eligibility_add', args=[p]), reverse('research:consent_add', args=[p]),
            reverse('research:enrollment_add', args=[p]) + f'?study={self.study.pk}',
            reverse('encounters:appointment_list'), reverse('encounters:appointment_list') + '?view=reconcile',
            reverse('encounters:appointment_add', args=[p]),
            reverse('encounters:appointment_update', args=[self.appointment.pk]),
            reverse('encounters:encounter_list'), reverse('encounters:encounter_add', args=[p]) + f'?appointment={self.appointment.pk}',
            reverse('encounters:encounter_detail', args=[e]), reverse('encounters:encounter_update', args=[e]),
            reverse('encounters:vitals_add', args=[e]), reverse('encounters:plan_add', args=[p]),
            reverse('encounters:plan_update', args=[self.plan.pk]), reverse('encounters:admission_list'),
            reverse('encounters:admission_add', args=[p]),
            reverse('crf:definition_list'), reverse('crf:definition_create'),
            reverse('crf:definition_detail', args=[self.version.definition_id]),
            reverse('crf:definition_update', args=[self.version.definition_id]),
            reverse('crf:version_detail', args=[self.version.pk]), reverse('crf:version_detail', args=[self.draft.pk]),
            reverse('crf:section_add', args=[self.draft.pk]), reverse('crf:field_add', args=[self.draft.pk]),
            reverse('crf:submission_list'), reverse('crf:submission_add', args=[p, self.version.pk]) + f'?encounter={e}',
            reverse('crf:submission_detail', args=[self.submission.pk]),
            reverse('laboratory:lab_worklist'), reverse('laboratory:lab_add', args=[p]),
            reverse('laboratory:lab_action', args=[self.lab.pk, 'collect']),
            reverse('laboratory:investigation_worklist'), reverse('laboratory:investigation_add', args=[p]),
            reverse('laboratory:investigation_report', args=[self.inv.pk]),
            reverse('reporting:report'), reverse('reporting:runs'), reverse('audit:list'),
            reverse('users:list'), reverse('users:create'), reverse('users:detail', args=[self.clinician_ilala.pk]),
            reverse('users:update', args=[self.clinician_ilala.pk]), reverse('facilities:list'),
            reverse('password_change'),
        ]

    def test_all_pages_render_for_admin(self):
        self.client.force_login(self.admin)
        for url in self.urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_clinician_pages(self):
        self.client.force_login(self.clinician_ilala)
        allowed = [reverse('dashboard'), reverse('patients:detail', args=[self.patient.pk]),
                   reverse('encounters:encounter_detail', args=[self.encounter.pk]),
                   reverse('clinical:diagnosis_add', args=[self.patient.pk])]
        for url in allowed:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        for url in (reverse('users:create'), reverse('audit:list'), reverse('crf:definition_create')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_login_and_password_reset_pages_are_public(self):
        for url in (reverse('login'), reverse('password_reset'), reverse('password_reset_done')):
            self.assertEqual(self.client.get(url).status_code, 200)

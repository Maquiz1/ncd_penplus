import datetime

from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent
from clinical.models import ClinicalProgramme, Diagnosis, ProgrammeEnrollment, Screening, ScreeningSession
from core.tests import BaseTestCase
from patients.models import Patient


class IdentityAccessTests(BaseTestCase):
    """Only data-entry and point-of-care roles see identifiable fields."""

    ALLOWED = ['Registration Officer', 'Nurse / Triage Officer', 'Clinician', 'Data Clerk', 'Data Manager',
               'Laboratory Staff', 'Pharmacist / Dispenser', 'Imaging / ECG / Echo Staff']
    DENIED = ['Facility Coordinator', 'Geographic Coordinator', 'Research Coordinator', 'PI / Co-Investigator',
              'Statistician', 'Monitor / Auditor', 'Report Viewer', 'Facility Administrator', 'System Administrator']

    def test_identity_permission_by_role(self):
        for i, role in enumerate(self.ALLOWED + self.DENIED):
            user = self.make_user(f'u{i}', role, facility=self.ilala)
            with self.subTest(role=role):
                self.assertEqual(user.has_perm('patients.view_patient_identity'), role in self.ALLOWED)

    def test_pi_and_coordinator_see_ids_not_names(self):
        p = self.make_patient(first_name='Neema', last_name='Kileo', phone='0755111222')
        for role in ('Monitor / Auditor', 'Research Coordinator'):
            user = self.make_user(role[:6].lower().replace(' ', ''), role, facility=self.ilala)
            self.client.force_login(user)
            with self.subTest(role=role):
                for url in (reverse('patients:detail', args=[p.pk]), reverse('patients:list')):
                    response = self.client.get(url)
                    self.assertContains(response, p.patient_number)
                    self.assertNotContains(response, 'Kileo')
                    self.assertNotContains(response, '0755111222')

    def test_pharmacist_sees_name_on_worklist(self):
        from pharmacy.models import Medication, Prescription
        p = self.make_patient(first_name='Neema', last_name='Kileo')
        Prescription.objects.create(patient=p, facility=self.ilala, medication=Medication.objects.first(),
                                    dose='1', frequency='OD', quantity=10, prescriber=self.admin)
        self.client.force_login(self.make_user('ph', 'Pharmacist / Dispenser', facility=self.ilala))
        self.assertContains(self.client.get(reverse('pharmacy:prescription_list')), 'Kileo')

    def test_audit_trail_masks_identity_changes(self):
        p = self.make_patient(last_name='Kileo')
        self.client.force_login(self.admin)
        self.client.post(reverse('patients:update', args=[p.pk]), {
            'first_name': 'Asha', 'last_name': 'Kileo-Mushi', 'sex': 'female', 'date_of_birth': '1980-05-01',
            'phone': '0712000000'})
        self.assertTrue(AuditEvent.objects.filter(action='update', object_id=str(p.pk)).exists())
        monitor = self.make_user('mon', 'Monitor / Auditor', level='national')
        self.client.force_login(monitor)
        response = self.client.get(reverse('audit:list'))
        self.assertContains(response, 'restricted')
        self.assertNotContains(response, 'Kileo-Mushi')
        manager = self.make_user('dmx', 'Data Manager', level='national')
        manager.groups.add(*monitor.groups.all())
        self.client.force_login(manager)
        self.assertContains(self.client.get(reverse('audit:list')), 'Kileo-Mushi')

    def test_identifier_label_hides_value(self):
        from patients.models import PatientIdentifier
        p = self.make_patient()
        identifier = PatientIdentifier.objects.create(patient=p, id_type='national', value='19800501-12345')
        self.assertNotIn('12345', str(identifier))


class ScreeningSessionTests(BaseTestCase):
    def setUp(self):
        self.nurse = self.make_user('nurse', 'Nurse / Triage Officer', facility=self.ilala)
        self.session = ScreeningSession.objects.create(name='Ilala Primary School', facility=self.ilala,
                                                       source='school', location_type='school',
                                                       place_name='Ilala Primary School')
        self.session.diseases.set([self.diabetes, self.cardiac])
        self.url = reverse('clinical:session_screen', args=[self.session.pk])
        self.client.force_login(self.nurse)

    def person(self, **overrides):
        data = {'p-first_name': 'Baraka', 'p-last_name': 'Mollel', 'p-sex': 'male', 'p-date_of_birth': '2014-03-01',
                'p-next_of_kin_name': 'Grace Mollel', 's-result_diabetes': 'negative', 's-result_cardiac': 'positive',
                's-permanent_resident': 'on', 's-consent_obtained': 'on', 's-assent_obtained': 'on',
                's-guardian_name': 'Grace Mollel', 's-referred': 'on', 's-referred_to': self.kinondoni.pk}
        data.update(overrides)
        return {k: v for k, v in data.items() if v is not None}

    def test_register_and_screen_new_person(self):
        response = self.client.post(self.url, self.person())
        self.assertEqual(response.status_code, 302)
        patient = Patient.objects.get(last_name='Mollel')
        self.assertEqual((patient.registration_facility, patient.current_facility), (self.ilala, self.ilala))
        screenings = {s.disease.code: s for s in Screening.objects.filter(patient=patient)}
        self.assertEqual(screenings['diabetes'].result, 'negative')
        self.assertEqual(screenings['cardiac'].result, 'positive')
        self.assertTrue(all(s.session == self.session and s.source == 'school' and s.consent_obtained
                            for s in screenings.values()))
        self.assertEqual(screenings['cardiac'].referred_to, self.kinondoni)
        self.assertFalse(Diagnosis.objects.filter(patient=patient).exists())

    def test_rescreen_existing_person_without_new_record(self):
        existing = self.make_patient(first_name='Baraka', last_name='Mollel', sex='male',
                                     date_of_birth=datetime.date(2010, 1, 1))
        response = self.client.get(self.url, {'q': 'Mollel'})
        self.assertContains(response, existing.patient_number)
        data = {k: v for k, v in self.person().items() if not k.startswith('p-')}
        response = self.client.post(self.url + f'?patient={existing.pk}', {**data, 'patient': existing.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Patient.objects.filter(last_name='Mollel').count(), 1)
        self.assertEqual(existing.screenings.count(), 2)

    def test_duplicate_suggested_before_new_registration(self):
        self.make_patient(first_name='Baraka', last_name='Mollel', sex='male', date_of_birth=datetime.date(2014, 3, 1))
        response = self.client.post(self.url, self.person())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'may be someone already registered')
        self.assertEqual(Patient.objects.filter(last_name='Mollel').count(), 1)

    def test_under_minimum_age_rejected(self):
        young = (timezone.localdate() - datetime.timedelta(days=365 * 4)).isoformat()
        response = self.client.post(self.url, self.person(**{'p-date_of_birth': young}))
        self.assertContains(response, 'below the minimum')
        self.assertFalse(Patient.objects.filter(last_name='Mollel').exists())

    def test_minor_needs_assent_and_guardian(self):
        response = self.client.post(self.url, self.person(**{'s-assent_obtained': None, 's-guardian_name': ''}))
        self.assertContains(response, 'Assent is required')
        self.assertContains(response, 'parent/guardian')

    def test_consent_residence_and_referral_rules(self):
        response = self.client.post(self.url, self.person(**{'s-consent_obtained': None, 's-permanent_resident': None,
                                                              's-referred': None}))
        self.assertContains(response, 'Only consenting individuals')
        self.assertContains(response, 'Non-permanent residents are excluded')
        self.assertContains(response, 'must be informed and referred')
        self.assertFalse(Screening.objects.exists())

    def test_known_patient_excluded_for_that_disease(self):
        existing = self.make_patient(first_name='Juma', last_name='Ali', sex='male')
        ProgrammeEnrollment.objects.create(patient=existing, facility=self.ilala,
                                           programme=ClinicalProgramme.objects.get(code='cardiac-care'))
        data = {k: v for k, v in self.person().items() if not k.startswith('p-')}
        response = self.client.post(self.url + f'?patient={existing.pk}', {**data, 'patient': existing.pk})
        self.assertContains(response, 'Known Cardiac disease subject')
        response = self.client.post(self.url + f'?patient={existing.pk}',
                                    {**data, 'patient': existing.pk, 's-result_cardiac': '', 's-referred': ''})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(existing.screenings.values_list('disease__code', flat=True)), ['diabetes'])

    def test_roles_without_identity_cannot_use_register_and_screen(self):
        self.client.force_login(self.make_user('pi', 'Monitor / Auditor', facility=self.ilala))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        detail = self.client.get(self.session.get_absolute_url())
        self.assertEqual(detail.status_code, 200)

    def test_closed_session_blocks_entry(self):
        self.session.is_closed = True
        self.session.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_session_pages_and_summary(self):
        self.client.post(self.url, self.person())
        for url in (reverse('clinical:session_list'), reverse('clinical:session_create'),
                    reverse('clinical:session_update', args=[self.session.pk]), self.session.get_absolute_url()):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.get(self.session.get_absolute_url())
        self.assertContains(response, 'Age 6–17')
        self.assertContains(response, 'Mollel')


class ReturnToCareTests(BaseTestCase):
    def test_return_after_ltfu_keeps_history_and_schedules(self):
        from clinical.models import EnrollmentEvent
        from encounters.models import Appointment
        patient = self.make_patient()
        enrollment = ProgrammeEnrollment.objects.create(
            patient=patient, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'),
            enrollment_date=datetime.date(2024, 1, 5), status='ltfu', outcome_date=datetime.date(2024, 6, 1),
            outcome_reason='Defaulted')
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:enrollment_return', args=[enrollment.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {'return_date': timezone.localdate().isoformat(), 'kind': 'return',
                                          'reason': 'Came back to clinic', 'create_schedule': 'on',
                                          'interval_months': 1, 'visit_count': 3})
        self.assertEqual(response.status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual((enrollment.status, enrollment.outcome_date), ('active', None))
        event = EnrollmentEvent.objects.get(enrollment=enrollment)
        self.assertEqual((event.kind, event.from_status, event.to_status), ('return', 'ltfu', 'active'))
        self.assertEqual(Appointment.objects.filter(patient=patient, status='scheduled').count(), 3)

    def test_return_not_allowed_for_active_or_deceased(self):
        patient = self.make_patient()
        enrollment = ProgrammeEnrollment.objects.create(
            patient=patient, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'))
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(reverse('clinical:enrollment_return', args=[enrollment.pk])).status_code, 403)


class LtfuRuleTests(BaseTestCase):
    def setUp(self):
        from encounters.models import Encounter
        self.patient = self.make_patient()
        self.enrollment = ProgrammeEnrollment.objects.create(
            patient=self.patient, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'),
            enrollment_date=datetime.date(2025, 1, 10))
        Encounter.objects.create(patient=self.patient, facility=self.ilala, staff=self.admin,
                                 encounter_date=timezone.make_aware(datetime.datetime(2025, 6, 15, 9)))

    def test_three_months_without_attendance_becomes_ltfu(self):
        from clinical.services import apply_ltfu_rule
        self.assertEqual(apply_ltfu_rule(self.admin, today=datetime.date(2025, 9, 15)), [])  # exactly 3 months
        changed = apply_ltfu_rule(self.admin, today=datetime.date(2025, 9, 16))
        self.assertEqual(changed, [(self.enrollment, datetime.date(2025, 9, 15))])
        self.enrollment.refresh_from_db()
        self.assertEqual((self.enrollment.status, self.enrollment.outcome_date), ('ltfu', datetime.date(2025, 9, 15)))
        self.assertEqual(self.enrollment.events.get().to_status, 'ltfu')

    def test_return_date_resets_the_clock(self):
        from clinical.models import EnrollmentEvent
        from clinical.services import apply_ltfu_rule
        EnrollmentEvent.objects.create(enrollment=self.enrollment, kind='return', from_status='ltfu',
                                       to_status='active', event_date=datetime.date(2025, 8, 1))
        self.assertEqual(apply_ltfu_rule(self.admin, today=datetime.date(2025, 10, 1)), [])

    def test_dry_run_and_command(self):
        from django.core.management import call_command
        call_command('apply_ltfu_rule', '--dry-run', stdout=open('/dev/null', 'w'))
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, 'active')
        call_command('apply_ltfu_rule', stdout=open('/dev/null', 'w'))
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, 'ltfu')  # last visit Jun 2025, today is later


class FamilyMemberTests(BaseTestCase):
    def test_nurse_records_sibling_and_patient_page_shows_it(self):
        from .models import FamilyMember
        patient = self.make_patient()
        nurse = self.make_user('nurse_f', 'Nurse / Triage Officer', facility=self.ilala)
        self.client.force_login(nurse)
        self.assertEqual(self.client.get(reverse('clinical:family_add', args=[patient.pk])).status_code, 200)
        self.client.post(reverse('clinical:family_add', args=[patient.pk]), {
            'relation': 'sibling', 'sex': 'female', 'age_years': 7, 'alive': 'true', 'scd_status': 'affected',
            'tested': 'true', 'test_type': 'Sickle SCAN', 'recorded_on': '2026-01-10'})
        member = FamilyMember.objects.get(patient=patient)
        self.assertEqual((member.scd_status, member.alive, member.age_years), ('affected', True, 7))
        self.assertContains(self.client.get(reverse('patients:detail', args=[patient.pk])), 'Has sickle cell disease')
        self.assertEqual(self.client.get(reverse('clinical:family_edit', args=[member.pk])).status_code, 200)


class ResultsOnlyRoleTests(BaseTestCase):
    """PI, coordinators, statistician and report viewers: their own home page, view only, no forms or clinic screens."""

    ROLES = ['PI / Co-Investigator', 'Facility Coordinator', 'Geographic Coordinator', 'Report Viewer', 'Statistician']

    def test_home_and_sections_are_view_only(self):
        p = self.make_patient(first_name='Neema', last_name='Kileo')
        ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'))
        allowed = [reverse(name) for name in (
            'reporting:home', 'reporting:disease', 'reporting:recruitment', 'reporting:conditions', 'reporting:trends',
            'reporting:study_progress', 'reporting:follow_up', 'reporting:report', 'reporting:data_quality',
            'indicators:quarterly', 'indicators:clinical', 'indicators:socio', 'laboratory:monitoring', 'patients:summaries')]
        allowed.append(reverse('patients:summary', args=[p.pk]))
        refused = [reverse('patients:list'), reverse('patients:detail', args=[p.pk]), reverse('crf:submission_list'),
                   reverse('crf:definition_list'), reverse('clinical:enrollment_add', args=[p.pk]),
                   reverse('investigations:request', args=[p.pk]), reverse('laboratory:gap_add', args=[p.pk])]
        for i, role in enumerate(self.ROLES):
            self.client.force_login(self.make_user(f'r{i}', role, level='national'))
            with self.subTest(role=role):
                home = self.client.get(reverse('dashboard'))
                self.assertTemplateUsed(home, 'core/results_home.html')
                self.assertNotContains(home, 'Form review')
                self.assertNotContains(home, 'Subject register')
                for url in allowed:
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200, url)
                    self.assertNotContains(response, 'Kileo')
                for url in refused:
                    self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_subject_list_shows_status_and_opens_summary(self):
        active = self.make_patient(first_name='Neema', last_name='Kileo')
        ProgrammeEnrollment.objects.create(patient=active, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'))
        lost = self.make_patient()
        ProgrammeEnrollment.objects.create(patient=lost, facility=self.ilala, status='ltfu', outcome_date=timezone.localdate(),
                                           programme=ClinicalProgramme.objects.get(code='dm-care'))
        died = self.make_patient()
        type(died).all_objects.filter(pk=died.pk).update(vital_status='deceased')
        new = self.make_patient()
        self.client.force_login(self.make_user('st', 'Statistician', level='national'))
        url = reverse('patients:summaries')
        response = self.client.get(url)
        statuses = {p.pk: p.overall_status[0] for p in response.context['object_list']}
        self.assertEqual(statuses, {active.pk: 'active', lost.pk: 'ltfu', died.pk: 'deceased', new.pk: 'not_enrolled'})
        self.assertNotContains(response, 'Kileo')
        self.assertContains(response, reverse('patients:summary', args=[active.pk]))
        for status, patient in (('ltfu', lost), ('deceased', died), ('not_enrolled', new)):
            found = [p.pk for p in self.client.get(url, {'status': status}).context['object_list']]
            self.assertEqual(found, [patient.pk], status)

    def test_only_the_statistician_gets_exports_and_dictionary(self):
        urls = [reverse('exports:list'), reverse('exports:codebook'), reverse('exports:forms')]
        for i, role in enumerate(self.ROLES):
            self.client.force_login(self.make_user(f'x{i}', role, level='national'))
            home = self.client.get(reverse('dashboard'))
            with self.subTest(role=role):
                expected = 200 if role == 'Statistician' else 403
                for url in urls:
                    self.assertEqual(self.client.get(url).status_code, expected, url)
                self.assertEqual(b'Data dictionary' in home.content, role == 'Statistician')


class PatientFlowTests(BaseTestCase):
    """Progress bar, next step, duplicate pre-screening and the stuck-in-the-flow list."""

    def states(self, patient):
        from clinical.flow import patient_flow
        track = patient_flow(patient)[0]
        return [s.state for s in track.steps], track.next.label if track.next else None

    def test_next_step_follows_the_pathway(self):
        from clinical.models import Disease, PreScreening
        from encounters.models import FollowUpPlan
        from laboratory.models import LabOrder, LabTest
        p = self.make_patient()
        self.assertEqual(self.states(p), (['done'] + ['todo'] * 6, 'Record screening'))
        PreScreening.objects.create(patient=p, facility=self.ilala)
        self.assertEqual(self.states(p)[1], 'Choose what to screen for')
        diabetes = Disease.objects.get(code='diabetes')
        screening = Screening.objects.create(patient=p, disease=diabetes, facility=self.ilala, screened_by=self.admin)
        self.assertEqual(self.states(p), (['done', 'done', 'done'] + ['todo'] * 4, 'Request investigations'))
        test = LabTest.objects.create(code='rbg-flow', name='Glucose')
        order = LabOrder.objects.create(patient=p, facility=self.ilala, test=test, ordered_by=self.admin)
        states, next_step = self.states(p)
        self.assertEqual((states[3], next_step), ('waiting', None))
        order.status = 'resulted'
        order.save()
        self.assertEqual(self.states(p)[1], 'Diagnosis')
        diagnosis = Diagnosis.objects.create(patient=p, disease=diabetes, description='T2DM', clinician=self.admin)
        self.assertEqual(self.states(p)[1], 'Confirm diagnosis')
        diagnosis.status = 'confirmed'
        diagnosis.save()
        screening.refresh_from_db()
        self.assertEqual(screening.result, 'positive')
        self.assertEqual(self.states(p)[1], 'Enroll in programme')
        enrollment = ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala,
                                                        programme=ClinicalProgramme.objects.get(code='dm-care'))
        self.assertEqual(self.states(p)[1], 'Create follow-up plan')
        FollowUpPlan.objects.create(patient=p, facility=self.ilala, programme_enrollment=enrollment,
                                    anchor_date=timezone.localdate())
        self.assertEqual(self.states(p), (['done'] * 7, None))

    def enroll(self, p, *programmes):
        return self.client.post(reverse('clinical:enrollment_add', args=[p.pk]), {
            'programmes': [ClinicalProgramme.objects.get(code=code).pk for code in programmes], 'facility': self.ilala.pk,
            'enrollment_date': timezone.localdate().isoformat()})

    def test_enrolled_in_every_disease_the_doctor_confirmed(self):
        from clinical.models import Disease, PreScreening
        p = self.make_patient()
        PreScreening.objects.create(patient=p, facility=self.ilala, known_ncd=True, consent_obtained=True)
        self.client.force_login(self.clinician_ilala)
        self.assertContains(self.client.get(reverse('clinical:enrollment_add', args=[p.pk])), 'Nothing to enroll in yet')
        self.assertContains(self.enroll(p, 'dm-care'), 'confirmed by the doctor')
        diagnosis = Diagnosis.objects.create(patient=p, disease=Disease.objects.get(code='diabetes'), description='T2DM',
                                             clinician=self.admin)
        self.enroll(p, 'dm-care')
        self.assertFalse(ProgrammeEnrollment.objects.filter(patient=p).exists())
        diagnosis.status = 'confirmed'
        diagnosis.save()
        Diagnosis.objects.create(patient=p, disease=Disease.objects.get(code='cardiac'), description='RHD',
                                 clinician=self.admin, status='confirmed')
        form = self.client.get(reverse('clinical:enrollment_add', args=[p.pk])).context['form']
        self.assertEqual(set(form.initial['programmes']), set(ClinicalProgramme.objects.filter(code__in=['dm-care', 'cardiac-care'])))
        self.assertContains(self.enroll(p, 'dm-care', 'scd-care'), 'confirmed by the doctor')
        self.enroll(p, 'dm-care', 'cardiac-care')
        self.assertEqual(ProgrammeEnrollment.objects.filter(patient=p).count(), 2)

    def make_main_study(self):
        from research.models import Study
        return Study.objects.create(code='penplus', title='PEN-Plus', status='active', current_consent_version='2.0')

    def confirmed_subject(self, consent=True):
        from clinical.models import Disease, PreScreening
        p = self.make_patient()
        PreScreening.objects.create(patient=p, facility=self.ilala, consent_obtained=consent,
                                    consent_date=timezone.localdate() if consent else None)
        for code in ('diabetes', 'cardiac'):
            Diagnosis.objects.create(patient=p, disease=Disease.objects.get(code=code), description=code, clinician=self.admin,
                                     status='confirmed')
        return p

    def test_enrolling_also_joins_the_study_using_prescreening_consent(self):
        from research.models import ConsentRecord, StudyEnrollment
        study = self.make_main_study()
        p = self.confirmed_subject()
        self.client.force_login(self.clinician_ilala)
        self.assertContains(self.client.get(reverse('clinical:enrollment_add', args=[p.pk])), 'also enrolls')
        self.enroll(p, 'dm-care', 'cardiac-care')
        self.assertEqual(ProgrammeEnrollment.objects.filter(patient=p).count(), 2)
        enrollment = StudyEnrollment.objects.get(patient=p, study=study)
        self.assertEqual((enrollment.participant_id, enrollment.status, enrollment.consent.consent_version),
                         (p.patient_number, 'active', '2.0'))
        self.assertEqual(ConsentRecord.objects.filter(patient=p).count(), 1)

    def test_no_research_consent_no_enrollment(self):
        self.make_main_study()
        p = self.confirmed_subject(consent=False)
        self.client.force_login(self.clinician_ilala)
        self.assertContains(self.client.get(reverse('clinical:enrollment_add', args=[p.pk])), 'Cannot enroll yet')
        self.assertContains(self.enroll(p, 'dm-care'), 'needs research consent')
        self.assertFalse(ProgrammeEnrollment.objects.filter(patient=p).exists())

    def test_closing_one_disease_keeps_the_study_until_the_last(self):
        from research.models import StudyEnrollment
        self.make_main_study()
        p = self.confirmed_subject()
        self.client.force_login(self.clinician_ilala)
        self.enroll(p, 'dm-care', 'cardiac-care')
        self.client.force_login(self.admin)
        def close(code):
            e = ProgrammeEnrollment.objects.get(patient=p, programme__code=code)
            return self.client.post(reverse('clinical:enrollment_update', args=[e.pk]), {
                'programme': e.programme_id, 'facility': self.ilala.pk, 'status': 'completed',
                'enrollment_date': timezone.localdate().isoformat(), 'outcome_date': timezone.localdate().isoformat()})
        close('dm-care')
        self.assertEqual(StudyEnrollment.objects.get(patient=p).status, 'active')
        close('cardiac-care')
        self.assertEqual(StudyEnrollment.objects.get(patient=p).status, 'completed')

    def test_known_ncd_screening_is_optional(self):
        from clinical.flow import patient_flow
        from clinical.models import PreScreening
        p = self.make_patient()
        PreScreening.objects.create(patient=p, facility=self.ilala, known_ncd=True)
        track = patient_flow(p)[0]
        self.assertEqual((track.steps[2].state, track.next.label), ('skipped', 'Request investigations'))
        self.assertEqual({a.label for a in track.also}, {'Diagnosis', 'Screening (optional)'})

    def test_screening_only_says_what_to_screen_for(self):
        from clinical.models import Disease
        p = self.make_patient()
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:screening_add', args=[p.pk])
        self.assertNotIn('result', self.client.get(url).context['form'].fields)
        chosen = Disease.objects.filter(code__in=['diabetes', 'scd'])
        self.client.post(url, {'diseases': [d.pk for d in chosen], 'facility': self.ilala.pk,
                               'screening_date': timezone.localdate().isoformat()})
        self.assertEqual(set(Screening.objects.filter(patient=p).values_list('result', flat=True)), {'pending'})
        self.assertEqual(Screening.objects.filter(patient=p).count(), 2)

    def test_known_ncd_names_the_diseases_and_focuses_screening(self):
        from clinical.flow import patient_flow
        from clinical.models import Disease, PreScreening
        p = self.make_patient()
        diabetes, scd = Disease.objects.get(code='diabetes'), Disease.objects.get(code='scd')
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:prescreening_add', args=[p.pk])
        data = {'facility': self.ilala.pk, 'date': timezone.localdate().isoformat(), 'screening_type': 'facility',
                'consent_obtained': 'True', 'consent_date': timezone.localdate().isoformat(), 'resides_in_area': 'True',
                'known_ncd': 'True', 'disposition': 'proceed'}
        self.assertContains(self.client.post(url, data), 'Tick the known NCD')
        self.client.post(url, {**data, 'known_diseases': [diabetes.pk, scd.pk]})
        self.assertEqual(set(PreScreening.objects.get(patient=p).known_diseases.all()), {diabetes, scd})
        self.assertEqual({t.disease for t in patient_flow(p)}, {diabetes, scd})
        form = self.client.get(reverse('clinical:screening_add', args=[p.pk])).context['form']
        self.assertEqual(set(form.initial['diseases']), {diabetes.pk, scd.pk})

    def test_rescreening_keeps_the_earlier_record_and_reopens_the_path(self):
        from clinical.flow import patient_flow
        from clinical.models import Disease, PreScreening
        p = self.make_patient()
        PreScreening.objects.create(patient=p, facility=self.ilala)
        diabetes = Disease.objects.get(code='diabetes')
        first = Screening.objects.create(patient=p, disease=diabetes, facility=self.ilala, screened_by=self.admin,
                                         result='negative', screening_date=timezone.localdate() - datetime.timedelta(days=300))
        self.assertIsNone(patient_flow(p)[0].next)
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:screening_rescreen', args=[first.pk])
        self.assertContains(self.client.get(reverse('patients:detail', args=[p.pk])), 'Re-screen')
        self.assertContains(self.client.post(url, {'screening_date': timezone.localdate().isoformat()}), 'This field is required')
        self.client.post(url, {'screening_date': timezone.localdate().isoformat(), 'reason': 'New symptoms'})
        first.refresh_from_db()
        second = Screening.objects.filter(patient=p).first()
        self.assertEqual((first.result, second.rescreen_of, second.result), ('negative', first, 'pending'))
        self.assertEqual(patient_flow(p)[0].next.label, 'Request investigations')
        self.assertContains(self.client.get(reverse('patients:detail', args=[p.pk])), 'Re-screening')
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_positive_screening_cannot_be_rescreened(self):
        from clinical.models import Disease
        p = self.make_patient()
        positive = Screening.objects.create(patient=p, disease=Disease.objects.get(code='diabetes'), facility=self.ilala,
                                            screened_by=self.admin, result='positive')
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(reverse('clinical:screening_rescreen', args=[positive.pk])).status_code, 403)

    def test_duplicate_screening_can_be_deleted_with_a_reason(self):
        from clinical.models import Disease
        p = self.make_patient()
        duplicate = Screening.objects.create(patient=p, disease=Disease.objects.get(code='diabetes'), facility=self.ilala,
                                             screened_by=self.admin)
        url = reverse('clinical:screening_delete', args=[duplicate.pk])
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(url, {'reason': ''})
        self.assertTrue(Screening.objects.filter(pk=duplicate.pk).exists())
        self.client.post(url, {'reason': 'Entered twice'})
        self.assertFalse(Screening.objects.filter(pk=duplicate.pk).exists())
        self.assertTrue(Screening.all_objects.filter(pk=duplicate.pk).exists())

    def test_everything_up_to_enrollment_is_one_baseline_visit(self):
        from clinical.models import Disease, PreScreening
        from encounters.models import Encounter
        from investigations.models import InvestigationOrder, InvestigationType
        from django.core.management import call_command
        call_command('setup_investigations', stdout=open('/dev/null', 'w'))
        call_command('setup_diseases', stdout=open('/dev/null', 'w'))
        p = self.make_patient()
        diabetes = Disease.objects.get(code='diabetes')
        self.client.force_login(self.clinician_ilala)
        today = timezone.localdate().isoformat()
        response = self.client.post(reverse('clinical:prescreening_add', args=[p.pk]), {
            'facility': self.ilala.pk, 'date': today, 'screening_type': 'facility', 'consent_obtained': 'True',
            'consent_date': today, 'resides_in_area': 'True', 'known_ncd': 'False', 'disposition': 'proceed',
            'lab_test_requested': 'True', 'screen_for': [diabetes.pk]})
        self.assertRedirects(response, reverse('investigations:request', args=[p.pk]), fetch_redirect_response=False)
        visit = Encounter.objects.get(patient=p)
        self.assertEqual((visit.stage, visit.status), ('baseline', 'open'))
        self.client.post(reverse('investigations:request', args=[p.pk]), {
            'types': [InvestigationType.objects.get(code='echo').pk], 'facility': self.ilala.pk})
        condition = diabetes.conditions.filter(parent=None).first()
        self.client.post(reverse('clinical:diagnosis_add', args=[p.pk]), {
            'conditions': [condition.pk], f'status_{diabetes.pk}': 'confirmed', 'diagnosis_date': today})
        self.assertEqual(Encounter.objects.filter(patient=p).count(), 1)
        self.assertEqual(PreScreening.objects.get(patient=p).encounter, visit)
        self.assertEqual(Screening.objects.get(patient=p).encounter, visit)
        self.assertEqual(InvestigationOrder.objects.get(patient=p).encounter, visit)
        self.assertEqual(Diagnosis.objects.get(patient=p).encounter, visit)
        self.enroll(p, 'dm-care')
        visit.refresh_from_db()
        self.assertEqual(visit.status, 'completed')
        self.assertEqual(visit.programme_enrollment.programme.code, 'dm-care')

    def test_no_disease_closes_as_screening_only_and_community_opens_no_visit(self):
        from clinical.models import Disease
        from encounters.models import Encounter
        p = self.make_patient()
        self.client.force_login(self.clinician_ilala)
        today = timezone.localdate().isoformat()
        self.client.post(reverse('clinical:prescreening_add', args=[p.pk]), {
            'facility': self.ilala.pk, 'date': today, 'screening_type': 'community', 'consent_obtained': 'True',
            'consent_date': today, 'resides_in_area': 'True', 'known_ncd': 'False', 'disposition': 'proceed'})
        self.assertFalse(Encounter.objects.filter(patient=p).exists())
        self.client.post(reverse('clinical:screening_add', args=[p.pk]), {
            'diseases': [Disease.objects.get(code='diabetes').pk], 'facility': self.ilala.pk, 'screening_date': today})
        screening = Screening.objects.get(patient=p)
        self.client.post(reverse('clinical:screening_not_diagnosed', args=[screening.pk]))
        visit = Encounter.objects.get(patient=p)
        self.assertEqual((visit.stage, visit.status), ('screening', 'completed'))

    def test_subject_page_has_baseline_follow_up_and_unscheduled_panels(self):
        from encounters.models import Encounter
        p = self.make_patient()
        self.client.force_login(self.clinician_ilala)
        response = self.client.get(reverse('patients:detail', args=[p.pk]))
        self.assertEqual(response.context['pathway']['active'], 'baseline')
        for text in ('Baseline', 'Follow-up', 'Unscheduled', 'Diagnosis', 'start after enrollment'):
            self.assertContains(response, text)
        ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'))
        visit = Encounter.objects.create(patient=p, facility=self.ilala, stage='follow_up', staff=self.admin)
        response = self.client.get(reverse('patients:detail', args=[p.pk]))
        self.assertEqual(response.context['pathway']['active'], 'follow_up')
        self.assertContains(response, f'?encounter={visit.pk}')
        self.assertContains(response, 'class="row g-3 mt-0 d-none" data-baseline-only', count=1)
        page = self.client.get(visit.get_absolute_url())
        self.assertNotContains(page, reverse('clinical:screening_add', args=[p.pk]))
        self.assertNotContains(page, reverse('clinical:diagnosis_add', args=[p.pk]))

    def test_follow_up_panel_lists_visits_and_the_visit_holds_the_steps(self):
        from encounters.models import Encounter
        p = self.make_patient()
        ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'))
        visit = Encounter.objects.create(patient=p, facility=self.ilala, stage='unscheduled', staff=self.admin)
        self.client.force_login(self.clinician_ilala)
        page = self.client.get(reverse('patients:detail', args=[p.pk]))
        self.assertIn(visit, [r['visit'] for r in page.context['pathway']['unscheduled_visits']])
        self.assertNotContains(page, '?stage=unscheduled"><i class="bi bi-plus"></i> Unscheduled visit')
        visit_page = self.client.get(visit.get_absolute_url())
        self.assertEqual([s.label for s in visit_page.context['steps']],
                         ['Forms', 'Investigations', 'Prescribe', 'Admission', 'Close visit'])
        self.assertContains(visit_page, 'Everything done from here belongs to this visit.')
        self.assertEqual({s.label: s.note for s in visit_page.context['steps']}['Prescribe'], 'Optional')
        self.assertNotContains(visit_page, 'Screening &amp; diagnoses')
        self.assertContains(visit_page, 'Investigations at this visit')
        self.assertContains(visit_page, 'Medicines prescribed at this visit')
        form = self.client.get(reverse('pharmacy:prescription_add', args=[p.pk]) + f'?encounter={visit.pk}').context['form']
        self.assertEqual([str(c[0]) for c in form.fields['encounter'].choices], [str(visit.pk)])
        self.assertContains(page, 'class="d-none" data-baseline-only')
        self.assertContains(visit_page, reverse('pharmacy:prescription_add', args=[p.pk]) + f'?encounter={visit.pk}')
        # Today's open visit replaces the Start button; one left open on an earlier day does not.
        self.assertNotContains(page, 'Start a new unscheduled visit')
        Encounter.objects.filter(pk=visit.pk).update(encounter_date=timezone.now() - datetime.timedelta(days=2))
        page = self.client.get(reverse('patients:detail', args=[p.pk]))
        self.assertContains(page, 'Start a new unscheduled visit')
        self.assertContains(page, 'is still open: close it once its work is done')

    def test_admission_is_a_step_inside_the_visit(self):
        from encounters.models import Admission, Encounter
        p = self.make_patient()
        visit = Encounter.objects.create(patient=p, facility=self.ilala, stage='follow_up', staff=self.admin)
        self.client.force_login(self.admin)
        steps = {s.label: s for s in self.client.get(visit.get_absolute_url()).context['steps']}
        self.assertEqual(steps['Admission'].url, reverse('encounters:admission_add', args=[p.pk]) + f'?encounter={visit.pk}')
        response = self.client.post(steps['Admission'].url, {'facility': self.ilala.pk, 'encounter': visit.pk, 'ward': 'Medical',
                                                              'admitted_at': timezone.now().strftime('%Y-%m-%dT%H:%M'), 'reason': 'DKA'})
        self.assertRedirects(response, visit.get_absolute_url(), fetch_redirect_response=False)
        stay = Admission.objects.get(patient=p)
        page = self.client.get(visit.get_absolute_url())
        self.assertEqual({s.label: s.url for s in page.context['steps']}['Discharge'],
                         reverse('encounters:admission_discharge', args=[stay.pk]))
        self.assertContains(page, 'Still admitted')
        self.assertNotContains(self.client.get(reverse('patients:detail', args=[p.pk])), 'data-bs-target="#path-admission"')
        # Admitted again: only after discharge, and a stay ending within 30 days is offered as the earlier one.
        again = {'facility': self.ilala.pk, 'ward': 'Medical', 'admitted_at': timezone.now().strftime('%Y-%m-%dT%H:%M'),
                 'reason': 'DKA again'}
        self.assertContains(self.client.post(steps['Admission'].url, again), 'already admitted')
        stay.discharged_at, stay.discharge_outcome = timezone.now() - datetime.timedelta(days=10), 'improved'
        stay.save()
        form = self.client.get(steps['Admission'].url).context['form']
        self.assertEqual(form.initial['readmission_of'], stay.pk)
        self.client.post(steps['Admission'].url, {**again, 'readmission_of': stay.pk})
        self.assertEqual(Admission.objects.filter(patient=p).latest('pk').readmission_of, stay)
        # Died in hospital: the discharge asks for the date and cause, and records the death on the subject.
        second = Admission.objects.filter(patient=p).latest('pk')
        url = reverse('encounters:admission_discharge', args=[second.pk])
        died = {'discharged_at': timezone.now().strftime('%Y-%m-%dT%H:%M'), 'discharge_outcome': 'died'}
        self.assertContains(self.client.post(url, died), 'Give the date of death.')
        self.client.post(url, {**died, 'date_of_death': timezone.localdate().isoformat(), 'cause_of_death': 'DKA'})
        p.refresh_from_db()
        self.assertEqual((p.vital_status, p.date_of_death, p.cause_of_death), ('deceased', timezone.localdate(), 'DKA'))

    def test_subject_page_has_five_tabs_and_baseline_cards_on_overview(self):
        import re
        from clinical.models import Disease
        self.make_main_study()
        p = self.confirmed_subject()
        self.client.force_login(self.clinician_ilala)
        self.enroll(p, 'dm-care')
        page = self.client.get(reverse('patients:detail', args=[p.pk])).content.decode()
        tabs = re.findall(r'data-bs-toggle="tab" data-bs-target="#\w+" type="button">([^<]+)', page)
        self.assertEqual(tabs, ['Overview', 'Screening', 'Visits', 'Investigations', 'Medications', 'Admission', 'Diagnosis',
                                'Forms ', 'Follow-up plan'])
        Screening.objects.create(patient=p, disease=Disease.objects.get(code='scd'), facility=self.ilala, screened_by=self.admin)
        self.assertContains(self.client.get(reverse('patients:detail', args=[p.pk])), 'SCD family history')
        from encounters.models import Encounter
        Encounter.objects.create(patient=p, facility=self.ilala, stage='follow_up', staff=self.admin)
        visits = self.client.get(reverse('patients:detail', args=[p.pk])).context['baseline_visits']
        self.assertEqual({v.stage for v in visits} - {'baseline', 'screening'}, set())
        for anchor in ('id="screening"', 'id="clinical"', 'id="research"', 'id="transfers"'):
            self.assertIn(anchor, page)
        enrollment = p.programme_enrollments.get()
        self.assertIn(reverse('clinical:enrollment_update', args=[enrollment.pk]), page)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('patients:detail', args=[p.pk])), 'Withdraw / leave the study')

    def test_forms_tab_splits_baseline_and_follow_up_forms(self):
        from crf.models import FormDefinition, FormField, FormSection, FormVersion
        from crf.services import publish_version
        from encounters.models import Encounter
        p = self.make_patient()
        for code, stages in (('demo-x', ['baseline']), ('symptoms-x', ['baseline', 'follow_up']), ('hosp-x', ['follow_up'])):
            definition = FormDefinition.objects.create(code=code, name=code, applicability='universal', stages=stages,
                                                       is_active=True)
            version = FormVersion.objects.create(definition=definition, number=1)
            FormField.objects.create(section=FormSection.objects.create(version=version, title='A'), key='a', label='A')
            publish_version(version, None)
        ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'))
        visit = Encounter.objects.create(patient=p, facility=self.ilala, stage='follow_up', staff=self.admin)
        self.client.force_login(self.clinician_ilala)
        sets = dict((key, rows) for key, _visit, rows in self.client.get(reverse('patients:detail', args=[p.pk])).context['form_sets'])
        names = {key: {r['name'] for r in rows} & {'demo-x', 'symptoms-x', 'hosp-x'} for key, rows in sets.items()}
        self.assertEqual(names, {'baseline': {'demo-x', 'symptoms-x'}, 'visits': {'symptoms-x', 'hosp-x'}})
        vitals, *forms = sets['visits']
        self.assertEqual((vitals['name'], vitals['url'], vitals['done']),
                         ('Vital signs', reverse('encounters:vitals_add', args=[visit.pk]), False))
        self.assertTrue(all(r['url'].endswith(f'?encounter={visit.pk}') for r in forms))
        self.assertEqual(sets['baseline'][0]['name'], 'Vital signs')

    def test_vitals_count_as_a_visit_form(self):
        from clinical.flow import visit_steps
        from encounters.models import Encounter, Vitals
        p = self.make_patient()
        visit = Encounter.objects.create(patient=p, facility=self.ilala, stage='unscheduled', staff=self.admin)
        forms = lambda: next(s for s in visit_steps(visit) if s.label == 'Forms')
        self.assertNotIn('Vitals', [s.label for s in visit_steps(visit)])
        missing = lambda: int(forms().note.split()[0]) if forms().note else 0
        before = missing()
        self.assertFalse(forms().done)
        Vitals.objects.create(encounter=visit, weight_kg=70)
        self.assertEqual(missing(), before - 1)

    def test_every_step_link_opens(self):
        from clinical.flow import patient_pathway, visit_steps
        from encounters.models import Encounter
        p = self.make_patient()
        visit = Encounter.objects.create(patient=p, facility=self.ilala, stage='baseline', staff=self.admin)
        self.client.force_login(self.admin)
        pathway = patient_pathway(p)
        urls = [s.url for s in [*pathway['baseline_steps'], *visit_steps(visit)]]
        urls += [pathway['start_follow_up_url'], pathway['start_unscheduled_url']]
        for url in urls:
            self.assertIn(self.client.get(url).status_code, (200, 302), url)

    def test_late_subject_attends_the_appointment_they_missed(self):
        from clinical.flow import patient_pathway
        from encounters.models import Appointment
        p = self.make_patient()
        ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'))
        today = timezone.localdate()
        Appointment.objects.create(patient=p, facility=self.ilala, scheduled_date=today + datetime.timedelta(days=60))
        missed = Appointment.objects.create(patient=p, facility=self.ilala, scheduled_date=today - datetime.timedelta(days=5))
        pathway = patient_pathway(p)
        self.assertEqual((pathway['appointment'], pathway['appointment_overdue']), (missed, True))

    def test_enrolling_again_reopens_the_study_record(self):
        from research.models import StudyEnrollment
        self.make_main_study()
        p = self.confirmed_subject()
        self.client.force_login(self.clinician_ilala)
        self.enroll(p, 'dm-care')
        record = StudyEnrollment.objects.get(patient=p)
        StudyEnrollment.objects.filter(pk=record.pk).update(status='completed', outcome_date=timezone.localdate())
        self.assertEqual(self.enroll(p, 'cardiac-care').status_code, 302)
        record.refresh_from_db()
        self.assertEqual((record.status, StudyEnrollment.objects.filter(patient=p).count()), ('active', 1))

    def test_follow_up_schedule_defaults_to_the_study(self):
        from research.models import StudyEnrollment
        self.make_main_study()
        p = self.confirmed_subject()
        self.client.force_login(self.clinician_ilala)
        self.enroll(p, 'dm-care')
        record = StudyEnrollment.objects.get(patient=p)
        form = self.client.get(reverse('encounters:plan_add', args=[p.pk])).context['form']
        self.assertNotIn('programme_enrollment', form.fields)
        self.assertEqual(list(form.fields['study_enrollment'].queryset), [record])
        self.assertEqual((form.initial['study_enrollment'], form.initial['anchor_date']),
                         (record.pk, record.enrollment_date))
        self.assertEqual(form.initial['visit_count'], 1)
        self.assertEqual([c[0] for c in form.fields['visit_count'].widget.choices], list(range(13)))
        response = self.client.post(reverse('encounters:plan_add', args=[p.pk]), {
            'facility': self.ilala.pk, 'study_enrollment': record.pk, 'interval_months': 1, 'anchor_type': 'enrollment',
            'anchor_date': record.enrollment_date.isoformat(), 'visit_count': 0, 'generation': 'automatic'})
        self.assertContains(response, 'Choose how many future visits to book')

    def test_one_active_schedule_and_deleting_unused_appointments(self):
        from encounters.models import Appointment, Encounter, FollowUpPlan
        from encounters.services import generate_visits
        from records.services import blockers
        p = self.make_patient()
        today = timezone.localdate()
        dm = ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala,
                                                programme=ClinicalProgramme.objects.get(code='dm-care'))
        old = FollowUpPlan.objects.create(patient=p, facility=self.ilala, interval_months=3, anchor_date=today, visit_count=2)
        generate_visits(old, self.admin)
        self.client.force_login(self.admin)
        self.client.post(reverse('encounters:plan_add', args=[p.pk]), {
            'programmes': [dm.pk], 'facility': self.ilala.pk, 'interval_months': 1, 'anchor_type': 'custom',
            'anchor_date': today.isoformat(), 'visit_count': 2, 'generation': 'automatic'})
        old.refresh_from_db()
        self.assertEqual((old.is_active, FollowUpPlan.objects.filter(patient=p, is_active=True).count()), (False, 1))
        self.assertFalse(Appointment.objects.filter(plan=old).exclude(status='cancelled').exists())
        self.assertEqual(generate_visits(old, self.admin), 0)
        unused = Appointment.objects.filter(plan=old).first()
        self.assertEqual(blockers(unused), [])
        attended = Appointment.objects.filter(patient=p, is_deleted=False).exclude(status='cancelled').first()
        Encounter.objects.create(patient=p, facility=self.ilala, appointment=attended, staff=self.admin)
        self.assertTrue(blockers(attended))

    def test_one_schedule_per_programme(self):
        from encounters.models import Appointment, FollowUpPlan
        from issues.detectors import visits_same_month
        from issues.models import DataIssue
        p = self.make_patient()
        today = timezone.localdate()
        dm, cardiac = (ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala,
                                                          programme=ClinicalProgramme.objects.get(code=code))
                       for code in ('dm-care', 'cardiac-care'))
        self.client.force_login(self.admin)
        url = reverse('encounters:plan_add', args=[p.pk])
        form = self.client.get(url).context['form']
        self.assertEqual(set(form.initial['programmes']), {dm.pk, cardiac.pk})       # both ticked to start with

        def plan(programmes, months):
            self.client.post(url, {'programmes': [e.pk for e in programmes], 'facility': self.ilala.pk,
                                   'interval_months': months, 'anchor_type': 'custom', 'anchor_date': today.isoformat(),
                                   'visit_count': 2, 'generation': 'automatic'})
            return FollowUpPlan.objects.filter(patient=p).latest('pk')

        both = plan([dm, cardiac], 1)
        cardiac_plan = plan([cardiac], 3)                  # cardiac moves to its own schedule
        both.refresh_from_db()
        self.assertTrue(both.is_active)
        self.assertEqual(list(both.programmes.all()), [dm])
        self.assertEqual(Appointment.objects.filter(plan=cardiac_plan).first().programme_enrollment, cardiac)
        plan([dm], 2)                                      # a new diabetes schedule replaces the old one
        both.refresh_from_db()
        cardiac_plan.refresh_from_db()
        self.assertEqual((both.is_active, cardiac_plan.is_active), (False, True))
        # A diabetes and a cardiac clinic visit in the same month are both expected.
        from encounters.models import Encounter
        for a in Appointment.objects.filter(patient=p, status='scheduled').order_by('scheduled_date')[:1]:
            Encounter.objects.create(patient=p, facility=self.ilala, appointment=a, stage='follow_up', staff=self.admin)
        cardiac_visit = Appointment.objects.filter(plan=cardiac_plan, status='scheduled').first()
        Encounter.objects.create(patient=p, facility=self.ilala, appointment=cardiac_visit, stage='follow_up',
                                 staff=self.admin)
        visits_same_month([p.pk])
        self.assertFalse(DataIssue.objects.filter(code='visits_same_month', patient=p).exists())

    def test_confirmed_diagnosis_hides_awaiting_doctor(self):
        from clinical.flow import patient_flow
        from clinical.models import Disease
        p = self.make_patient()
        diabetes = Disease.objects.get(code='diabetes')
        Diagnosis.objects.create(patient=p, disease=diabetes, description='T2DM', clinician=self.admin, status='confirmed')
        Screening.objects.create(patient=p, disease=diabetes, facility=self.ilala, screened_by=self.admin)
        self.assertEqual(patient_flow(p)[0].steps[2].note, '')

    def test_other_condition_needs_to_be_specified(self):
        from diseases.models import Condition
        p = self.make_patient()
        main = Condition.objects.get_or_create(code='cardiomyopathy', defaults={'disease': self.cardiac,
                                                                                'name': 'Cardiomyopathy'})[0]
        other = Condition.objects.get_or_create(code='cardiomyopathy-other', defaults={
            'disease': self.cardiac, 'parent': main, 'name': 'Other'})[0]
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:diagnosis_add', args=[p.pk])
        data = {'conditions': [other.pk], f'status_{other.disease_id}': 'provisional',
                'diagnosis_date': timezone.localdate().isoformat()}
        response = self.client.get(url)
        self.assertContains(response, f'data-key="{other.pk}" data-name="{other.label}" data-disease="{other.disease.name}" data-other="1"')
        response = self.client.post(url, data)
        self.assertContains(response, 'Say what &quot;Cardiomyopathy – Other&quot; is')
        self.assertFalse(Diagnosis.objects.filter(patient=p).exists())
        self.client.post(url, {**data, f'other_{other.pk}': 'Takotsubo'})
        self.assertEqual(Diagnosis.objects.get(patient=p).description, 'Cardiomyopathy – Other: Takotsubo')

    def test_doctor_marks_not_diagnosed(self):
        from clinical.flow import patient_flow
        from clinical.models import Disease
        p = self.make_patient()
        screening = Screening.objects.create(patient=p, disease=Disease.objects.get(code='diabetes'), facility=self.ilala,
                                             screened_by=self.admin)
        url = reverse('clinical:screening_not_diagnosed', args=[screening.pk])
        self.client.force_login(self.make_user('nursex', 'Nurse / Triage Officer', facility=self.ilala))
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.clinician_ilala)
        self.client.post(url, {'comment': 'HbA1c normal'})
        screening.refresh_from_db()
        self.assertEqual(screening.result, 'negative')
        self.assertIn('Not diagnosed', patient_flow(p)[0].message)

    def test_existing_enrollment_can_still_be_updated(self):
        p = self.make_patient()
        enrollment = ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala,
                                                        programme=ClinicalProgramme.objects.get(code='dm-care'))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('clinical:enrollment_update', args=[enrollment.pk]), {
            'programme': enrollment.programme_id, 'facility': self.ilala.pk, 'status': 'active',
            'enrollment_date': timezone.localdate().isoformat(), 'responsible_team': 'Team A'}).status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.responsible_team, 'Team A')

    def test_negative_screening_ends_the_pathway(self):
        from clinical.flow import patient_flow
        from clinical.models import Disease
        p = self.make_patient()
        Screening.objects.create(patient=p, disease=Disease.objects.get(code='diabetes'), facility=self.ilala,
                                 screened_by=self.admin, result='negative')
        track = patient_flow(p)[0]
        self.assertIsNone(track.next)
        self.assertIn('Not diagnosed', track.message)

    def test_patient_page_shows_next_step(self):
        p = self.make_patient()
        self.client.force_login(self.clinician_ilala)
        response = self.client.get(reverse('patients:detail', args=[p.pk]))
        self.assertContains(response, 'Next step: Record screening')

    def test_prescreening_done_once(self):
        from clinical.models import PreScreening
        p = self.make_patient()
        first = PreScreening.objects.create(patient=p, facility=self.ilala,
                                            date=timezone.localdate() - datetime.timedelta(days=30))
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:prescreening_add', args=[p.pk])
        self.assertRedirects(self.client.get(url), reverse('clinical:prescreening_update', args=[first.pk]))
        response = self.client.post(url, {'facility': self.ilala.pk, 'date': timezone.localdate().isoformat(),
                                          'resides_in_area': 'on', 'disposition': 'proceed'})
        self.assertContains(response, 'done once per subject')
        self.assertEqual(PreScreening.objects.filter(patient=p).count(), 1)

    def test_screening_form_screens_for_diseases_when_lab_requested(self):
        from clinical.models import Disease, PreScreening
        p = self.make_patient()
        diabetes, cardiac = Disease.objects.get(code='diabetes'), Disease.objects.get(code='cardiac')
        self.client.force_login(self.clinician_ilala)
        today = timezone.localdate().isoformat()
        known = {'facility': self.ilala.pk, 'date': today, 'screening_type': 'facility', 'consent_obtained': 'True',
                 'consent_date': today, 'resides_in_area': 'True', 'known_ncd': 'True', 'known_diseases': [diabetes.pk],
                 'disposition': 'proceed'}
        url = reverse('clinical:prescreening_add', args=[p.pk])
        # A known NCD still asks what to screen for once a lab test is requested.
        response = self.client.post(url, {**known, 'lab_test_requested': 'True'})
        self.assertContains(response, 'Tick what the subject is screened for')
        # With no lab test, nothing is screened, even if diseases were ticked before "No" was chosen.
        self.client.post(url, {**known, 'lab_test_requested': 'False', 'screen_for': [diabetes.pk]})
        self.assertFalse(Screening.objects.filter(patient=p).exists())
        # Editing the form later adds screening for more diseases; diseases already screened are not offered.
        pre = PreScreening.objects.get(patient=p)
        update = reverse('clinical:prescreening_update', args=[pre.pk])
        self.client.post(update, {**known, 'lab_test_requested': 'True', 'screen_for': [diabetes.pk, cardiac.pk]})
        self.assertEqual(set(Screening.objects.filter(patient=p).values_list('disease__code', flat=True)),
                         {'diabetes', 'cardiac'})
        self.assertNotIn(diabetes, self.client.get(update).context['form'].fields['screen_for'].queryset)

    def test_no_investigations_requested_goes_to_doctor_confirmation(self):
        from clinical.flow import investigations, patient_flow
        from clinical.models import Disease
        p = self.make_patient()
        diabetes = Disease.objects.get(code='diabetes')
        self.client.force_login(self.clinician_ilala)
        today = timezone.localdate().isoformat()
        response = self.client.post(reverse('clinical:prescreening_add', args=[p.pk]), {
            'facility': self.ilala.pk, 'date': today, 'screening_type': 'facility', 'consent_obtained': 'True',
            'consent_date': today, 'resides_in_area': 'True', 'known_ncd': 'True', 'known_diseases': [diabetes.pk],
            'lab_test_requested': 'False', 'disposition': 'proceed'})
        self.assertRedirects(response, reverse('clinical:diagnosis_add', args=[p.pk]), fetch_redirect_response=False)
        step = investigations(p, '')
        self.assertEqual((step.disabled, step.note), (True, 'Not requested'))
        track = patient_flow(p)[0]
        self.assertEqual((track.steps[3].state, track.next.label), ('skipped', 'Diagnosis'))
        self.assertContains(self.client.get(reverse('patients:detail', args=[p.pk])), 'aria-disabled="true" title="No investigations')

    def test_baseline_prescribe_comes_after_doctor_confirmation(self):
        from clinical.flow import patient_pathway
        from encounters.models import Encounter
        from pharmacy.models import Medication, Prescription
        p = self.make_patient()
        visit = Encounter.objects.create(patient=p, facility=self.ilala, stage='baseline', staff=self.admin)
        steps = lambda: {s.label: s for s in patient_pathway(p)['baseline_steps']}
        labels = list(steps())
        self.assertEqual(labels[labels.index('Diagnosis') + 1:], ['Prescribe', 'Admission', 'Enroll'])
        self.assertEqual(steps()['Admission'].note, 'Optional')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('patients:detail', args=[p.pk])),
                            'It closes by itself when the subject is enrolled.')
        self.assertEqual((steps()['Prescribe'].done, steps()['Prescribe'].note), (False, 'Optional'))
        self.assertTrue(steps()['Prescribe'].url.endswith(f'?encounter={visit.pk}'))
        Prescription.objects.create(patient=p, encounter=visit, facility=self.ilala, prescriber=self.admin,
                                    medication=Medication.objects.first(), dose='500 mg', frequency='BD', quantity=60)
        self.assertTrue(steps()['Prescribe'].done)

    def test_disease_screening_done_once_per_disease(self):
        from clinical.models import Disease
        p = self.make_patient()
        diabetes = Disease.objects.get(code='diabetes')
        Screening.objects.create(patient=p, disease=diabetes, facility=self.ilala, screened_by=self.admin)
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:screening_add', args=[p.pk])
        offered = self.client.get(url).context['form'].fields['diseases'].queryset
        self.assertNotIn(diabetes, offered)
        self.assertGreater(offered.count(), 0)
        for disease in offered:
            Screening.objects.create(patient=p, disease=disease, facility=self.ilala, screened_by=self.admin)
        self.assertRedirects(self.client.get(url), reverse('patients:detail', args=[p.pk]) + '#screening',
                             fetch_redirect_response=False)

    def test_flow_gaps_list(self):
        from clinical.models import Disease
        positive = self.make_patient()
        Screening.objects.create(patient=positive, disease=Disease.objects.get(code='diabetes'), facility=self.ilala,
                                 screened_by=self.admin, result='positive')
        enrolled = self.make_patient()
        ProgrammeEnrollment.objects.create(patient=enrolled, facility=self.ilala,
                                           programme=ClinicalProgramme.objects.get(code='dm-care'))
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:flow_gaps')
        response = self.client.get(url, {'gap': 'positive_no_diagnosis'})
        self.assertContains(response, positive.patient_number)
        self.assertNotContains(response, enrolled.patient_number)
        response = self.client.get(url, {'gap': 'enrolled_no_baseline'})
        self.assertContains(response, enrolled.patient_number)


class SubjectSummaryTests(BaseTestCase):
    """Summaries are for investigators and coordinators: subject IDs, never names."""

    def test_pi_sees_summary_without_identity(self):
        from encounters.models import Encounter, Vitals
        from patients.models import PatientIdentifier
        from patients.models import Patient
        p = self.make_patient(first_name='Neema', last_name='Kileo', phone='0755111222')
        Patient.all_objects.filter(pk=p.pk).update(old_id=7)
        PatientIdentifier.objects.create(patient=p, id_type='edcs_study', value='1-007')
        ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala,
                                           programme=ClinicalProgramme.objects.get(code='dm-care'))
        visit = Encounter.objects.create(patient=p, facility=self.ilala, stage='baseline', staff=self.admin,
                                         chief_complaint='Thirst')
        Vitals.objects.create(encounter=visit, weight_kg=51, systolic_bp=150, diastolic_bp=95)
        self.client.force_login(self.make_user('pi', 'PI / Co-Investigator', level='national'))
        response = self.client.get(reverse('patients:summaries'), {'q': '1-007'})
        self.assertContains(response, p.patient_number)
        response = self.client.get(reverse('patients:summary', args=[p.pk]))
        self.assertContains(response, p.patient_number)
        self.assertContains(response, 'Old ID 1-007')
        self.assertContains(response, 'Diabetes Clinic')
        self.assertContains(response, '150/95')
        self.assertNotContains(response, 'Kileo')
        self.assertNotContains(response, '0755111222')

    def test_summary_search_ignores_names(self):
        p = self.make_patient(first_name='Neema', last_name='Kileo')
        self.client.force_login(self.clinician_ilala)
        response = self.client.get(reverse('patients:summaries'), {'q': 'Kileo'})
        self.assertNotContains(response, p.patient_number)

    def test_summary_out_of_scope_is_hidden(self):
        p = self.make_patient(self.mwanza)
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(reverse('patients:summary', args=[p.pk])).status_code, 404)

    def test_prescreening_consent_rules(self):
        from clinical.models import Disease, PreScreening
        p = self.make_patient()
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:prescreening_add', args=[p.pk])
        base = {'facility': self.ilala.pk, 'date': timezone.localdate().isoformat(), 'screening_type': 'facility',
                'resides_in_area': 'True', 'disposition': 'proceed'}
        self.assertContains(self.client.post(url, {**base, 'consent_obtained': 'True'}), 'Enter the date of consent')
        response = self.client.post(url, {**base, 'consent_obtained': 'True', 'consent_date': base['date'],
                                          'known_ncd': 'False', 'lab_test_requested': 'True',
                                          'screen_for': [Disease.objects.get(code='diabetes').pk]})
        self.assertEqual(response.status_code, 302)
        record = PreScreening.objects.get(patient=p)
        self.assertEqual((record.consent_obtained, record.known_ncd, record.lab_request_date),
                         (True, False, timezone.localdate()))
        self.assertEqual(record.disposition, 'proceed')
        # Without consent the outcome becomes "does not proceed", whatever was chosen; a referral is kept.
        for chosen, expected in (('proceed', 'not_now'), ('', 'not_now'), ('referred', 'referred')):
            other = self.make_patient()
            self.client.post(reverse('clinical:prescreening_add', args=[other.pk]),
                             {**base, 'consent_obtained': 'False', 'disposition': chosen})
            self.assertEqual(PreScreening.objects.get(patient=other).disposition, expected)

    def test_report_viewer_sees_summaries_not_register(self):
        p = self.make_patient(first_name='Neema', last_name='Kileo')
        viewer = self.make_user('me_officer', 'Report Viewer', facility=self.ilala)
        self.client.force_login(viewer)
        response = self.client.get(reverse('patients:summary', args=[p.pk]))
        self.assertContains(response, p.patient_number)
        self.assertNotContains(response, 'Kileo')
        self.assertNotContains(response, 'Full record')
        self.assertEqual(self.client.get(reverse('patients:list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('patients:detail', args=[p.pk])).status_code, 403)

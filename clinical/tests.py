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
              'Monitor / Auditor', 'Report Viewer', 'Facility Administrator', 'System Administrator']

    def test_identity_permission_by_role(self):
        for i, role in enumerate(self.ALLOWED + self.DENIED):
            user = self.make_user(f'u{i}', role, facility=self.ilala)
            with self.subTest(role=role):
                self.assertEqual(user.has_perm('patients.view_patient_identity'), role in self.ALLOWED)

    def test_pi_and_coordinator_see_ids_not_names(self):
        p = self.make_patient(first_name='Neema', last_name='Kileo', phone='0755111222')
        for role in ('PI / Co-Investigator', 'Research Coordinator', 'Facility Coordinator'):
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
        self.client.force_login(self.make_user('pi', 'PI / Co-Investigator', facility=self.ilala))
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


class PatientFlowTests(BaseTestCase):
    """Progress bar, next step, duplicate pre-screening and the stuck-in-the-flow list."""

    def states(self, patient):
        from clinical.flow import patient_flow
        track = patient_flow(patient)[0]
        return [s.state for s in track.steps], track.next.label if track.next else None

    def test_next_step_follows_the_pathway(self):
        from clinical.models import Disease, PreScreening
        from encounters.models import Encounter, FollowUpPlan
        p = self.make_patient()
        self.assertEqual(self.states(p), (['done'] + ['todo'] * 6, 'Record pre-screening'))
        PreScreening.objects.create(patient=p, facility=self.ilala)
        self.assertEqual(self.states(p)[1], 'Record disease screening')
        diabetes = Disease.objects.get(code='diabetes')
        screening = Screening.objects.create(patient=p, disease=diabetes, facility=self.ilala, screened_by=self.admin)
        self.assertEqual(self.states(p), (['done', 'done', 'waiting'] + ['todo'] * 4, 'Record screening result'))
        screening.result = 'positive'
        screening.save()
        self.assertEqual(self.states(p)[1], 'Add diagnosis')
        Diagnosis.objects.create(patient=p, disease=diabetes, description='T2DM', clinician=self.admin)
        self.assertEqual(self.states(p)[1], 'Enroll in programme')
        enrollment = ProgrammeEnrollment.objects.create(patient=p, facility=self.ilala,
                                                        programme=ClinicalProgramme.objects.get(code='dm-care'))
        self.assertEqual(self.states(p)[1], 'Record baseline visit')
        Encounter.objects.create(patient=p, facility=self.ilala, stage='baseline', staff=self.admin)
        self.assertEqual(self.states(p)[1], 'Create follow-up plan')
        FollowUpPlan.objects.create(patient=p, facility=self.ilala, programme_enrollment=enrollment,
                                    anchor_date=timezone.localdate())
        self.assertEqual(self.states(p), (['done', 'done', 'done', 'waiting', 'done', 'done', 'done'], None))

    def test_negative_screening_ends_the_pathway(self):
        from clinical.flow import patient_flow
        from clinical.models import Disease
        p = self.make_patient()
        Screening.objects.create(patient=p, disease=Disease.objects.get(code='diabetes'), facility=self.ilala,
                                 screened_by=self.admin, result='negative')
        track = patient_flow(p)[0]
        self.assertIsNone(track.next)
        self.assertIn('negative', track.message)

    def test_patient_page_shows_next_step(self):
        p = self.make_patient()
        self.client.force_login(self.clinician_ilala)
        response = self.client.get(reverse('patients:detail', args=[p.pk]))
        self.assertContains(response, 'Next step: Record pre-screening')

    def test_second_prescreening_same_day_refused(self):
        from clinical.models import PreScreening
        p = self.make_patient()
        first = PreScreening.objects.create(patient=p, facility=self.ilala)
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:prescreening_add', args=[p.pk])
        self.assertRedirects(self.client.get(url), reverse('clinical:prescreening_update', args=[first.pk]))
        response = self.client.post(url, {'facility': self.ilala.pk, 'date': timezone.localdate().isoformat(),
                                          'resides_in_area': 'on', 'disposition': 'proceed'})
        self.assertContains(response, 'already has a pre-screening')
        self.assertEqual(PreScreening.objects.filter(patient=p).count(), 1)

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
        self.assertContains(response, 'Diabetes care programme')
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
        from clinical.models import PreScreening
        p = self.make_patient()
        self.client.force_login(self.clinician_ilala)
        url = reverse('clinical:prescreening_add', args=[p.pk])
        base = {'facility': self.ilala.pk, 'date': timezone.localdate().isoformat(), 'screening_type': 'facility',
                'resides_in_area': 'True', 'disposition': 'proceed'}
        self.assertContains(self.client.post(url, {**base, 'consent_obtained': 'True'}), 'Enter the date of consent')
        self.assertContains(self.client.post(url, {**base, 'consent_obtained': 'False'}),
                            'Without consent the person cannot proceed')
        response = self.client.post(url, {**base, 'consent_obtained': 'True', 'consent_date': base['date'],
                                          'known_ncd': 'False', 'lab_test_requested': 'True'})
        self.assertEqual(response.status_code, 302)
        record = PreScreening.objects.get(patient=p)
        self.assertEqual((record.consent_obtained, record.known_ncd, record.lab_request_date),
                         (True, False, timezone.localdate()))

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

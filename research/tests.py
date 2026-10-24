from django.core.exceptions import ValidationError

from core.tests import BaseTestCase

from .models import ConsentRecord, Study, StudyEligibilityAssessment, StudyEnrollment


class OpenEnrollmentRuleTests(BaseTestCase):
    def test_one_open_enrollment_per_study_without_database_constraint(self):
        patient = self.make_patient()
        study = Study.objects.create(code='s', title='S', status='active')
        eligibility = StudyEligibilityAssessment.objects.create(patient=patient, study=study, is_eligible=True,
                                                                assessed_by=self.admin)
        consent = ConsentRecord.objects.create(patient=patient, study=study, consent_version='1',
                                               decision='consented', obtained_by=self.admin)
        StudyEnrollment.objects.create(patient=patient, study=study, participant_id='A', facility=self.ilala,
                                       eligibility=eligibility, consent=consent)
        second = StudyEnrollment(patient=patient, study=study, participant_id='B', facility=self.ilala,
                                 eligibility=eligibility, consent=consent)
        with self.assertRaises(ValidationError):
            second.full_clean()


class ResearchFollowsCareTests(BaseTestCase):
    def setUp(self):
        import datetime
        from clinical.models import ClinicalProgramme, ProgrammeEnrollment
        self.patient = self.make_patient()
        self.study = Study.objects.create(code='pp', title='PEN-Plus', status='active')
        eligibility = StudyEligibilityAssessment.objects.create(patient=self.patient, study=self.study,
                                                                is_eligible=True, assessed_by=self.admin)
        self.consent = ConsentRecord.objects.create(patient=self.patient, study=self.study, consent_version='1',
                                                    decision='consented', obtained_by=self.admin)
        self.study_enrollment = StudyEnrollment.objects.create(
            patient=self.patient, study=self.study, participant_id='1-001', facility=self.ilala,
            eligibility=eligibility, consent=self.consent, enrollment_date=datetime.date(2025, 1, 10))
        self.programme = ProgrammeEnrollment.objects.create(
            patient=self.patient, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'),
            enrollment_date=datetime.date(2025, 1, 10))

    def test_ltfu_rule_closes_research_too(self):
        import datetime
        from clinical.services import apply_ltfu_rule
        apply_ltfu_rule(self.admin, today=datetime.date(2025, 6, 1))
        self.study_enrollment.refresh_from_db()
        self.assertEqual((self.study_enrollment.status, self.study_enrollment.outcome_date),
                         ('ltfu', datetime.date(2025, 4, 10)))

    def test_return_to_care_reopens_research(self):
        from django.urls import reverse
        from django.utils import timezone
        self.programme.status, self.programme.outcome_date = 'ltfu', timezone.localdate()
        self.programme.save()
        self.study_enrollment.status, self.study_enrollment.outcome_date = 'ltfu', timezone.localdate()
        self.study_enrollment.save()
        self.client.force_login(self.clinician_ilala)
        self.client.post(reverse('clinical:enrollment_return', args=[self.programme.pk]),
                         {'return_date': timezone.localdate().isoformat(), 'kind': 'return', 'interval_months': 1,
                          'visit_count': 1})
        self.study_enrollment.refresh_from_db()
        self.assertEqual((self.study_enrollment.status, self.study_enrollment.outcome_date), ('active', None))

    def test_withdrawn_consent_is_not_reopened(self):
        import datetime
        from django.urls import reverse
        from django.utils import timezone
        ConsentRecord.objects.create(patient=self.patient, study=self.study, consent_version='1', decision='withdrawn',
                                     withdrawal_date=datetime.date(2025, 3, 1), obtained_by=self.admin,
                                     consent_date=timezone.now())
        self.programme.status, self.programme.outcome_date = 'ltfu', timezone.localdate()
        self.programme.save()
        self.study_enrollment.status, self.study_enrollment.outcome_date = 'withdrawn', timezone.localdate()
        self.study_enrollment.save()
        self.client.force_login(self.clinician_ilala)
        response = self.client.post(reverse('clinical:enrollment_return', args=[self.programme.pk]),
                                    {'return_date': timezone.localdate().isoformat(), 'kind': 'return',
                                     'interval_months': 1, 'visit_count': 1}, follow=True)
        self.assertContains(response, 'consent was withdrawn')
        self.study_enrollment.refresh_from_db()
        self.assertEqual(self.study_enrollment.status, 'withdrawn')

    def test_staff_status_change_mirrors(self):
        import datetime
        from django.urls import reverse
        self.client.force_login(self.clinician_ilala)
        self.client.post(reverse('clinical:enrollment_update', args=[self.programme.pk]), {
            'programme': self.programme.programme_id, 'facility': self.ilala.pk, 'enrollment_date': '2025-01-10',
            'status': 'transferred_out', 'outcome_date': '2025-05-02', 'outcome_reason': 'Moved to Arusha'})
        self.study_enrollment.refresh_from_db()
        self.assertEqual((self.study_enrollment.status, self.study_enrollment.outcome_date),
                         ('transferred', datetime.date(2025, 5, 2)))

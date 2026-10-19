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

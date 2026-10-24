import datetime

from django.urls import reverse
from django.utils import timezone

from core.tests import BaseTestCase
from encounters.models import Appointment, Encounter

from .detectors import run_all
from .models import DataIssue


class DataIssueTests(BaseTestCase):
    def setUp(self):
        self.patient = self.make_patient()
        when = timezone.make_aware(datetime.datetime(2024, 3, 4, 9))
        appt = Appointment.objects.create(patient=self.patient, facility=self.ilala, scheduled_date=datetime.date(2024, 3, 4),
                                          status='attended')
        self.scheduled = Encounter.objects.create(patient=self.patient, facility=self.ilala, appointment=appt,
                                                  encounter_date=when, staff=self.admin)
        self.second = Encounter.objects.create(patient=self.patient, facility=self.ilala, staff=self.admin,
                                               appointment=Appointment.objects.create(
                                                   patient=self.patient, facility=self.ilala, status='attended',
                                                   scheduled_date=datetime.date(2024, 3, 20)),
                                               encounter_date=when + datetime.timedelta(days=16))
        self.nurse = self.make_user('nurse_i', 'Nurse / Triage Officer', facility=self.ilala)

    def test_detector_finds_month_with_two_visits_once(self):
        run_all()
        run_all()
        issue = DataIssue.objects.get(code='visits_same_month')
        self.assertEqual((issue.patient, issue.related_date), (self.patient, datetime.date(2024, 3, 1)))

    def test_staff_classify_visit_and_resolve(self):
        run_all()
        issue = DataIssue.objects.get(code='visits_same_month')
        self.client.force_login(self.nurse)
        self.assertContains(self.client.get(reverse('issues:list')), '2 visits in March 2024')
        page = self.client.get(issue.get_absolute_url())
        self.assertContains(page, 'Unscheduled')
        self.client.post(reverse('issues:classify_visit', args=[issue.pk, self.second.pk]), {'as': 'unscheduled'})
        self.second.refresh_from_db()
        self.assertIsNone(self.second.appointment)
        self.assertEqual(Appointment.objects.get(scheduled_date=datetime.date(2024, 3, 20)).status, 'cancelled')
        self.client.post(reverse('issues:resolve', args=[issue.pk]), {'decision': 'resolved', 'note': ''})
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'open')  # a note is required
        self.client.post(reverse('issues:resolve', args=[issue.pk]),
                         {'decision': 'resolved', 'note': 'Second visit was for fever'})
        issue.refresh_from_db()
        self.assertEqual((issue.status, issue.resolved_by), ('resolved', self.nurse))
        run_all()
        self.assertEqual(DataIssue.objects.get(pk=issue.pk).status, 'resolved')  # not reopened

    def test_scope_and_roles(self):
        run_all()
        other = self.make_user('nurse_m', 'Nurse / Triage Officer', facility=self.mwanza)
        self.client.force_login(other)
        self.assertNotContains(self.client.get(reverse('issues:list')), '2 visits in March 2024')
        for role in ('Data Manager', 'Clinician', 'Laboratory Staff', 'Pharmacist / Dispenser',
                     'Imaging / ECG / Echo Staff'):
            user = self.make_user(role[:8].replace(' ', '').lower() + 'x', role, facility=self.ilala)
            with self.subTest(role=role):
                self.assertTrue(user.has_perm('issues.change_dataissue'))
        pi = self.make_user('pi_x', 'PI / Co-Investigator', facility=self.ilala)
        self.client.force_login(pi)
        self.assertEqual(self.client.get(reverse('issues:list')).status_code, 403)

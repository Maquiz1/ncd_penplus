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


class HbA1cOverdueTests(BaseTestCase):
    def test_flag_raised_then_closed_by_new_result(self):
        from clinical.models import ClinicalProgramme, ProgrammeEnrollment
        from laboratory.models import LabOrder, LabTest
        from laboratory.monitoring import monitoring_status

        from .detectors import hba1c_overdue
        today = timezone.localdate()
        patient = self.make_patient()
        ProgrammeEnrollment.objects.create(patient=patient, facility=self.ilala, enrollment_date=today - datetime.timedelta(days=200),
                                           programme=ClinicalProgramme.objects.get(code='dm-care'))
        hba1c = LabTest.objects.get_or_create(code='hba1c', defaults={'name': 'HbA1c', 'result_type': 'numeric', 'unit': '%'})[0]
        old = LabOrder(patient=patient, facility=self.ilala, test=hba1c, ordered_by=self.admin)
        old.record_result('8.1', self.admin, when=timezone.now() - datetime.timedelta(days=120))
        old.save()
        status = monitoring_status(patient)
        self.assertTrue(status['overdue'])
        self.assertEqual(status['days'], 120)
        self.assertEqual(hba1c_overdue(), 1)
        issue = DataIssue.objects.get(code='hba1c_overdue', patient=patient)
        self.assertIn('8.1', issue.description)
        self.assertEqual(hba1c_overdue(), 0)  # running again does not duplicate
        new = LabOrder(patient=patient, facility=self.ilala, test=hba1c, ordered_by=self.admin)
        new.record_result('7.0', self.admin)
        new.save()
        hba1c_overdue()
        issue.refresh_from_db()
        self.assertEqual(issue.status, DataIssue.Status.RESOLVED)
        self.assertFalse(monitoring_status(patient)['overdue'])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(patient.get_absolute_url()), 'next due')

    def test_due_list_and_not_done(self):
        from clinical.models import ClinicalProgramme, ProgrammeEnrollment
        from laboratory.models import LabOrder, LabTest, MonitoringGap
        from laboratory.monitoring import compute

        from .detectors import hba1c_overdue
        today = timezone.localdate()
        late, soon = self.make_patient(), self.make_patient()
        hba1c = LabTest.objects.get_or_create(code='hba1c', defaults={'name': 'HbA1c', 'result_type': 'numeric', 'unit': '%'})[0]
        for patient, days in ((late, 200), (soon, 60)):
            ProgrammeEnrollment.objects.create(patient=patient, facility=self.ilala, enrollment_date=today - datetime.timedelta(days=days),
                                               programme=ClinicalProgramme.objects.get(code='dm-care'))
        order = LabOrder(patient=soon, facility=self.ilala, test=hba1c, ordered_by=self.admin)
        order.record_result('7.2', self.admin, when=timezone.now() - datetime.timedelta(days=60))
        order.save()
        hba1c_overdue()
        self.client.force_login(self.admin)
        url = reverse('laboratory:monitoring')
        response = self.client.get(url)
        self.assertEqual([p for p, _ in response.context['rows']], [late])
        self.assertEqual(response.context['counts']['overdue'], 1)
        self.assertEqual(len(self.client.get(url, {'state': 'all'}).context['rows']), 2)
        csv = self.client.get(url, {'state': 'all', 'format': 'csv'}).content.decode()
        self.assertIn(late.patient_number, csv)
        self.assertNotIn(late.last_name, csv)

        response = self.client.post(reverse('laboratory:gap_add', args=[late.pk]), {'reason': 'stock_out', 'note': 'no kits'})
        self.assertEqual(response.status_code, 302)
        gap = MonitoringGap.objects.get(patient=late)
        issue = DataIssue.objects.get(code='hba1c_overdue', patient=late)
        self.assertEqual(issue.status, DataIssue.Status.RESOLVED)
        self.assertIn('out of stock', issue.resolution_note)
        rows = dict(self.client.get(url, {'state': 'all'}).context['rows'])
        self.assertEqual(rows[late]['state'], 'next_month')
        self.assertEqual(rows[late]['gap'], gap)
        self.assertEqual(self.client.get(url, {'state': 'not_done'}).context['rows'][0][0], late)
        # Still nothing a month later: overdue again, with the reason in the new issue.
        later = rows[late]['due'] + datetime.timedelta(days=1)
        self.assertEqual(compute('hba1c', today - datetime.timedelta(days=200), gap=gap, today=later)['state'], 'overdue')
        # A result for the next cycle makes the old record irrelevant.
        result = LabOrder(patient=late, facility=self.ilala, test=hba1c, ordered_by=self.admin)
        result.record_result('8.0', self.admin)
        result.save()
        status = dict(self.client.get(url, {'state': 'all'}).context['rows'])[late]
        self.assertEqual(status['state'], 'ok')
        self.assertIsNone(status['gap'])

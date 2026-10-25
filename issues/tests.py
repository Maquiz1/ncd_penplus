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
        self.assertContains(self.client.get(reverse('issues:list')), '2 follow-up visits in March 2024')
        page = self.client.get(issue.get_absolute_url())
        self.assertContains(page, 'Unscheduled')
        self.client.post(reverse('issues:resolve', args=[issue.pk]), {'decision': 'resolved', 'note': ''})
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'open')  # a note is required
        self.client.post(reverse('issues:classify_visit', args=[issue.pk, self.second.pk]), {'as': 'unscheduled'})
        self.second.refresh_from_db()
        self.assertEqual((self.second.appointment, self.second.stage), (None, 'unscheduled'))
        self.assertEqual(Appointment.objects.get(scheduled_date=datetime.date(2024, 3, 20)).status, 'cancelled')
        issue.refresh_from_db()  # one follow-up visit left: resolved by the person who classified it
        self.assertEqual((issue.status, issue.resolved_by), ('resolved', self.nurse))
        run_all()
        self.assertEqual(DataIssue.objects.get(pk=issue.pk).status, 'resolved')  # not reopened

    def test_unscheduled_and_other_visits_do_not_count(self):
        Encounter.objects.filter(pk=self.second.pk).update(stage='unscheduled', appointment=None)
        Encounter.objects.create(patient=self.patient, facility=self.ilala, staff=self.admin, stage='admission',
                                 encounter_date=self.scheduled.encounter_date + datetime.timedelta(days=2))
        run_all()
        self.assertFalse(DataIssue.objects.filter(code='visits_same_month').exists())

    def test_issue_closes_when_visit_type_changed_elsewhere(self):
        run_all()
        Encounter.objects.filter(pk=self.second.pk).update(stage='unscheduled', appointment=None)
        run_all()
        self.assertEqual(DataIssue.objects.get(code='visits_same_month').status, 'resolved')

    def test_unscheduled_visit_cannot_have_appointment(self):
        from encounters.forms import AppointmentForm, EncounterForm
        appt = Appointment.objects.create(patient=self.patient, facility=self.ilala, scheduled_date=datetime.date(2024, 4, 4))
        form = EncounterForm({'facility': self.ilala.pk, 'appointment': appt.pk, 'encounter_date': '2024-04-04T09:00',
                              'stage': 'unscheduled', 'location_type': 'facility', 'status': 'completed'},
                             patient=self.patient, user=self.admin)
        self.assertIn('appointment', form.errors)
        self.assertNotIn('unscheduled', dict(AppointmentForm(patient=self.patient, user=self.admin).fields['stage'].choices))
        self.client.force_login(self.admin)
        page = self.client.get(reverse('encounters:encounter_add', args=[self.patient.pk]) + '?stage=unscheduled')
        self.assertEqual(page.context['form'].initial['stage'], 'unscheduled')

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
        from laboratory.monitoring import compute, get_rule

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
        self.assertEqual(compute(get_rule(), today - datetime.timedelta(days=200), gap=gap, today=later)['state'], 'overdue')
        # A result for the next cycle makes the old record irrelevant.
        result = LabOrder(patient=late, facility=self.ilala, test=hba1c, ordered_by=self.admin)
        result.record_result('8.0', self.admin)
        result.save()
        status = dict(self.client.get(url, {'state': 'all'}).context['rows'])[late]
        self.assertEqual(status['state'], 'ok')
        self.assertIsNone(status['gap'])

    def test_settings_change_interval_and_switch_off(self):
        from clinical.models import ClinicalProgramme, ProgrammeEnrollment
        from laboratory.models import LabOrder, LabTest, MonitoringRule
        from laboratory.monitoring import monitoring_status

        from .detectors import hba1c_overdue
        patient = self.make_patient()
        ProgrammeEnrollment.objects.create(patient=patient, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'),
                                           enrollment_date=timezone.localdate() - datetime.timedelta(days=200))
        hba1c = LabTest.objects.get_or_create(code='hba1c', defaults={'name': 'HbA1c', 'result_type': 'numeric', 'unit': '%'})[0]
        order = LabOrder(patient=patient, facility=self.ilala, test=hba1c, ordered_by=self.admin)
        order.record_result('7.5', self.admin, when=timezone.now() - datetime.timedelta(days=120))
        order.save()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('laboratory:monitoring_rules')), 'HbA1c')
        rule = MonitoringRule.objects.get(test=hba1c)
        self.assertEqual((rule.interval_months, rule.retry_months), (3, 1))
        hba1c_overdue()
        issue = DataIssue.objects.get(code='hba1c_overdue', patient=patient)
        edit = reverse('laboratory:monitoring_rule_edit', args=[rule.pk])
        self.client.post(edit, {'interval_months': 6, 'retry_months': 2, 'is_active': 'on'})
        rule.refresh_from_db()
        self.assertEqual((rule.interval_months, rule.retry_months, rule.updated_by), (6, 2, self.admin))
        self.assertFalse(monitoring_status(patient)['overdue'])
        issue.refresh_from_db()
        self.assertEqual(issue.status, DataIssue.Status.RESOLVED)  # no longer due with 6 months
        self.client.post(edit, {'interval_months': 1, 'retry_months': 1})  # switched off
        self.assertIsNone(monitoring_status(patient))
        self.assertEqual(hba1c_overdue(), 0)
        self.assertNotContains(self.client.get(patient.get_absolute_url()), 'next due')
        self.assertEqual(self.client.post(edit, {'interval_months': 0, 'retry_months': 1}).status_code, 200)  # invalid

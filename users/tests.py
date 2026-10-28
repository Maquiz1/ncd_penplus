from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from core.tests import BaseTestCase
from audit.models import AuditEvent
from users.models import User
from clinical.models import Disease
from integrations.models import ExternalRecord


class BulkActivationTests(BaseTestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.contenttypes.models import ContentType
        User = get_user_model()
        self.migrated = User.objects.create(username='old_nurse', email='nurse@example.org')
        self.migrated.set_unusable_password()
        self.migrated.save()
        ExternalRecord.objects.create(source='penplus_edcs', table='user', source_id='7',
                                      content_type=ContentType.objects.get_for_model(User), object_id=str(self.migrated.pk))
        self.new = User.objects.create(username='new_clerk', email='clerk@example.org')
        self.new.set_unusable_password()
        self.new.save()
        self.bad = User.objects.create(username='bad_email', email='not-an-email')
        self.bad.set_unusable_password()
        self.bad.save()
        self.inactive = User.objects.create(username='gone', email='gone@example.org', is_active=False)
        self.inactive.set_unusable_password()
        self.inactive.save()

    def test_page_lists_only_pending_accounts_and_sends(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('users:bulk_activation'))
        self.assertContains(response, 'old_nurse')
        self.assertContains(response, 'new_clerk')
        self.assertNotContains(response, 'gone@example.org')
        self.assertNotContains(response, 'clin_ilala')  # already has a password
        self.client.post(reverse('users:bulk_activation'))
        recipients = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(recipients, ['clerk@example.org', 'nurse@example.org'])
        self.assertTrue(all('/accounts/reset/' in m.body for m in mail.outbox))

    def test_migrated_only_filter(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('users:bulk_activation'), {'migrated': '1'})
        self.assertContains(response, 'old_nurse')
        self.assertNotContains(response, 'new_clerk')
        self.client.post(reverse('users:bulk_activation'), {'migrated': '1'})
        self.assertEqual([m.to[0] for m in mail.outbox], ['nurse@example.org'])

    def test_command_dry_run_and_send(self):
        call_command('send_staff_activation', '--migrated', '--dry-run', stdout=open('/dev/null', 'w'))
        self.assertEqual(len(mail.outbox), 0)
        call_command('send_staff_activation', stdout=open('/dev/null', 'w'), stderr=open('/dev/null', 'w'))
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['clerk@example.org', 'nurse@example.org'])

    def test_requires_permission(self):
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(reverse('users:bulk_activation')).status_code, 403)


class AccessRuleTests(BaseTestCase):
    def test_setup_changes_need_national_level(self):
        site_dm = self.make_user('dm_site', 'Data Manager', facility=self.ilala)
        national_dm = self.make_user('dm_nat', 'Data Manager', level='national')
        self.assertTrue(site_dm.has_perm('clinical.view_disease'))
        self.assertFalse(site_dm.has_perm('clinical.change_disease'))
        self.assertFalse(site_dm.has_perm('laboratory.add_labtest'))
        self.assertTrue(site_dm.has_perm('issues.change_dataissue'))
        self.assertTrue(national_dm.has_perm('clinical.change_disease'))
        self.client.force_login(site_dm)
        self.assertEqual(self.client.get(reverse('diseases:create')).status_code, 403)

    def test_identity_override_grant_revoke_and_expiry(self):
        import datetime

        from django.utils import timezone
        monitor = self.make_user('mon', 'Monitor / Auditor', level='national')
        nurse = self.make_user('nur', 'Nurse / Triage Officer', facility=self.ilala)
        self.assertFalse(monitor.has_perm('patients.view_patient_identity'))
        self.assertTrue(nurse.has_perm('patients.view_patient_identity'))
        self.client.force_login(self.admin)
        self.client.post(reverse('users:identity_access', args=[monitor.pk]), {'identity_access': 'grant'})
        monitor.refresh_from_db()
        self.assertEqual(monitor.identity_access, 'role')          # a reason is required
        self.client.post(reverse('users:identity_access', args=[monitor.pk]),
                         {'identity_access': 'grant', 'identity_access_reason': 'Source data verification',
                          'identity_access_until': (timezone.localdate() + datetime.timedelta(days=5)).isoformat()})
        self.client.post(reverse('users:identity_access', args=[nurse.pk]),
                         {'identity_access': 'revoke', 'identity_access_reason': 'Student on attachment'})
        monitor = User.objects.get(pk=monitor.pk)
        nurse = User.objects.get(pk=nurse.pk)
        self.assertTrue(monitor.has_perm('patients.view_patient_identity'))
        self.assertFalse(nurse.has_perm('patients.view_patient_identity'))
        User.objects.filter(pk=monitor.pk).update(identity_access_until=timezone.localdate() - datetime.timedelta(days=1))
        self.assertFalse(User.objects.get(pk=monitor.pk).has_perm('patients.view_patient_identity'))
        self.assertTrue(AuditEvent.objects.filter(model='users.User', object_id=str(nurse.pk), action='account').exists())

    def test_cannot_assign_wider_level_than_own(self):
        from .forms import StaffAssignmentForm
        site_admin = self.make_user('fa', 'Facility Administrator', facility=self.ilala)
        self.assertEqual([c[0] for c in StaffAssignmentForm(user=site_admin).fields['level'].choices], ['facility'])
        district_admin = self.make_user('da', 'Facility Administrator', level='district', district=self.ilala.district)
        form = StaffAssignmentForm(user=district_admin)
        self.assertEqual([c[0] for c in form.fields['level'].choices], ['facility', 'district'])
        self.assertEqual(list(form.fields['district'].queryset), [self.ilala.district])

    def test_position_gives_default_role_and_pages_render(self):
        from django.core.management import call_command
        call_command('setup_roles', stdout=open('/dev/null', 'w'))
        from .models import Position
        nurse = Position.objects.get(code='nurse')
        self.client.force_login(self.admin)
        self.client.post(reverse('users:create'), {'username': 'pnurse', 'first_name': 'P', 'last_name': 'N',
                                                   'email': 'pn@example.org', 'position': nurse.pk})
        user = User.objects.get(username='pnurse')
        self.assertEqual(list(user.groups.values_list('name', flat=True)), ['Nurse / Triage Officer'])
        for url in (reverse('users:roles'), reverse('users:positions'), reverse('users:position_add'),
                    reverse('users:position_edit', args=[nurse.pk]), reverse('users:detail', args=[user.pk]),
                    reverse('users:list')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


class DiseaseAssignmentTests(BaseTestCase):
    def test_patients_limited_to_assigned_diseases(self):
        from clinical.models import ClinicalProgramme, ProgrammeEnrollment
        from patients.models import Patient
        diabetic = self.make_patient(first_name='Dia')
        cardiac = self.make_patient(first_name='Card', phone='0712000001')
        new = self.make_patient(first_name='New', phone='0712000002')
        ProgrammeEnrollment.objects.create(patient=diabetic, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='dm-care'))
        ProgrammeEnrollment.objects.create(patient=cardiac, facility=self.ilala, programme=ClinicalProgramme.objects.get(code='cardiac-care'))
        user = User.objects.get(pk=self.clinician_ilala.pk)
        self.assertEqual(set(Patient.objects.in_scope(user)), {diabetic, cardiac, new})
        user.diseases.add(self.diabetes)
        user = User.objects.get(pk=user.pk)
        self.assertEqual(set(Patient.objects.in_scope(user)), {diabetic, new})
        self.assertEqual(user.disease_summary, self.diabetes.name)
        self.assertEqual(User.objects.get(pk=self.admin.pk).disease_summary, 'All diseases')


    def test_reports_and_dashboard_count_only_assigned_diseases(self):
        import datetime
        from clinical.models import ClinicalProgramme, ProgrammeEnrollment
        from encounters.models import Appointment
        from reporting.services import dashboard_stats
        dm, cardiac = (ClinicalProgramme.objects.get(code=c) for c in ('dm-care', 'cardiac-care'))
        diabetic = self.make_patient(first_name='Dia')
        both = self.make_patient(first_name='Both', phone='0712000003')
        for patient, programme in ((diabetic, dm), (both, dm), (both, cardiac)):
            ProgrammeEnrollment.objects.create(patient=patient, facility=self.ilala, programme=programme)
        today = datetime.date.today()
        for patient in (diabetic, both):
            Appointment.objects.create(patient=patient, facility=self.ilala, scheduled_date=today, status='missed')
        cardiologist = self.make_user('cardio', 'Data Manager', facility=self.ilala)
        cardiologist.diseases.set([self.cardiac, Disease.objects.get(code='scd')])  # two diseases: no auto-filter
        cardiologist = User.objects.get(pk=cardiologist.pk)
        stats = dashboard_stats(cardiologist)
        self.assertEqual(stats['programme_active'], 1)  # only the cardiac enrolment of "Both"
        self.assertEqual([r['programme__disease__name'] for r in stats['programmes_by_disease']], [self.cardiac.name])
        self.client.force_login(cardiologist)
        rows = self.client.get(reverse('reporting:follow_up'), {'period': 'this_month'}).context['rows']
        self.assertEqual(sum(r['due'] for r in rows), 1)  # the diabetes-only patient is not counted
        self.client.force_login(self.admin)
        rows = self.client.get(reverse('reporting:follow_up'), {'period': 'this_month'}).context['rows']
        self.assertEqual(sum(r['due'] for r in rows), 2)


class AccountLoginAndActivationTests(BaseTestCase):
    def create(self, **data):
        self.client.force_login(self.admin)
        base = {'first_name': 'Neema', 'last_name': 'Juma'}
        return self.client.post(reverse('users:create'), {**base, **data})

    def test_login_with_username_or_email(self):
        user = self.make_user('asha', 'Nurse / Triage Officer', facility=self.ilala)
        user.email = 'Asha.Mushi@Example.org'
        user.save()
        for login in ('asha', 'asha.mushi@example.org'):
            with self.subTest(login=login):
                self.client.logout()
                response = self.client.post(reverse('login'), {'username': login, 'password': 'Str0ng-pass-123'})
                self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_email_only_makes_username_and_sends_activation(self):
        self.create(email='neema.juma@example.org')
        user = User.objects.get(email='neema.juma@example.org')
        self.assertEqual(user.username, 'neema.juma')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(mail.outbox[-1].to, ['neema.juma@example.org'])
        self.create(email='neema.juma@other.org')  # same start of address: a free username is chosen
        self.assertEqual(User.objects.get(email='neema.juma@other.org').username, 'neema.juma2')

    def test_username_only_needs_admin_password_then_own_password(self):
        self.create(username='neema')
        user = User.objects.get(username='neema')
        self.assertEqual((user.email, len(mail.outbox)), ('', 0))
        self.assertContains(self.create(), 'Give a username, an email address, or both.')
        response = self.client.post(reverse('users:set_password', args=[user.pk]),
                                    {'new_password1': 'Temp-pass-4821', 'new_password2': 'Temp-pass-4821'})
        self.assertRedirects(response, reverse('users:detail', args=[user.pk]), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.must_change_password)
        self.assertTrue(AuditEvent.objects.filter(object_id=str(user.pk), action='account').exists())
        self.client.logout()
        self.client.post(reverse('login'), {'username': 'neema', 'password': 'Temp-pass-4821'})
        self.assertRedirects(self.client.get(reverse('dashboard')), reverse('password_change'),
                             fetch_redirect_response=False)
        self.client.post(reverse('password_change'), {'old_password': 'Temp-pass-4821', 'new_password1': 'Own-pass-9137',
                                                      'new_password2': 'Own-pass-9137'})
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_forgot_password_reaches_accounts_never_activated(self):
        self.create(email='neema.juma@example.org')
        mail.outbox.clear()
        self.client.logout()
        self.client.post(reverse('password_reset'), {'email': 'NEEMA.JUMA@example.org'})
        self.assertEqual([m.to for m in mail.outbox], [['neema.juma@example.org']])

    def test_delete_unused_account_only(self):
        self.create(username='mistake')
        unused = User.objects.get(username='mistake')
        self.client.post(reverse('users:delete', args=[unused.pk]))
        self.assertFalse(User.objects.filter(pk=unused.pk).exists())
        used = self.clinician_ilala
        AuditEvent.objects.create(actor=used, action='login')
        self.client.post(reverse('users:delete', args=[used.pk]))
        self.assertTrue(User.objects.filter(pk=used.pk).exists())  # has records: deactivate instead
        self.client.post(reverse('users:toggle_active', args=[used.pk]))
        used.refresh_from_db()
        self.assertFalse(used.is_active)
        self.client.post(reverse('users:toggle_active', args=[used.pk]))
        used.refresh_from_db()
        self.assertTrue(used.is_active)
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.post(reverse('users:delete', args=[self.admin.pk])).status_code, 403)

    def test_activation_link_opened_by_logged_in_admin(self):
        import re
        self.create(email='neema.juma@example.org')
        link = re.search(r'https?://\S+/accounts/reset/\S+/', mail.outbox[-1].body).group(0)
        path = '/accounts/' + link.split('/accounts/', 1)[1]
        response = self.client.get(path, follow=True)  # admin still logged in from create()
        self.assertContains(response, 'Set your password')
        self.assertContains(response, 'You were logged out')
        self.client.post(response.redirect_chain[-1][0], {'new_password1': 'Neema-pass-7731',
                                                          'new_password2': 'Neema-pass-7731'})
        self.assertTrue(User.objects.get(email='neema.juma@example.org').check_password('Neema-pass-7731'))
        self.assertNotIn('_auth_user_id', self.client.session)  # nobody is logged in as the admin any more

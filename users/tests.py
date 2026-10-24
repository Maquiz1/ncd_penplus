from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from core.tests import BaseTestCase
from audit.models import AuditEvent
from users.models import User
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

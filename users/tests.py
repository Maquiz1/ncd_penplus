from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from core.tests import BaseTestCase
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

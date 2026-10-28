import datetime
import json
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent
from clinical.models import ClinicalProgramme, ProgrammeEnrollment
from core.tests import BaseTestCase
from encounters import clinic
from encounters.models import Appointment, ClinicSession

from .models import SmsMessage
from .services import process_queue, queue_reminders
from .sms import AfricasTalkingBackend, BeemBackend, normalize_tz_phone, sms_config


class PhoneTests(BaseTestCase):
    def test_normalize(self):
        self.assertEqual(normalize_tz_phone('0712 345 678'), '255712345678')
        self.assertEqual(normalize_tz_phone('+255 754-000111'), '255754000111')
        self.assertEqual(normalize_tz_phone('754000111'), '255754000111')
        self.assertEqual(normalize_tz_phone('022 211 0000'), '')   # landline
        self.assertEqual(normalize_tz_phone('12345'), '')


class ClinicDayTests(BaseTestCase):
    def setUp(self):
        self.manager = self.make_user('dm_c', 'Data Manager', facility=self.ilala)
        self.dm = ClinicalProgramme.objects.get(code='dm-care')
        self.cardiac = ClinicalProgramme.objects.get(code='cardiac-care')
        self.start = timezone.localdate() + datetime.timedelta(days=10)
        self.patients = []
        for i, (programme, status, phone, consent) in enumerate([
                (self.dm, 'active', '0712000001', True), (self.dm, 'ltfu', '0712000002', True),
                (self.cardiac, 'active', '0712000003', True), (self.dm, 'active', '', True),
                (self.dm, 'active', '0712000005', None)]):
            p = self.make_patient(first_name=f'P{i}', phone=phone, sms_consent=consent)
            ProgrammeEnrollment.objects.create(patient=p, programme=programme, facility=self.ilala, status=status,
                                               outcome_date=timezone.localdate() if status != 'active' else None)
            self.patients.append(p)
        self.patients[3].next_of_kin_phone = '0755000004'   # child: guardian's phone
        self.patients[3].save()

    def session(self, **kw):
        values = dict(name='Diabetes clinic', facility=self.ilala, start_date=self.start, contact_phone='0755123456')
        values.update(kw)
        session = ClinicSession.objects.create(**values)
        session.programmes.set(kw.pop('programmes', [self.dm]))
        return session

    def test_schedule_active_patients_by_disease(self):
        session = self.session()
        counts = clinic.schedule(session, self.manager)
        booked = set(session.appointments.values_list('patient__first_name', flat=True))
        self.assertEqual(booked, {'P0', 'P3', 'P4'})          # diabetes, active only
        self.assertEqual(counts['new appointment'], 3)
        self.assertEqual(clinic.schedule(session, self.manager)['already scheduled'], 3)   # idempotent

    def test_include_ltfu_and_all_programmes(self):
        session = self.session(include_ltfu=True)
        session.programmes.clear()
        clinic.schedule(session, self.manager)
        self.assertEqual(session.appointments.count(), 5)

    def test_existing_appointment_in_month_is_moved_not_duplicated(self):
        existing = Appointment.objects.create(patient=self.patients[0], facility=self.ilala,
                                              scheduled_date=self.start.replace(day=1) if self.start.day > 1 else self.start,
                                              status='scheduled')
        session = self.session()
        clinic.schedule(session, self.manager)
        existing.refresh_from_db()
        self.assertEqual((existing.scheduled_date, existing.clinic_session), (self.start, session))
        self.assertEqual(self.patients[0].appointments.count(), 1)

    def test_spread_across_days(self):
        session = self.session(end_date=self.start + datetime.timedelta(days=2), allocation='spread', skip_weekends=False)
        clinic.schedule(session, self.manager)
        self.assertEqual(len(set(session.appointments.values_list('scheduled_date', flat=True))), 3)

    def test_invitations_respect_consent_and_use_guardian_phone(self):
        session = self.session()
        clinic.schedule(session, self.manager)
        reasons, sent = clinic.send_invitations(session, self.manager)
        self.assertEqual(reasons, {'queued': 2, 'SMS consent not asked yet': 1})
        self.assertEqual(sent['sent'], 2)
        guardian = SmsMessage.objects.get(patient=self.patients[3])
        self.assertEqual((guardian.phone, guardian.recipient), ('255755000004', 'next of kin'))
        self.assertIn(self.start.strftime('%d/%m/%Y'), guardian.body)
        self.assertNotIn('Diabetes', guardian.body)                       # no diagnosis in SMS
        reasons, _ = clinic.send_invitations(session, self.manager)
        self.assertEqual(reasons.get('queued', 0), 0)                     # never twice

    def test_reminders_day_before_and_same_day_and_cancel_when_moved(self):
        session = self.session()
        clinic.schedule(session, self.manager)
        day_before = self.start - datetime.timedelta(days=1)
        self.assertEqual(queue_reminders(today=day_before), 2)
        self.assertEqual(queue_reminders(today=day_before), 0)
        appointment = Appointment.objects.get(patient=self.patients[0], clinic_session=session)
        appointment.status = 'cancelled'
        appointment.save()
        counts = process_queue()
        self.assertEqual((counts['sent'], counts['cancelled']), (1, 1))
        self.assertEqual(queue_reminders(today=self.start), 1)

    def test_cancel_and_close_attendance(self):
        session = self.session(start_date=timezone.localdate() - datetime.timedelta(days=1))
        clinic.schedule(session, self.manager)
        self.assertEqual(clinic.close_attendance(session, self.manager), 3)
        self.assertEqual(set(session.appointments.values_list('status', flat=True)), {'missed'})
        other = self.session(name='Second')
        clinic.schedule(other, self.manager)
        self.assertEqual(clinic.cancel(other, self.manager, 'Specialist unavailable'), 3)

    def test_ltfu_patient_attending_returns_to_care(self):
        p = self.patients[1]
        self.client.force_login(self.clinician_ilala)
        self.client.post(reverse('encounters:encounter_add', args=[p.pk]), {
            'facility': self.ilala.pk, 'encounter_date': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
            'stage': 'follow_up', 'location_type': 'facility', 'status': 'open'})
        enrollment = p.programme_enrollments.get()
        self.assertEqual(enrollment.status, 'active')
        self.assertEqual(enrollment.events.get().kind, 'return')

    def test_pages_and_permissions(self):
        session = self.session()
        self.client.force_login(self.manager)
        for url in (reverse('encounters:clinic_list'), reverse('encounters:clinic_create'), session.get_absolute_url(),
                    reverse('encounters:clinic_update', args=[session.pk]), reverse('notifications:log')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(session.get_absolute_url()), 'Eligible patients')
        self.client.post(reverse('encounters:clinic_action', args=[session.pk, 'schedule']))
        self.client.post(reverse('encounters:clinic_action', args=[session.pk, 'sms']))
        self.assertEqual(SmsMessage.objects.filter(status='sent').count(), 2)
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.post(reverse('encounters:clinic_action', args=[session.pk, 'sms'])).status_code, 403)


class GatewayTests(BaseTestCase):
    def fake_response(self, payload):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response

    @override_settings(BEEM_API_KEY='k', BEEM_SECRET_KEY='s', SMS_SENDER_ID='NIMR')
    def test_beem(self):
        with mock.patch('urllib.request.urlopen', return_value=self.fake_response(
                {'successful': True, 'request_id': 123})) as call:
            self.assertEqual(BeemBackend(sms_config()).send('255712000001', 'Habari'), '123')
        body = json.loads(call.call_args[0][0].data)
        self.assertEqual(body['recipients'][0]['dest_addr'], '255712000001')

    @override_settings(AFRICASTALKING_USERNAME='u', AFRICASTALKING_API_KEY='k')
    def test_africastalking_failure_is_reported(self):
        from .sms import SmsError
        with mock.patch('urllib.request.urlopen', return_value=self.fake_response(
                {'SMSMessageData': {'Recipients': [{'status': 'InvalidPhoneNumber'}]}})):
            with self.assertRaises(SmsError):
                AfricasTalkingBackend(sms_config()).send('255712000001', 'Habari')


    @override_settings(NEXTSMS_API_TOKEN='tok', SMS_SENDER_ID='NIMR')
    def test_nextsms_token_and_rejection(self):
        from .sms import NextSmsBackend, SmsError
        ok = {'messages': [{'messageId': 'm-1', 'status': {'groupName': 'PENDING'}}]}
        with mock.patch('urllib.request.urlopen', return_value=self.fake_response(ok)) as call:
            self.assertEqual(NextSmsBackend(sms_config()).send('255712000001', 'Habari'), 'm-1')
        request = call.call_args[0][0]
        self.assertEqual((request.get_header('Authorization'), json.loads(request.data)['to']),
                         ('Bearer tok', '255712000001'))
        self.assertIn('/v2/', request.full_url)
        rejected = {'messages': [{'status': {'groupName': 'REJECTED', 'name': 'REJECTED_SENDER'}}]}
        with mock.patch('urllib.request.urlopen', return_value=self.fake_response(rejected)):
            with self.assertRaises(SmsError):
                NextSmsBackend(sms_config()).send('255712000001', 'Habari')


class ConsentTests(BaseTestCase):
    def test_declined_is_never_overridden_and_not_asked_needs_opt_in(self):
        from .services import sms_eligibility
        declined = self.make_patient(phone='0712000009', sms_consent=False)
        not_asked = self.make_patient(first_name='N', phone='0712000010', sms_consent=None)
        self.assertEqual(sms_eligibility(declined, allow_without_consent=True)[2], 'declined SMS')
        self.assertEqual(sms_eligibility(not_asked)[2], 'SMS consent not asked yet')
        self.assertEqual(sms_eligibility(not_asked, allow_without_consent=True)[2], '')

    def test_one_click_consent_from_visit_pages(self):
        from audit.models import AuditEvent
        patient = self.make_patient(phone='0712000011', sms_consent=None)
        nurse = self.make_user('nurse_c', 'Nurse / Triage Officer', facility=self.ilala)
        self.client.force_login(nurse)
        page = self.client.get(reverse('patients:detail', args=[patient.pk]))
        self.assertContains(page, 'SMS consent not recorded')
        self.client.post(reverse('patients:sms_consent', args=[patient.pk]), {'answer': 'yes'})
        patient.refresh_from_db()
        self.assertTrue(patient.sms_consent)
        self.assertIsNotNone(patient.sms_consent_recorded_at)
        self.assertTrue(AuditEvent.objects.filter(object_id=str(patient.pk), reason__icontains='SMS consent').exists())
        self.assertNotContains(self.client.get(reverse('patients:detail', args=[patient.pk])), 'SMS consent not recorded')
        pi = self.make_user('pi_c', 'PI / Co-Investigator', facility=self.ilala)
        self.client.force_login(pi)
        self.assertEqual(self.client.post(reverse('patients:sms_consent', args=[patient.pk]), {'answer': 'no'}).status_code, 403)


class EmailTests(BaseTestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.staff = get_user_model().objects.create(username='newstaff', email='new@example.org')
        self.staff.set_unusable_password()
        self.staff.save()

    def test_activation_uses_active_site_configuration_and_is_logged(self):
        from django.core import mail

        from users.services import send_activation_email

        from .models import EmailLog, SiteConfiguration
        SiteConfiguration.objects.create(name='Prod', site_url='https://ncd.example.org/', from_name='Afya NCD',
                                         from_email='noreply@example.org', is_active=True)
        send_activation_email(self.staff, sent_by=self.admin)
        self.assertIn('https://ncd.example.org/accounts/reset/', mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].from_email, 'Afya NCD <noreply@example.org>')
        log = EmailLog.objects.get()
        self.assertEqual((log.status, log.purpose, log.user, log.sent_by), ('sent', 'activation', self.staff, self.admin))

    def test_only_one_active_configuration(self):
        from .models import SiteConfiguration
        a = SiteConfiguration.objects.create(name='A', site_url='https://a.example.org', is_active=True)
        SiteConfiguration.objects.create(name='B', site_url='https://b.example.org', is_active=True)
        a.refresh_from_db()
        self.assertFalse(a.is_active)

    def test_falls_back_to_env_settings(self):
        from .email import from_email, site_url
        with override_settings(SITE_URL='http://local.test/', DEFAULT_FROM_EMAIL='NCD <x@example.org>'):
            self.assertEqual((site_url(), from_email()), ('http://local.test', 'NCD <x@example.org>'))

    def test_failed_delivery_is_logged_and_bulk_continues(self):
        from users.services import send_bulk_activation

        from .models import EmailLog
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('SMTP down')):
            sent, failed = send_bulk_activation([self.staff], actor=self.admin)
        self.assertEqual((sent, len(failed)), ([], 1))
        log = EmailLog.objects.get()
        self.assertEqual((log.status, log.error), ('failed', 'SMTP down'))

    def test_failed_activation_on_resend_keeps_log_and_shows_error(self):
        from .models import EmailLog
        self.client.force_login(self.admin)
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('SMTP down')):
            response = self.client.post(reverse('users:resend_activation', args=[self.staff.pk]), follow=True)
        self.assertContains(response, 'could not be sent')
        self.assertEqual(EmailLog.objects.get().status, 'failed')

    def test_password_reset_is_logged_with_site_url(self):
        from django.core import mail

        from .models import EmailLog
        self.staff.set_password('Activated-123')
        self.staff.save()
        with override_settings(SITE_URL='https://ncd.example.org'):
            self.client.post(reverse('password_reset'), {'email': 'new@example.org'})
        self.assertIn('https://ncd.example.org/accounts/reset/', mail.outbox[0].body)
        self.assertEqual(EmailLog.objects.get().purpose, 'password_reset')
        self.client.post(reverse('password_reset'), {'email': 'nobody@example.org'})
        self.assertEqual(EmailLog.objects.count(), 1)

    def test_pages_and_permissions(self):
        from .models import SiteConfiguration
        config = SiteConfiguration.objects.create(name='Prod', site_url='https://ncd.example.org')
        self.client.force_login(self.admin)
        for url in (reverse('email:log'), reverse('email:log') + '?status=failed&q=x&start=2026-01-01',
                    reverse('email:settings'), reverse('email:settings_add'),
                    reverse('email:settings_edit', args=[config.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(reverse('email:settings_activate', args=[config.pk]))
        config.refresh_from_db()
        self.assertTrue(config.is_active)
        self.client.post(reverse('email:test'))
        from .models import EmailLog
        self.assertEqual(EmailLog.objects.filter(purpose='test').count(), 1)
        nurse = self.make_user('nurse_e', 'Nurse / Triage Officer', facility=self.ilala)
        self.client.force_login(nurse)
        self.assertEqual(self.client.get(reverse('email:log')).status_code, 403)
        self.assertEqual(self.client.get(reverse('email:settings')).status_code, 403)
        manager = self.make_user('dm_e', 'Data Manager', facility=self.ilala)
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse('email:log')).status_code, 200)
        self.assertEqual(self.client.get(reverse('email:settings_add')).status_code, 403)


class SmsSettingsTests(BaseTestCase):
    def test_page_settings_encrypted_override_env_and_test_message(self):
        from .models import SmsSettings
        self.client.force_login(self.admin)
        self.assertEqual(sms_config().backend, 'console')  # .env default
        self.client.post(reverse('notifications:settings'), {
            'mode': 'live', 'provider': 'beem', 'sender_id': 'NIMR', 'default_contact': '0755000000',
            'at_username': '', 'beem_api_key': 'key-123', 'beem_secret_key': 'secret-456', 'at_api_key': ''})
        row = SmsSettings.load()
        self.assertNotIn('secret-456', row.beem_secret_key_encrypted)
        config = sms_config()
        self.assertEqual((config.backend, config.beem_secret_key, config.sender_id, config.source),
                         ('beem', 'secret-456', 'NIMR', 'SMS settings'))
        # a blank key box keeps the saved key
        self.client.post(reverse('notifications:settings'), {
            'mode': 'console', 'provider': 'beem', 'sender_id': 'NIMR', 'default_contact': '', 'at_username': '',
            'beem_api_key': '', 'beem_secret_key': '', 'at_api_key': ''})
        self.assertEqual((sms_config().backend, sms_config().beem_secret_key), ('console', 'secret-456'))
        self.client.post(reverse('notifications:test'), {'phone': '0712 000 111', 'message': 'Test'})
        test = SmsMessage.objects.get(purpose='test')
        self.assertEqual((test.status, test.phone, test.patient), ('sent', '255712000111', None))
        self.assertContains(self.client.get(reverse('notifications:log')), '(test)')
        audit = AuditEvent.objects.filter(action='update', object_id=str(row.pk)).last()
        self.assertNotIn('secret-456', str(audit.changes))

    def test_only_permitted_roles(self):
        self.client.force_login(self.clinician_ilala)
        self.assertEqual(self.client.get(reverse('notifications:settings')).status_code, 403)

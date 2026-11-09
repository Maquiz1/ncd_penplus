from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import redirect, render
from django.utils import timezone
from django.contrib.auth.decorators import login_not_required
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import ListView

from audit.models import AuditEvent
from audit.services import log_event
from facilities.scope import scope_queryset

from .forms import SmsSettingsForm, TestSmsForm
from .models import SmsMessage, SmsSettings
from .services import process_queue
from .sms import SmsError, delivery_report_token, delivery_report_url, get_backend, mask_phone, sms_config

# The System settings pages (email, SMS, scheduled jobs) are for system administrators and superusers.
SYSTEM_PERMISSION = 'notifications.change_smssettings'


class SmsLogView(PermissionRequiredMixin, ListView):
    permission_required = SYSTEM_PERMISSION
    template_name = 'notifications/sms_log.html'
    paginate_by = 50

    def get_queryset(self):
        qs = scope_queryset(SmsMessage.objects.select_related('patient', 'clinic_session'), self.request.user,
                            'patient__current_facility')
        if self.request.user.has_perm('notifications.view_smssettings'):  # test and staff messages have no patient
            qs = qs | SmsMessage.objects.filter(patient__isnull=True)
        for key in ('status', 'purpose', 'clinic_session'):
            if self.request.GET.get(key):
                qs = qs.filter(**{key: self.request.GET[key]})
        return qs

    def get_context_data(self, **kwargs):
        try:
            backend = sms_config().backend
        except SmsError as exc:
            backend = f'not readable ({exc})'
        return super().get_context_data(statuses=SmsMessage.Status.choices, purposes=SmsMessage.Purpose.choices,
                                        backend=backend, test_phone=SmsSettings.load().test_phone, **kwargs)


class SmsSendQueuedView(PermissionRequiredMixin, View):
    permission_required = 'notifications.add_smsmessage'

    def post(self, request):
        counts = process_queue(limit=300)
        messages.success(request, f'Sent {counts["sent"]}, failed {counts["failed"]}, cancelled {counts["cancelled"]}.')
        return redirect('notifications:log')


class SmsSettingsView(PermissionRequiredMixin, View):
    """Gateway, sender and credentials (encrypted), plus a test message to check them."""

    permission_required = SYSTEM_PERMISSION
    template_name = 'notifications/sms_settings.html'

    def context(self, request, form, test_form):
        try:
            effective, error = sms_config(), ''
        except SmsError as exc:
            effective, error = None, str(exc)
        return {'form': form, 'test_form': test_form, 'effective': effective, 'config_error': error,
                'delivery_url': delivery_report_url(), 'verify_token': delivery_report_token(),
                'saved': SmsSettings.load(), 'can_change': request.user.has_perm('notifications.change_smssettings')}

    def get(self, request):
        return render(request, self.template_name,
                      self.context(request, SmsSettingsForm(instance=SmsSettings.load()), TestSmsForm()))

    def post(self, request):
        if not request.user.has_perm('notifications.change_smssettings'):
            messages.error(request, 'You can view the SMS settings but not change them.')
            return redirect('notifications:settings')
        form = SmsSettingsForm(request.POST, instance=SmsSettings.load())
        if not form.is_valid():
            return render(request, self.template_name, self.context(request, form, TestSmsForm()))
        row = form.save(commit=False)
        row.updated_by = request.user
        row.save()
        changed = [f for f in form.changed_data if f != 'clear_secrets' or form.cleaned_data['clear_secrets']]
        log_event(request=request, action=AuditEvent.Action.UPDATE, obj=row,
                  changes={'changed': changed})  # secret values are never written to the audit log
        messages.success(request, 'SMS settings saved. Send a test message to check them.')
        return redirect('notifications:settings')


class SendTestSmsView(PermissionRequiredMixin, View):
    permission_required = 'notifications.change_smssettings'

    def post(self, request):
        form = TestSmsForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Enter a valid Tanzanian mobile number for the test message.')
            return redirect('notifications:settings')
        phone, body = form.cleaned_data['phone'], form.cleaned_data['message']
        message = SmsMessage(purpose=SmsMessage.Purpose.TEST, phone=phone, recipient='staff test', body=body,
                             created_by=request.user, attempts=1)
        try:
            backend = get_backend()
            message.backend = backend.name
            message.provider_reference = backend.send(phone, body)[:100]
        except SmsError as exc:
            message.status, message.error = SmsMessage.Status.FAILED, str(exc)[:255]
            messages.error(request, f'The test SMS failed: {exc}')
        else:
            message.status, message.sent_at = SmsMessage.Status.SENT, timezone.now()
            note = ' (gateway off: logged only, not sent to the phone)' if backend.name == 'console' else ''
            messages.success(request, f'Test SMS sent to {mask_phone(phone)} through {backend.name}{note}.')
        message.save()
        return redirect('notifications:settings')


class SmsResendView(PermissionRequiredMixin, View):
    """Send a failed or cancelled SMS again, as a new message (the first one stays in the log)."""

    permission_required = SYSTEM_PERMISSION

    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        original = get_object_or_404(SmsMessage, pk=pk, status__in=['failed', 'cancelled'])
        copy = SmsMessage.objects.create(
            patient=original.patient, appointment=None, clinic_session=original.clinic_session,
            purpose=original.purpose, phone=original.phone, recipient=original.recipient, body=original.body,
            for_date=original.for_date, created_by=request.user)
        # The first one is not retried as well.
        SmsMessage.objects.filter(pk=original.pk).update(status=SmsMessage.Status.CANCELLED,
                                                         error=f'Resent as SMS {copy.pk}')
        process_queue(limit=50)
        copy.refresh_from_db()
        log_event(request=request, action=AuditEvent.Action.CREATE, obj=copy, reason=f'Resend of SMS {original.pk}')
        if copy.status == SmsMessage.Status.SENT:
            messages.success(request, f'SMS sent again to {mask_phone(copy.phone)}.')
        else:
            messages.error(request, f'Sending again failed: {copy.error or copy.get_status_display()}.')
        return redirect(request.POST.get('next') or 'notifications:log')


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(login_not_required, name='dispatch')
class DeliveryReportView(View):
    """NextSMS delivery report webhook: JSON {"token", "messageId", "status"}. Public URL protected by a secret in
    the URL, and the same secret as NextSMS's Verify Token."""

    def post(self, request, token):
        import hmac
        import json

        from django.http import Http404, HttpResponse

        from .sms import record_delivery
        if not hmac.compare_digest(token, delivery_report_token()):
            raise Http404
        try:
            data = json.loads(request.body or b'{}')
        except ValueError:
            return HttpResponse('Bad JSON', status=400)
        if data.get('token') and not hmac.compare_digest(str(data['token']), delivery_report_token()):
            return HttpResponse('Wrong verify token', status=401)
        status = data.get('status')
        if isinstance(status, dict):
            status = status.get('groupName') or status.get('name')
        record_delivery(data.get('messageId'), status or '')
        return HttpResponse('OK')   # NextSMS sends a test request when the URL is saved


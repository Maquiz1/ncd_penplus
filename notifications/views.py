from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from audit.models import AuditEvent
from audit.services import log_event
from facilities.scope import scope_queryset

from .forms import SmsSettingsForm, TestSmsForm
from .models import SmsMessage, SmsSettings
from .services import process_queue
from .sms import SmsError, get_backend, mask_phone, sms_config


class SmsLogView(PermissionRequiredMixin, ListView):
    permission_required = 'notifications.view_smsmessage'
    template_name = 'notifications/sms_log.html'
    paginate_by = 50

    def get_queryset(self):
        qs = scope_queryset(SmsMessage.objects.select_related('patient', 'clinic_session'), self.request.user,
                            'patient__current_facility')
        if self.request.user.has_perm('notifications.view_smssettings'):  # test messages have no patient
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
                                        backend=backend, **kwargs)


class SmsSendQueuedView(PermissionRequiredMixin, View):
    permission_required = 'notifications.add_smsmessage'

    def post(self, request):
        counts = process_queue(limit=300)
        messages.success(request, f'Sent {counts["sent"]}, failed {counts["failed"]}, cancelled {counts["cancelled"]}.')
        return redirect('notifications:log')


class SmsSettingsView(PermissionRequiredMixin, View):
    """Gateway, sender and credentials (encrypted), plus a test message to check them."""

    permission_required = 'notifications.view_smssettings'
    template_name = 'notifications/sms_settings.html'

    def context(self, request, form, test_form):
        try:
            effective, error = sms_config(), ''
        except SmsError as exc:
            effective, error = None, str(exc)
        return {'form': form, 'test_form': test_form, 'effective': effective, 'config_error': error,
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

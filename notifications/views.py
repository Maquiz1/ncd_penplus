from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import redirect
from django.views import View
from django.views.generic import ListView

from facilities.scope import scope_queryset

from .models import SmsMessage
from .services import process_queue


class SmsLogView(PermissionRequiredMixin, ListView):
    permission_required = 'notifications.view_smsmessage'
    template_name = 'notifications/sms_log.html'
    paginate_by = 50

    def get_queryset(self):
        qs = scope_queryset(SmsMessage.objects.select_related('patient', 'clinic_session'), self.request.user,
                            'patient__current_facility')
        for key in ('status', 'purpose', 'clinic_session'):
            if self.request.GET.get(key):
                qs = qs.filter(**{key: self.request.GET[key]})
        return qs

    def get_context_data(self, **kwargs):
        from django.conf import settings
        return super().get_context_data(statuses=SmsMessage.Status.choices, purposes=SmsMessage.Purpose.choices,
                                        backend=settings.SMS_BACKEND, **kwargs)


class SmsSendQueuedView(PermissionRequiredMixin, View):
    permission_required = 'notifications.add_smsmessage'

    def post(self, request):
        counts = process_queue(limit=300)
        messages.success(request, f'Sent {counts["sent"]}, failed {counts["failed"]}, cancelled {counts["cancelled"]}.')
        return redirect('notifications:log')

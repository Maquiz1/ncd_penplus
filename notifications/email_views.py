from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from core.forms import BootstrapModelForm
from users.views import staff_in_scope

from .email import active_site, from_email, send_logged_email, site_url
from .models import EmailLog, SiteConfiguration


class EmailLogView(PermissionRequiredMixin, ListView):
    permission_required = 'notifications.view_emaillog'
    template_name = 'notifications/email_log.html'
    paginate_by = 50

    def get_queryset(self):
        qs = EmailLog.objects.select_related('user', 'sent_by')
        if not self.request.user.is_superuser:
            staff = staff_in_scope(self.request.user)
            qs = qs.filter(Q(user__in=staff) | Q(user__isnull=True, sent_by=self.request.user))
        get = self.request.GET
        if get.get('q'):
            qs = qs.filter(Q(recipient__icontains=get['q']) | Q(subject__icontains=get['q'])
                           | Q(user__username__icontains=get['q']))
        if get.get('start'):
            qs = qs.filter(created_at__date__gte=get['start'])
        if get.get('end'):
            qs = qs.filter(created_at__date__lte=get['end'])
        for key in ('status', 'purpose'):
            if get.get(key):
                qs = qs.filter(**{key: get[key]})
        return qs

    def get_context_data(self, **kwargs):
        counts = dict(self.get_queryset().order_by().values_list('status').annotate(n=Count('id')))
        return super().get_context_data(
            statuses=EmailLog.Status.choices, purposes=EmailLog.Purpose.choices, counts=counts,
            backend=settings.EMAIL_BACKEND.rsplit('.', 2)[-2], site_url=site_url(), sender=from_email(), **kwargs)


class SiteConfigurationForm(BootstrapModelForm):
    class Meta:
        model = SiteConfiguration
        fields = ['name', 'site_url', 'from_name', 'from_email', 'is_active']


class SiteConfigurationListView(PermissionRequiredMixin, ListView):
    permission_required = 'notifications.view_siteconfiguration'
    model = SiteConfiguration
    template_name = 'notifications/site_settings.html'

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            active=active_site(), site_url=site_url(), sender=from_email(), env_site_url=settings.SITE_URL,
            env_sender=settings.DEFAULT_FROM_EMAIL, backend=settings.EMAIL_BACKEND,
            email_host=getattr(settings, 'EMAIL_HOST', ''), file_path=getattr(settings, 'EMAIL_FILE_PATH', ''),
            **kwargs)


class SiteConfigurationSaveMixin(PermissionRequiredMixin):
    model = SiteConfiguration
    form_class = SiteConfigurationForm
    template_name = 'generic_form.html'
    success_url = reverse_lazy('email:settings')

    def form_valid(self, form):
        response = super().form_valid(form)
        log_event(request=self.request, action=self.audit_action, obj=self.object,
                  changes={f: str(form.cleaned_data[f]) for f in form.changed_data})
        return response

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            cancel_url=reverse('email:settings'),
            subtitle='Used in every system email: activation and password-reset links, reports. '
                     'Ticking “active” makes this the only active configuration.', **kwargs)


class SiteConfigurationCreateView(SiteConfigurationSaveMixin, CreateView):
    permission_required = 'notifications.add_siteconfiguration'
    audit_action = AuditEvent.Action.CREATE
    extra_context = {'title': 'Add site configuration'}


class SiteConfigurationUpdateView(SiteConfigurationSaveMixin, UpdateView):
    permission_required = 'notifications.change_siteconfiguration'
    audit_action = AuditEvent.Action.UPDATE
    extra_context = {'title': 'Edit site configuration'}


class SiteConfigurationActivateView(PermissionRequiredMixin, View):
    permission_required = 'notifications.change_siteconfiguration'

    def post(self, request, pk):
        config = get_object_or_404(SiteConfiguration, pk=pk)
        config.is_active = True
        config.save()
        log_event(request=request, action=AuditEvent.Action.UPDATE, obj=config, changes={'is_active': 'True'})
        messages.success(request, f'Emails now use {config.site_url}.')
        return redirect('email:settings')


class SendTestEmailView(PermissionRequiredMixin, View):
    """Send a test email to the signed-in staff member's own address."""

    permission_required = 'notifications.view_siteconfiguration'

    def post(self, request):
        if not request.user.email:
            messages.error(request, 'Add an email address to your own account first.')
            return redirect('email:settings')
        body = (f'This is a test email from the {settings.SITE_NAME}.\n\n'
                f'System address: {site_url()}\nSender: {from_email()}\n\n'
                'If you received it, staff activation and password-reset emails will be delivered too.')
        try:
            send_logged_email(request.user.email, f'Test email – {settings.SITE_NAME}', body, purpose='test',
                              user=request.user, sent_by=request.user)
        except Exception as exc:  # noqa: BLE001 - shown to the administrator and kept in the log
            messages.error(request, f'Sending failed: {str(exc)[:300]}')
        else:
            if 'filebased' in settings.EMAIL_BACKEND or 'console' in settings.EMAIL_BACKEND:
                messages.warning(request, f'Test email to {request.user.email} was only saved on the server, not '
                                          'delivered: the mail server is not set up yet (see "Mail server" below).')
            else:
                messages.success(request, f'Test email sent to {request.user.email}.')
        return redirect('email:settings')

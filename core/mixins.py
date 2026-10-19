from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import get_object_or_404

from audit.models import AuditEvent
from audit.services import diff, log_event, snapshot
from facilities.scope import scope_queryset


class ScopedQuerysetMixin:
    """Limit list/detail querysets to the user's facility/geographic scope."""

    scope_field = 'patient__current_facility'

    def get_queryset(self):
        return scope_queryset(super().get_queryset(), self.request.user, self.scope_field)


class AuditedCreateMixin:
    success_message = 'Saved.'

    def form_valid(self, form):
        if hasattr(form.instance, 'created_by_id') and not form.instance.created_by_id:
            form.instance.created_by = self.request.user
        response = super().form_valid(form)
        log_event(request=self.request, action=AuditEvent.Action.CREATE, obj=self.object)
        if self.success_message:
            messages.success(self.request, self.success_message)
        return response


class AuditedUpdateMixin:
    success_message = 'Changes saved.'

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        self._audit_before = snapshot(obj)
        return obj

    def form_valid(self, form):
        response = super().form_valid(form)
        changes = diff(self._audit_before, snapshot(self.object))
        if changes:
            log_event(request=self.request, action=AuditEvent.Action.UPDATE, obj=self.object, changes=changes)
        if self.success_message:
            messages.success(self.request, self.success_message)
        return response


class PatientContextMixin:
    """Load the patient from the URL, enforcing the user's scope."""

    def dispatch(self, request, *args, **kwargs):
        from patients.models import Patient

        self.patient = get_object_or_404(Patient.objects.in_scope(request.user), pk=kwargs['patient_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs.setdefault('patient', self.patient)
        return super().get_context_data(**kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(patient=self.patient, user=self.request.user)
        return kwargs

    def get_success_url(self):
        return self.patient.get_absolute_url() + getattr(self, 'success_tab', '')


class PatientChildCreateMixin(PermissionRequiredMixin, PatientContextMixin, AuditedCreateMixin):
    """Create a record for the patient in the URL, defaulting to the patient's current facility."""

    template_name = 'generic_form.html'
    title = ''

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault('facility', self.patient.current_facility_id)
        return initial

    def form_valid(self, form):
        form.instance.patient = self.patient
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        kwargs.setdefault('title', self.title)
        kwargs.setdefault('cancel_url', self.patient.get_absolute_url())
        return super().get_context_data(**kwargs)


class PatientChildUpdateMixin(PermissionRequiredMixin, AuditedUpdateMixin):
    """Edit a patient-owned record; scope is enforced through the record's patient."""

    template_name = 'generic_form.html'
    title = ''
    success_tab = ''

    def get_queryset(self):
        return scope_queryset(super().get_queryset(), self.request.user, 'patient__current_facility')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(patient=self.object.patient, user=self.request.user)
        return kwargs

    def get_success_url(self):
        return self.object.patient.get_absolute_url() + self.success_tab

    def get_context_data(self, **kwargs):
        kwargs.setdefault('title', self.title)
        kwargs.setdefault('patient', self.object.patient)
        kwargs.setdefault('cancel_url', self.object.patient.get_absolute_url())
        return super().get_context_data(**kwargs)

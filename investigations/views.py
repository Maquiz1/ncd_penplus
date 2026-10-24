from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from core.mixins import (AuditedCreateMixin, AuditedUpdateMixin, PatientChildCreateMixin, PatientChildUpdateMixin,
                         ScopedQuerysetMixin)
from facilities.scope import scope_queryset
from issues.detectors import diagnosis_prompts
from pharmacy.services import consume_for_service, shortfall_message

from .forms import InvestigationFieldForm, InvestigationForm, InvestigationReportForm, InvestigationTypeForm
from .models import InvestigationField, InvestigationOrder, InvestigationType


class InvestigationCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'investigations.add_investigationorder'
    model = InvestigationOrder
    form_class = InvestigationForm
    title = 'Request investigation (ECG / echo / imaging)'
    success_tab = '#labs'

    def get_initial(self):
        initial = super().get_initial()
        initial['encounter'] = self.request.GET.get('encounter')
        initial['investigation_type'] = self.request.GET.get('type')
        return initial

    def form_valid(self, form):
        form.instance.requested_by = self.request.user
        return super().form_valid(form)


class InvestigationWorklistView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'investigations.view_investigationorder'
    model = InvestigationOrder
    template_name = 'investigations/worklist.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('patient', 'facility', 'investigation_type')
        status = self.request.GET.get('status', 'pending')
        if status == 'pending':
            qs = qs.filter(status__in=['requested', 'scheduled'])
        elif status:
            qs = qs.filter(status=status)
        for key in ('modality', 'investigation_type'):
            if self.request.GET.get(key):
                qs = qs.filter(**{key: self.request.GET[key]})
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(statuses=InvestigationOrder.Status.choices,
                                        modalities=InvestigationOrder.Modality.choices,
                                        types=InvestigationType.objects.all(),
                                        current_status=self.request.GET.get('status', 'pending'), **kwargs)


class InvestigationDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'investigations.view_investigationorder'
    template_name = 'investigations/detail.html'
    context_object_name = 'inv'

    def get_queryset(self):
        return scope_queryset(InvestigationOrder.objects.select_related('patient', 'investigation_type', 'facility'),
                              self.request.user, 'patient__current_facility')

    def get_context_data(self, **kwargs):
        return super().get_context_data(patient=self.object.patient,
                                        consumption=self.object.stock_movements.select_related('batch__medication'),
                                        **kwargs)


class InvestigationReportView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'investigations.change_investigationorder'
    model = InvestigationOrder
    form_class = InvestigationReportForm
    title = 'Enter investigation report'
    success_tab = '#labs'

    def get_queryset(self):
        return super().get_queryset().filter(status__in=['requested', 'scheduled', 'completed'])

    def get_context_data(self, **kwargs):
        kwargs.setdefault('subtitle', f'{self.object.name} – {self.object.description}')
        return super().get_context_data(**kwargs)

    def form_valid(self, form):
        form.instance.reported_by = self.request.user
        response = super().form_valid(form)
        shortfalls = consume_for_service(self.request.user, investigation=self.object, request=self.request)[1]
        diagnosis_prompts([self.object.patient_id])
        if shortfalls:
            messages.warning(self.request, f'Report saved, but stock was short: {shortfall_message(shortfalls)}. '
                                           'Recorded as a stock-out.')
        return response


class InvestigationReviewView(PermissionRequiredMixin, View):
    permission_required = 'investigations.review_investigationorder'

    def post(self, request, pk):
        inv = get_object_or_404(scope_queryset(InvestigationOrder.objects.all(), request.user,
                                               'patient__current_facility'), pk=pk, status='completed')
        inv.status, inv.reviewed_by, inv.reviewed_at = 'reviewed', request.user, timezone.now()
        inv.save()
        log_event(request=request, action=AuditEvent.Action.REVIEW, obj=inv)
        messages.success(request, 'Investigation reviewed.')
        return redirect(request.POST.get('next') or inv.patient.get_absolute_url() + '#labs')


class InvestigationAttachmentView(PermissionRequiredMixin, View):
    """Serve uploaded reports only after a permission and scope check."""

    permission_required = 'investigations.view_investigationorder'

    def get(self, request, pk):
        inv = get_object_or_404(scope_queryset(InvestigationOrder.objects.all(), request.user,
                                               'patient__current_facility'), pk=pk)
        if not inv.attachment:
            raise Http404
        log_event(request=request, action=AuditEvent.Action.EXPORT, obj=inv, reason='Report attachment viewed')
        return FileResponse(inv.attachment.open('rb'), filename=inv.attachment.name.rsplit('/', 1)[-1])


# ---------------------------------------------------------------- catalogue

class CatalogueView(PermissionRequiredMixin, ListView):
    permission_required = 'investigations.view_investigationtype'
    template_name = 'investigations/catalogue.html'

    def get_queryset(self):
        return InvestigationType.objects.prefetch_related('fields', 'consumables__item')


class TypeDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'investigations.view_investigationtype'
    model = InvestigationType
    template_name = 'investigations/type_detail.html'
    context_object_name = 'itype'

    def get_context_data(self, **kwargs):
        return super().get_context_data(fields=self.object.fields.all(),
                                        consumables=self.object.consumables.select_related('item'), **kwargs)


class TypeCreateView(PermissionRequiredMixin, AuditedCreateMixin, CreateView):
    permission_required = 'investigations.add_investigationtype'
    model = InvestigationType
    form_class = InvestigationTypeForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Add investigation type', 'subtitle': 'Then add its report fields and consumables.'}

    def get_success_url(self):
        return reverse('investigations:type_detail', args=[self.object.pk])


class TypeUpdateView(PermissionRequiredMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'investigations.change_investigationtype'
    model = InvestigationType
    form_class = InvestigationTypeForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Edit investigation type'}

    def get_success_url(self):
        return reverse('investigations:type_detail', args=[self.object.pk])


class FieldSaveMixin:
    model = InvestigationField
    form_class = InvestigationFieldForm
    template_name = 'generic_form.html'

    def get_success_url(self):
        return reverse('investigations:type_detail', args=[self.object.investigation_type_id])


class FieldCreateView(PermissionRequiredMixin, FieldSaveMixin, AuditedCreateMixin, CreateView):
    permission_required = 'investigations.change_investigationtype'
    extra_context = {'title': 'Add report field'}

    def form_valid(self, form):
        form.instance.investigation_type = get_object_or_404(InvestigationType, pk=self.kwargs['type_pk'])
        return super().form_valid(form)


class FieldUpdateView(PermissionRequiredMixin, FieldSaveMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'investigations.change_investigationtype'
    extra_context = {'title': 'Edit report field',
                     'subtitle': 'Earlier reports keep the values they were saved with.'}

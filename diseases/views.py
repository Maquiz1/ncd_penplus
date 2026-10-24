from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from clinical.models import Diagnosis, Disease
from core.forms import ReasonForm
from core.mixins import AuditedCreateMixin, AuditedUpdateMixin

from .forms import ConditionForm, DiseaseForm
from .models import Condition, match_condition


def with_usage(qs):
    return qs.annotate(
        n_conditions=Count('conditions', distinct=True),
        n_patients=Count('diagnoses__patient', distinct=True),
        n_enrollments=Count('programmes__enrollments', distinct=True),
        n_active=Count('programmes__enrollments', filter=Q(programmes__enrollments__status='active'), distinct=True))


class DiseaseListView(PermissionRequiredMixin, ListView):
    permission_required = 'clinical.view_disease'
    template_name = 'diseases/disease_list.html'

    def get_queryset(self):
        return with_usage(Disease.objects.all()).order_by('-is_active', 'order', 'name')

    def get_context_data(self, **kwargs):
        deleted = with_usage(Disease.all_objects.filter(is_deleted=True)) if self.request.user.is_superuser else None
        return super().get_context_data(deleted=deleted, **kwargs)


class DiseaseDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'clinical.view_disease'
    template_name = 'diseases/disease_detail.html'
    context_object_name = 'disease'

    def get_queryset(self):
        return Disease.all_objects.all() if self.request.user.is_superuser else Disease.objects.all()

    def get_context_data(self, **kwargs):
        disease = self.object
        conditions = (disease.conditions.filter(parent__isnull=True).annotate(n=Count('diagnoses', distinct=True))
                      .prefetch_related('excludes', 'children').order_by('order', 'name'))
        return super().get_context_data(
            conditions=conditions, programmes=disease.programmes.annotate(n=Count('enrollments')),
            unlinked=Diagnosis.objects.filter(disease=disease, condition__isnull=True).count(), **kwargs)


class DiseaseCreateView(PermissionRequiredMixin, AuditedCreateMixin, CreateView):
    permission_required = 'clinical.add_disease'
    model = Disease
    form_class = DiseaseForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Add disease',
                     'subtitle': 'Then add its reporting conditions, a clinical programme (admin) and its forms.'}

    def form_valid(self, form):
        if Disease.all_objects.filter(code=form.cleaned_data['code']).exists():
            form.add_error('code', 'This code belongs to a deleted disease. Restore it instead, or use another code.')
            return self.form_invalid(form)
        return super().form_valid(form)


class DiseaseUpdateView(PermissionRequiredMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'clinical.change_disease'
    model = Disease
    form_class = DiseaseForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Edit disease'}


def disease_or_404(request, pk, deleted=False):
    return get_object_or_404(Disease.all_objects, pk=pk, is_deleted=deleted)


class DiseaseToggleView(PermissionRequiredMixin, View):
    permission_required = 'clinical.change_disease'

    def post(self, request, pk):
        disease = disease_or_404(request, pk)
        disease.is_active = not disease.is_active
        disease.save(update_fields=['is_active'])
        log_event(request=request, action=AuditEvent.Action.STATUS, obj=disease,
                  changes={'is_active': [not disease.is_active, disease.is_active]})
        messages.success(request, f'{disease} {"activated" if disease.is_active else "deactivated: hidden from forms, indicators and reports"}.')
        return redirect(disease.get_absolute_url())


class DiseaseDeleteView(PermissionRequiredMixin, View):
    """Soft delete: the disease disappears everywhere, but all records and history are kept."""

    permission_required = 'clinical.delete_disease'

    def render_form(self, request, disease, form):
        return render(request, 'confirm_action.html', {
            'title': f'Delete disease – {disease}', 'form': form, 'button': 'Delete disease', 'button_class': 'btn-danger',
            'message': 'The disease is hidden everywhere and can no longer be used. Its patients, diagnoses and history '
                       'are kept, and a superuser can restore it.', 'cancel_url': disease.get_absolute_url()})

    def get(self, request, pk):
        return self.render_form(request, disease_or_404(request, pk), ReasonForm())

    def post(self, request, pk):
        disease = disease_or_404(request, pk)
        form = ReasonForm(request.POST)
        if not form.is_valid():
            return self.render_form(request, disease, form)
        disease.is_deleted, disease.is_active = True, False
        disease.deleted_at, disease.deleted_by = timezone.now(), request.user
        disease.deletion_reason = form.cleaned_data['reason']
        disease.save(update_fields=['is_deleted', 'is_active', 'deleted_at', 'deleted_by', 'deletion_reason'])
        log_event(request=request, action=AuditEvent.Action.DELETE, obj=disease, reason=disease.deletion_reason)
        messages.success(request, f'{disease} deleted. A superuser can restore it from Diseases.')
        return redirect('diseases:list')


class DiseaseRestoreView(View):
    """Superusers only: bring a deleted disease back (it returns inactive; activate it when ready)."""

    def post(self, request, pk):
        if not request.user.is_superuser:
            raise PermissionDenied('Only a superuser can restore a deleted disease.')
        disease = disease_or_404(request, pk, deleted=True)
        disease.is_deleted, disease.deleted_at, disease.deleted_by, disease.deletion_reason = False, None, None, ''
        disease.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason'])
        log_event(request=request, action=AuditEvent.Action.RESTORE, obj=disease)
        messages.success(request, f'{disease} restored (inactive). Activate it when it should be used again.')
        return redirect(disease.get_absolute_url())


class ConditionMixin(PermissionRequiredMixin):
    permission_required = 'clinical.change_disease'
    model = Condition
    form_class = ConditionForm
    template_name = 'generic_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.disease = disease_or_404(request, kwargs['disease_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'disease': self.disease}

    def get_success_url(self):
        return self.disease.get_absolute_url()

    def get_context_data(self, **kwargs):
        return super().get_context_data(cancel_url=self.get_success_url(), **kwargs)


class ConditionCreateView(ConditionMixin, AuditedCreateMixin, CreateView):
    extra_context = {'title': 'Add reporting condition'}

    def form_valid(self, form):
        form.instance.disease = self.disease
        return super().form_valid(form)


class ConditionUpdateView(ConditionMixin, AuditedUpdateMixin, UpdateView):
    extra_context = {'title': 'Edit reporting condition'}

    def get_queryset(self):
        return self.disease.conditions.all()


class RelinkDiagnosesView(PermissionRequiredMixin, View):
    """After changing keywords: re-link diagnoses that have no condition or only the default one."""

    permission_required = 'clinical.change_disease'

    def post(self, request, pk):
        disease = disease_or_404(request, pk)
        conditions = list(Condition.objects.filter(disease=disease, is_active=True))
        changed = 0
        for diagnosis in Diagnosis.objects.filter(disease=disease).filter(Q(condition__isnull=True) | Q(condition__is_default=True)):
            condition, _matched = match_condition(diagnosis.description, disease, conditions)
            if condition and condition.pk != diagnosis.condition_id:
                Diagnosis.objects.filter(pk=diagnosis.pk).update(condition=condition)
                changed += 1
        log_event(request=request, action=AuditEvent.Action.UPDATE, obj=disease, changes={'diagnoses_relinked': changed})
        messages.success(request, f'{changed} diagnosis/diagnoses re-linked to a more specific condition.')
        return redirect(disease.get_absolute_url())

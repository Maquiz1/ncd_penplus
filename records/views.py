from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.forms import modelform_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from audit.models import AuditEvent
from audit.services import diff, log_event, snapshot
from core.forms import BootstrapModelForm, ReasonForm
from patients.models import with_old_study_id
from facilities.scope import facilities_for_user, scope_queryset

from .registry import get_type, record_types
from .services import RecordError, blockers, delete_record, restore_record


def scoped(rtype, user, deleted=False):
    qs = scope_queryset(rtype.manager(deleted), user, f'{rtype.patient_path}__current_facility')
    return qs if deleted else qs.filter(**{f'{rtype.patient_path}__is_deleted': False})


def record_or_404(rtype, request, pk, deleted=False):
    return get_object_or_404(scoped(rtype, request.user, deleted), pk=pk)


def type_or_404(request, key, action='view'):
    rtype = get_type(key)
    if rtype is None:
        raise Http404
    if not request.user.has_perm(rtype.perm(action)):
        raise PermissionDenied
    return rtype


class RecordsHomeView(LoginRequiredMixin, View):
    """Every record type (form / table), grouped like the old EDCS, with how many records are in scope."""

    def get(self, request):
        groups = OrderedDict()
        for rtype in record_types():
            if not request.user.has_perm(rtype.perm('view')):
                continue
            count = scoped(rtype, request.user).count()
            deleted = scoped(rtype, request.user, deleted=True).count() if request.user.is_superuser else 0
            groups.setdefault(rtype.group, []).append((rtype, count, deleted))
        return render(request, 'records/home.html', {'groups': groups})


class RecordListView(LoginRequiredMixin, View):
    def get(self, request, key):
        rtype = type_or_404(request, key)
        deleted = request.GET.get('tab') == 'deleted'
        if deleted and not request.user.is_superuser:
            raise PermissionDenied
        qs = scoped(rtype, request.user, deleted)
        patient_field = rtype.patient_path
        params = request.GET
        if params.get('q'):
            q = params['q'].strip()
            qs = qs.filter(Q(**{f'{patient_field}__patient_number__icontains': q})
                           | Q(**{f'{patient_field}__old_id__iexact': q})
                           | Q(**{f'{patient_field}__identifiers__id_type': 'edcs_study',
                                  f'{patient_field}__identifiers__value__iexact': q})).distinct()
        if params.get('facility'):
            qs = qs.filter(**{f'{patient_field}__current_facility': params['facility']})
        date_lookup = rtype.date_field
        is_datetime = rtype.model._meta.get_field(date_lookup).get_internal_type() == 'DateTimeField'
        if params.get('start'):
            qs = qs.filter(**{f'{date_lookup}__date__gte' if is_datetime else f'{date_lookup}__gte': params['start']})
        if params.get('end'):
            qs = qs.filter(**{f'{date_lookup}__date__lte' if is_datetime else f'{date_lookup}__lte': params['end']})
        qs = with_old_study_id(qs.select_related(patient_field), patient_field).order_by(f'-{date_lookup}', '-pk')
        if deleted:
            qs = qs.select_related('deleted_by')
        page = Paginator(qs, 50).get_page(params.get('page'))
        rows = [(obj, rtype.patient_of(obj), getattr(obj, date_lookup), rtype.cells(obj), rtype.view(obj),
                 rtype.edit(obj)) for obj in page]
        return render(request, 'records/list.html', {
            'rtype': rtype, 'rows': rows, 'page_obj': page, 'paginator': page.paginator,
            'is_paginated': page.has_other_pages(), 'deleted_tab': deleted,
            'facilities': facilities_for_user(request.user),
            'can_change': request.user.has_perm(rtype.perm('change')),
            'can_delete': request.user.has_perm(rtype.perm('delete')),
            'deleted_count': scoped(rtype, request.user, True).count() if request.user.is_superuser else 0})


class RecordEditView(LoginRequiredMixin, View):
    """Generic edit for record types without their own edit page (vitals, admissions, lab results …)."""

    def form_class(self, rtype):
        if not rtype.edit_fields:
            raise Http404
        return modelform_factory(rtype.model, form=BootstrapModelForm, fields=rtype.edit_fields)

    def render(self, request, rtype, obj, form):
        return render(request, 'generic_form.html', {
            'form': form, 'title': f'Edit {rtype.label.lower()}', 'patient': rtype.patient_of(obj),
            'subtitle': rtype.note, 'cancel_url': reverse('records:list', args=[rtype.key])})

    def get(self, request, key, pk):
        rtype = type_or_404(request, key, 'change')
        obj = record_or_404(rtype, request, pk)
        return self.render(request, rtype, obj, self.form_class(rtype)(instance=obj))

    def post(self, request, key, pk):
        rtype = type_or_404(request, key, 'change')
        obj = record_or_404(rtype, request, pk)
        before = snapshot(obj)
        form = self.form_class(rtype)(request.POST, instance=obj)
        if not form.is_valid():
            return self.render(request, rtype, obj, form)
        obj = form.save()
        changes = diff(before, snapshot(obj))
        if changes:
            log_event(request=request, action=AuditEvent.Action.UPDATE, obj=obj, changes=changes)
        messages.success(request, 'Changes saved.')
        return redirect(request.GET.get('next') or reverse('records:list', args=[rtype.key]))


class RecordDeleteView(LoginRequiredMixin, View):
    def page(self, request, rtype, obj, form):
        problems = blockers(obj)
        return render(request, 'confirm_action.html', {
            'title': f'Delete {rtype.label.lower()} record', 'patient': rtype.patient_of(obj), 'form': form,
            'message': f'{obj} — {"; ".join(problems) if problems else rtype.note or "The record is hidden everywhere, including reports. A superuser can restore it."}',
            'button': 'Delete', 'button_class': 'btn-danger', 'disabled': bool(problems),
            'next': request.GET.get('next') or request.POST.get('next', ''),
            'cancel_url': request.GET.get('next') or reverse('records:list', args=[rtype.key])})

    def get(self, request, key, pk):
        rtype = type_or_404(request, key, 'delete')
        obj = record_or_404(rtype, request, pk)
        return self.page(request, rtype, obj, ReasonForm())

    def post(self, request, key, pk):
        rtype = type_or_404(request, key, 'delete')
        obj = record_or_404(rtype, request, pk)
        form = ReasonForm(request.POST)
        if not form.is_valid():
            return self.page(request, rtype, obj, form)
        try:
            cascaded = delete_record(obj, request.user, form.cleaned_data['reason'], request=request)
        except RecordError as exc:
            messages.error(request, str(exc))
            return self.page(request, rtype, obj, form)
        messages.success(request, 'Record deleted.' + (f' {cascaded} linked record(s) deleted with it.' if cascaded else ''))
        return redirect(request.POST.get('next') or reverse('records:list', args=[rtype.key]))


class RecordRestoreView(LoginRequiredMixin, View):
    def post(self, request, key, pk):
        rtype = get_type(key)
        if rtype is None:
            raise Http404
        if not request.user.is_superuser:
            raise PermissionDenied
        obj = record_or_404(rtype, request, pk, deleted=True)
        if rtype.patient_of(obj).is_deleted:
            messages.error(request, 'The patient is deleted: restore the patient first.')
        else:
            restored = restore_record(obj, request.user, request=request)
            messages.success(request, 'Record restored.' + (f' {restored} linked record(s) restored too.' if restored else ''))
        return redirect(reverse('records:list', args=[rtype.key]) + '?tab=deleted')

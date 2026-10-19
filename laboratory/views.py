import uuid

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from core.forms import ReasonForm
from core.mixins import (AuditedCreateMixin, AuditedUpdateMixin, PatientChildCreateMixin, PatientChildUpdateMixin,
                         ScopedQuerysetMixin)
from facilities.scope import scope_queryset

from .forms import (GroupResultForm, InvestigationForm, InvestigationReportForm, LabCategoryForm, LabOrderForm,
                    LabPanelForm, LabTestForm, SpecimenForm)
from .models import InvestigationOrder, LabCategory, LabOrder, LabPanel, LabTest


def back(request, obj):
    return request.POST.get('next') or request.GET.get('next') or reverse('laboratory:lab_worklist')


class LabOrderCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'laboratory.add_laborder'
    model = LabOrder
    form_class = LabOrderForm
    title = 'Order laboratory tests'
    success_tab = '#labs'

    def get_initial(self):
        initial = super().get_initial()
        initial['encounter'] = self.request.GET.get('encounter')
        return initial

    def form_valid(self, form):
        data = {k: v for k, v in form.cleaned_data.items() if k not in ('tests', 'panels')}
        group = uuid.uuid4()
        requested = form.requested_tests()
        for test, panel in requested:
            order = LabOrder.objects.create(patient=self.patient, test=test, panel=panel, request_group=group,
                                            ordered_by=self.request.user, created_by=self.request.user, **data)
            log_event(request=self.request, action=AuditEvent.Action.CREATE, obj=order)
        messages.success(self.request, f'{len(requested)} test(s) ordered.')
        encounter = data.get('encounter')
        return redirect(encounter.get_absolute_url() if encounter else self.get_success_url())


class LabWorklistView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'laboratory.view_laborder'
    model = LabOrder
    template_name = 'laboratory/lab_worklist.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('patient', 'test', 'facility', 'ordered_by')
        status = self.request.GET.get('status', 'pending')
        if status == 'pending':
            qs = qs.filter(status__in=['ordered', 'collected'])
        elif status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(statuses=LabOrder.Status.choices,
                                        current_status=self.request.GET.get('status', 'pending'), **kwargs)


class LabOrderActionView(PermissionRequiredMixin, View):
    """collect → result → review, or cancel. Collect and result apply to all tests requested together."""

    permission_required = 'laboratory.change_laborder'
    STEPS = {
        'collect': (('ordered',), 'Record specimen collection'),
        'result': (('collected',), 'Enter results'),
        'review': (('resulted',), 'Review result'),
        'cancel': (('ordered', 'collected'), 'Cancel order'),
    }

    def get_order(self, request, pk, step):
        if step not in self.STEPS:
            raise Http404
        order = get_object_or_404(scope_queryset(LabOrder.objects.select_related('patient', 'test'), request.user,
                                                 'patient__current_facility'), pk=pk)
        if order.status not in self.STEPS[step][0]:
            raise PermissionDenied(f'This order is {order.get_status_display().lower()}.')
        if step == 'review' and not request.user.has_perm('laboratory.review_laborder'):
            raise PermissionDenied
        return order

    def group(self, order, step):
        if step in ('collect', 'result'):
            return list(LabOrder.objects.filter(request_group=order.request_group, patient=order.patient,
                                                status__in=self.STEPS[step][0]).select_related('test'))
        return [order]

    def make_form(self, step, orders, data=None):
        if step == 'collect':
            return SpecimenForm(data, initial={'collected_at': timezone.now()})
        if step == 'result':
            return GroupResultForm(data, orders=orders)
        if step == 'cancel':
            return ReasonForm(data)
        return None

    def render_step(self, request, order, orders, step, form):
        names = ', '.join(o.test.name for o in orders)
        return render(request, 'confirm_action.html', {
            'title': self.STEPS[step][1], 'patient': order.patient, 'form': form,
            'message': f'{names} — ordered {timezone.localtime(order.ordered_at):%d %b %Y %H:%M}'
                       + (f'. Result: {order.result_value} {order.result_unit}' if order.result_value else ''),
            'button': self.STEPS[step][1], 'button_class': 'btn-danger' if step == 'cancel' else 'btn-primary',
            'cancel_url': back(request, order), 'next': request.GET.get('next', '')})

    def get(self, request, pk, step):
        order = self.get_order(request, pk, step)
        orders = self.group(order, step)
        return self.render_step(request, order, orders, step, self.make_form(step, orders))

    def post(self, request, pk, step):
        order = self.get_order(request, pk, step)
        orders = self.group(order, step)
        form = self.make_form(step, orders, request.POST)
        if form is not None and not form.is_valid():
            return self.render_step(request, order, orders, step, form)
        now = timezone.now()
        changed = []
        if step == 'collect':
            for o in orders:
                o.specimen_id, o.collected_at = form.cleaned_data['specimen_id'], form.cleaned_data['collected_at']
                o.collected_by, o.status = request.user, LabOrder.Status.COLLECTED
                changed.append(o)
        elif step == 'result':
            for o, value in form.results():
                o.record_result(value, request.user, form.cleaned_data['result_notes'], now)
                changed.append(o)
        elif step == 'review':
            order.reviewed_by, order.reviewed_at, order.status = request.user, now, LabOrder.Status.REVIEWED
            changed.append(order)
        elif step == 'cancel':
            order.cancel_reason, order.status = form.cleaned_data['reason'], LabOrder.Status.CANCELLED
            changed.append(order)
        for o in changed:
            o.save()
            log_event(request=request, action=AuditEvent.Action.STATUS, obj=o, changes={'status': o.status},
                      reason=form.cleaned_data.get('reason', '') if form is not None else '')
        messages.success(request, f'{len(changed)} test(s): {self.STEPS[step][1].lower()} done.')
        return redirect(back(request, order))


class InvestigationCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'laboratory.add_investigationorder'
    model = InvestigationOrder
    form_class = InvestigationForm
    title = 'Request investigation (ECG / echo / imaging)'
    success_tab = '#labs'

    def get_initial(self):
        initial = super().get_initial()
        initial['encounter'] = self.request.GET.get('encounter')
        return initial

    def form_valid(self, form):
        form.instance.requested_by = self.request.user
        return super().form_valid(form)


class InvestigationWorklistView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'laboratory.view_investigationorder'
    model = InvestigationOrder
    template_name = 'laboratory/investigation_worklist.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('patient', 'facility')
        status = self.request.GET.get('status', 'pending')
        if status == 'pending':
            qs = qs.filter(status__in=['requested', 'scheduled'])
        elif status:
            qs = qs.filter(status=status)
        if self.request.GET.get('modality'):
            qs = qs.filter(modality=self.request.GET['modality'])
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(statuses=InvestigationOrder.Status.choices,
                                        modalities=InvestigationOrder.Modality.choices,
                                        current_status=self.request.GET.get('status', 'pending'), **kwargs)


class InvestigationReportView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'laboratory.change_investigationorder'
    model = InvestigationOrder
    form_class = InvestigationReportForm
    title = 'Enter investigation report'
    success_tab = '#labs'

    def get_queryset(self):
        return super().get_queryset().filter(status__in=['requested', 'scheduled', 'completed'])

    def form_valid(self, form):
        form.instance.reported_by = self.request.user
        return super().form_valid(form)


class InvestigationReviewView(PermissionRequiredMixin, View):
    permission_required = 'laboratory.review_laborder'

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

    permission_required = 'laboratory.view_investigationorder'

    def get(self, request, pk):
        inv = get_object_or_404(scope_queryset(InvestigationOrder.objects.all(), request.user,
                                               'patient__current_facility'), pk=pk)
        if not inv.attachment:
            raise Http404
        log_event(request=request, action=AuditEvent.Action.EXPORT, obj=inv, reason='Report attachment viewed')
        return FileResponse(inv.attachment.open('rb'), filename=inv.attachment.name.rsplit('/', 1)[-1])


# ---------------------------------------------------------------- catalogue (units and ranges are configuration)

class LabCatalogueView(PermissionRequiredMixin, ListView):
    permission_required = 'laboratory.view_labtest'
    template_name = 'laboratory/catalogue.html'

    def get_queryset(self):
        return LabTest.objects.select_related('category').order_by('category__order', 'category__name', 'order', 'name')

    def get_context_data(self, **kwargs):
        return super().get_context_data(categories=LabCategory.objects.all(),
                                        panels=LabPanel.objects.prefetch_related('tests'), **kwargs)


class CatalogueEditMixin(PermissionRequiredMixin):
    template_name = 'generic_form.html'

    def get_success_url(self):
        return reverse('laboratory:catalogue')

    def get_context_data(self, **kwargs):
        return super().get_context_data(cancel_url=self.get_success_url(), **kwargs)


class LabTestCreateView(CatalogueEditMixin, AuditedCreateMixin, CreateView):
    permission_required = 'laboratory.add_labtest'
    model = LabTest
    form_class = LabTestForm
    extra_context = {'title': 'Add lab test / parameter'}


class LabTestUpdateView(CatalogueEditMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'laboratory.change_labtest'
    model = LabTest
    form_class = LabTestForm
    extra_context = {'title': 'Edit lab test, unit and reference range',
                     'subtitle': 'Results already recorded keep the unit and range they were recorded with.'}

    def form_valid(self, form):
        old_unit = LabTest.objects.get(pk=self.object.pk).unit
        response = super().form_valid(form)
        if form.cleaned_data.get('apply_unit_to_existing') and old_unit != self.object.unit:
            count = LabOrder.objects.filter(test=self.object, result_unit=old_unit).exclude(result_value='').update(
                result_unit=self.object.unit)
            log_event(request=self.request, action=AuditEvent.Action.UPDATE, obj=self.object,
                      changes={'relabelled_results': count, 'unit': [old_unit, self.object.unit]},
                      reason='Unit label corrected on existing results')
            messages.info(self.request, f'{count} existing result(s) relabelled from "{old_unit}" to "{self.object.unit}".')
        return response


class LabCategoryCreateView(CatalogueEditMixin, AuditedCreateMixin, CreateView):
    permission_required = 'laboratory.add_labtest'
    model = LabCategory
    form_class = LabCategoryForm
    extra_context = {'title': 'Add lab category'}


class LabCategoryUpdateView(CatalogueEditMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'laboratory.change_labtest'
    model = LabCategory
    form_class = LabCategoryForm
    extra_context = {'title': 'Edit lab category'}


class LabPanelCreateView(CatalogueEditMixin, AuditedCreateMixin, CreateView):
    permission_required = 'laboratory.add_labtest'
    model = LabPanel
    form_class = LabPanelForm
    extra_context = {'title': 'Add lab panel'}


class LabPanelUpdateView(CatalogueEditMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'laboratory.change_labtest'
    model = LabPanel
    form_class = LabPanelForm
    extra_context = {'title': 'Edit lab panel'}

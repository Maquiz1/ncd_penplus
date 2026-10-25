import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from core.forms import ReasonForm
from core.mixins import (AuditedCreateMixin, AuditedUpdateMixin, PatientChildCreateMixin, PatientContextMixin,
                         ScopedQuerysetMixin)
from facilities.scope import scope_queryset
from issues.detectors import diagnosis_prompts, hba1c_overdue
from pharmacy.services import consume_for_service, shortfall_message

from .forms import (GroupResultForm, LabCategoryForm, LabOrderForm, LabPanelForm, LabTestForm, MonitoringGapForm,
                    SpecimenForm)
from .models import LabCategory, LabOrder, LabPanel, LabTest, MonitoringGap


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
        shortfalls = []
        for o in changed:
            o.save()
            log_event(request=request, action=AuditEvent.Action.STATUS, obj=o, changes={'status': o.status},
                      reason=form.cleaned_data.get('reason', '') if form is not None else '')
            if step == 'result':
                shortfalls += consume_for_service(request.user, lab_order=o, request=request)[1]
        if step == 'result':
            diagnosis_prompts([order.patient_id])
            hba1c_overdue([order.patient_id])
        if shortfalls:
            messages.warning(request, f'Results saved, but stock was short: {shortfall_message(shortfalls)}. '
                                      'Recorded as a stock-out; receive stock in Medicines & stock.')
        messages.success(request, f'{len(changed)} test(s): {self.STEPS[step][1].lower()} done.')
        return redirect(back(request, order))


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

    def get_context_data(self, **kwargs):
        return super().get_context_data(after_template='pharmacy/_consumables.html', target='lab_test',
                                        target_id=self.object.pk,
                                        consumables=self.object.consumables.select_related('item'), **kwargs)

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


class MonitoringListView(PermissionRequiredMixin, View):
    """Diabetes patients by HbA1c status: overdue, due this month, due next month, up to date."""
    permission_required = 'laboratory.view_monitoringgap'
    code = 'hba1c'

    def get(self, request):
        from indicators.views import csv_response
        from patients.models import Patient
        from reporting.filters import ReportFilters

        from .monitoring import STATES, monitoring_list

        filters = ReportFilters(request, with_disease=False)
        patients = Patient.objects.in_scope(request.user).filter(current_facility__in=filters.facilities)
        rows = monitoring_list(patients, self.code)
        counts = {key: 0 for key, _ in STATES}
        explained = 0
        for _, status in rows:
            counts[status['state']] += 1
            explained += status['gap'] is not None
        state = request.GET.get('state', 'overdue')
        if state == 'not_done':
            rows = [r for r in rows if r[1]['gap'] is not None]
        elif state in counts:
            rows = [r for r in rows if r[1]['state'] == state]
        else:
            state = 'all'
        rows.sort(key=lambda r: (r[1]['due'], r[0].patient_number))
        labels = dict(STATES, not_done='Not done recorded', all='All diabetes patients')
        if request.GET.get('format') == 'csv':
            log_event(request=request, action=AuditEvent.Action.EXPORT, model='laboratory.Monitoring',
                      object_repr=f'HbA1c {labels[state]} · {filters.area_label}')
            return csv_response(f'hba1c-{state}-{timezone.localdate()}.csv',
                                ['Patient ID', 'Old ID', 'Sex', 'Age', 'Facility', 'Last HbA1c', 'Last HbA1c date',
                                 'Days since', 'Due date', 'Status', 'Ordered (waiting for result)', 'Not done reason',
                                 'Not done recorded'],
                                [[p.patient_number, p.old_id or '', p.get_sex_display(), p.age, p.current_facility,
                                  s['last'].result_value if s['last'] else '', s['last_date'] or '',
                                  '' if s['days'] is None else s['days'], s['due'], labels[s['state']],
                                  timezone.localtime(s['pending'].ordered_at).date() if s['pending'] else '',
                                  s['gap'].get_reason_display() if s['gap'] else '',
                                  s['gap'].recorded_on if s['gap'] else ''] for p, s in rows])
        return render(request, 'laboratory/monitoring.html', {
            'filters': filters, 'rows': rows, 'counts': counts, 'explained': explained, 'state': state,
            'states': STATES, 'state_label': labels[state], 'total': sum(counts.values()),
            'hidden_filters': {'state': state}, 'hide_pdf': True, 'hide_period': True, 'csv_available': True,
            'qs': filters.querystring(), 'months': settings.HBA1C_INTERVAL_MONTHS,
            'retry_months': settings.HBA1C_RETRY_MONTHS})


class MonitoringGapCreateView(PermissionRequiredMixin, PatientContextMixin, View):
    """Record that the due HbA1c was not done, and why. Closes the overdue data issue for that due date."""
    permission_required = 'laboratory.add_monitoringgap'
    code = 'hba1c'

    def setup_status(self):
        from .monitoring import monitoring_status
        status = monitoring_status(self.patient, self.code)
        if status is None:
            raise Http404('The patient is not enrolled in active diabetes care.')
        return status

    def render_form(self, request, status, form):
        when = f'due {status["due"]:%d %b %Y}' + (f', last result {status["last_date"]:%d %b %Y}'
                                                  if status['last_date'] else ', no result since enrolment')
        return render(request, 'confirm_action.html', {
            'title': f'{status["test"]} not done', 'patient': self.patient, 'form': form, 'button': 'Save',
            'message': f'{status["test"]} {when}. Record why it was not done. The patient stays on the list and is '
                       f'due again {settings.HBA1C_RETRY_MONTHS} month(s) from the date recorded; enter the result as '
                       f'usual once the test is done.',
            'cancel_url': request.GET.get('next') or self.patient.get_absolute_url(), 'next': request.GET.get('next', '')})

    def get(self, request, patient_pk):
        status = self.setup_status()
        return self.render_form(request, status, MonitoringGapForm())

    def post(self, request, patient_pk):
        from issues.detectors import HBA1C_CODE
        from issues.models import DataIssue

        status = self.setup_status()
        form = MonitoringGapForm(request.POST)
        if not form.is_valid():
            return self.render_form(request, status, form)
        test = get_object_or_404(LabTest, code=self.code)
        gap, _ = MonitoringGap.objects.update_or_create(
            patient=self.patient, test=test, due_date=status['due'],
            defaults={**form.cleaned_data, 'recorded_on': timezone.localdate(), 'recorded_by': request.user})
        log_event(request=request, action=AuditEvent.Action.CREATE, obj=gap)
        reason = gap.get_reason_display() + (f': {gap.note}' if gap.note else '')
        DataIssue.objects.open().filter(code=HBA1C_CODE, patient=self.patient).update(
            status=DataIssue.Status.RESOLVED, resolution_note=f'Not done ({reason}), recorded by {request.user}.')
        hba1c_overdue([self.patient.pk])
        messages.success(request, f'Recorded: {status["test"]} not done ({reason}).')
        return redirect(request.POST.get('next') or self.patient.get_absolute_url())

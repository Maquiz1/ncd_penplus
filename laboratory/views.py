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
from core.mixins import AuditedCreateMixin, AuditedUpdateMixin, PatientContextMixin
from facilities.scope import scope_queryset
from issues.detectors import diagnosis_prompts, hba1c_overdue
from pharmacy.services import consume_for_service, shortfall_message

from .forms import (GroupResultForm, LabCategoryForm, LabPanelForm, LabTestForm, MonitoringGapForm,
                    MonitoringRuleForm, SpecimenForm)
from .models import LabCategory, LabOrder, LabPanel, LabTest, MonitoringGap, MonitoringRule


def back(request, obj):
    return request.POST.get('next') or request.GET.get('next') or reverse('laboratory:lab_worklist')


# Worklist tabs: a request is listed under a tab when any of its tests is at that step.
REQUEST_TABS = [
    ('collect', 'To collect', ['ordered']),
    ('results', 'Awaiting results', ['collected']),
    ('review', 'Doctor review', ['resulted']),
    ('completed', 'Completed', ['reviewed']),
    ('cancelled', 'Cancelled', ['cancelled']),
    ('all', 'All', None),
]


def requests_in_scope(user):
    return scope_queryset(LabOrder.objects.all(), user, 'patient__current_facility')


def request_tab_counts(user):
    from django.db.models import Count, Q
    orders = requests_in_scope(user)
    counts = orders.aggregate(**{key: Count('request_group', distinct=True, filter=Q(status__in=statuses))
                                 for key, _l, statuses in REQUEST_TABS if statuses})
    counts['all'] = orders.values('request_group').distinct().count()
    return counts


class LabWorklistView(PermissionRequiredMixin, ListView):
    """Lab requests, one row per request (all tests ordered together), like the GoTHOMIS lab module."""

    permission_required = 'laboratory.view_laborder'
    template_name = 'laboratory/lab_worklist.html'
    paginate_by = 30

    def tab(self):
        key = self.request.GET.get('tab', 'collect')
        return next((t for t in REQUEST_TABS if t[0] == key), REQUEST_TABS[0])

    def orders(self):
        from patients.models import PatientIdentifier
        from django.db.models import Q
        qs = requests_in_scope(self.request.user)
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(Q(patient__patient_number__icontains=q) | Q(specimen_id__iexact=q) | Q(
                patient__identifiers__id_type=PatientIdentifier.IdType.EDCS_STUDY, patient__identifiers__value__iexact=q))
        return qs

    def get_queryset(self):
        from django.db.models import Max, Min
        qs = self.orders()
        statuses = self.tab()[2]
        if statuses:
            qs = qs.filter(request_group__in=qs.filter(status__in=statuses).values('request_group'))
        return qs.values('request_group').annotate(ordered=Min('ordered_at'), last=Max('updated_at')).order_by('-ordered')

    def get_context_data(self, **kwargs):
        from patients.models import with_old_study_id
        ctx = super().get_context_data(**kwargs)
        groups = [row['request_group'] for row in ctx['object_list']]
        orders = with_old_study_id(LabOrder.objects.filter(request_group__in=groups)
                                   .select_related('patient', 'test', 'facility', 'ordered_by', 'encounter'), 'patient')
        by_group = {}
        for o in orders.order_by('test__name'):
            by_group.setdefault(o.request_group, []).append(o)
        counts = request_tab_counts(self.request.user)
        ctx.update(requests=[LabRequest(by_group[g]) for g in groups if g in by_group], tab=self.tab()[0],
                   tabs=[(key, label, counts.get(key)) for key, label, _s in REQUEST_TABS])
        return ctx


class LabRequest:
    """The tests ordered together for one subject, with the request's overall step."""

    STEP_ORDER = ['ordered', 'collected', 'resulted', 'reviewed', 'cancelled']

    def __init__(self, orders):
        self.orders = orders
        self.first = orders[0]
        live = [o for o in orders if o.status != 'cancelled'] or orders
        self.status = min((o.status for o in live), key=self.STEP_ORDER.index)
        self.patient, self.group = self.first.patient, self.first.request_group
        self.abnormal = sum(1 for o in orders if o.is_abnormal)

    @property
    def next_step(self):
        """(url step name, button label, order to act on) for the request's next step, or None."""
        steps = {'ordered': ('collect', 'Collect specimen'), 'collected': ('result', 'Enter results'),
                 'resulted': ('review', 'Doctor review')}
        if self.status not in steps:
            return None
        order = next(o for o in self.orders if o.status == self.status)
        return steps[self.status] + (order,)

    def status_label(self):
        return dict(LabOrder.Status.choices)[self.status]


class LabRequestDetailView(PermissionRequiredMixin, View):
    """One request: subject, tests with values and ranges, and when each step happened and by whom."""

    permission_required = 'laboratory.view_laborder'

    def get(self, request, group):
        from patients.models import with_old_study_id
        orders = list(with_old_study_id(requests_in_scope(request.user).filter(request_group=group)
                                        .select_related('patient__current_facility', 'test', 'facility', 'ordered_by',
                                                        'collected_by', 'resulted_by', 'reviewed_by', 'encounter'),
                                        'patient').order_by('test__name'))
        if not orders:
            raise Http404
        lab_request = LabRequest(orders)
        first = lab_request.first
        events = [(first.ordered_at, 'Requested', first.ordered_by, first.clinical_notes)]
        for attr, label, who in (('collected_at', 'Specimen collected', 'collected_by'),
                                 ('resulted_at', 'Results entered', 'resulted_by'),
                                 ('reviewed_at', 'Confirmed by doctor', 'reviewed_by')):
            done = [o for o in orders if getattr(o, attr)]
            if done:
                last = max(done, key=lambda o: getattr(o, attr))
                events.append((getattr(last, attr), label, getattr(last, who),
                               f'{len(done)} of {len(orders)} test(s)' if len(done) < len(orders) else ''))
        for o in orders:
            if o.status == 'cancelled':
                events.append((o.updated_at, f'{o.test.name} cancelled', None, o.cancel_reason))
        return render(request, 'laboratory/request_detail.html', {
            'req': lab_request, 'patient': first.patient, 'events': sorted(events, key=lambda e: e[0])})


class LabOrderActionView(PermissionRequiredMixin, View):
    """collect → result → review, or cancel. Collect, result and review apply to all tests requested together."""

    permission_required = 'laboratory.change_laborder'

    def has_permission(self):
        # The doctor confirming results needs only the review right, not lab-bench rights.
        if self.kwargs.get('step') == 'review':
            return self.request.user.has_perm('laboratory.review_laborder')
        return super().has_permission()
    STEPS = {
        'collect': (('ordered',), 'Record specimen collection'),
        'result': (('collected',), 'Enter results'),
        'review': (('resulted',), 'Review results (doctor confirmation)'),
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
        if step in ('collect', 'result', 'review'):
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
            for o in orders:
                o.reviewed_by, o.reviewed_at, o.status = request.user, now, LabOrder.Status.REVIEWED
                changed.append(o)
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
        from patients.models import Patient, with_old_study_id
        from reporting.filters import ReportFilters

        from .monitoring import STATES, get_rule, monitoring_list

        rule = get_rule(self.code)
        filters = ReportFilters(request, with_disease=False)
        patients = with_old_study_id(Patient.objects.in_scope(request.user)
                                     .filter(current_facility__in=filters.facilities))
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
        labels = dict(STATES, not_done='Not done recorded', all='All diabetes subjects')
        if request.GET.get('format') == 'csv':
            log_event(request=request, action=AuditEvent.Action.EXPORT, model='laboratory.Monitoring',
                      object_repr=f'HbA1c {labels[state]} · {filters.area_label}')
            return csv_response(f'hba1c-{state}-{timezone.localdate()}.csv',
                                ['Subject ID', 'Old ID', 'Sex', 'Age', 'Facility', 'Last HbA1c', 'Last HbA1c date',
                                 'Days since', 'Due date', 'Status', 'Ordered (waiting for result)', 'Not done reason',
                                 'Not done recorded'],
                                [[p.patient_number, p.old_study_id or p.old_id or '', p.get_sex_display(), p.age, p.current_facility,
                                  s['last'].result_value if s['last'] else '', s['last_date'] or '',
                                  '' if s['days'] is None else s['days'], s['due'], labels[s['state']],
                                  timezone.localtime(s['pending'].ordered_at).date() if s['pending'] else '',
                                  s['gap'].get_reason_display() if s['gap'] else '',
                                  s['gap'].recorded_on if s['gap'] else ''] for p, s in rows])
        return render(request, 'laboratory/monitoring.html', {
            'filters': filters, 'rows': rows, 'counts': counts, 'explained': explained, 'state': state,
            'states': STATES, 'state_label': labels[state], 'total': sum(counts.values()),
            'hidden_filters': {'state': state}, 'hide_pdf': True, 'hide_period': True, 'csv_available': True,
            'qs': filters.querystring(), 'rule': rule})


class MonitoringGapCreateView(PermissionRequiredMixin, PatientContextMixin, View):
    """Record that the due HbA1c was not done, and why. Closes the overdue data issue for that due date."""
    permission_required = 'laboratory.add_monitoringgap'
    code = 'hba1c'

    def setup_status(self):
        from .monitoring import monitoring_status
        status = monitoring_status(self.patient, self.code)
        if status is None:
            raise Http404('The subject is not enrolled in active diabetes care.')
        return status

    def render_form(self, request, status, form):
        when = f'due {status["due"]:%d %b %Y}' + (f', last result {status["last_date"]:%d %b %Y}'
                                                  if status['last_date'] else ', no result since enrolment')
        return render(request, 'confirm_action.html', {
            'title': f'{status["test"]} not done', 'patient': self.patient, 'form': form, 'button': 'Save',
            'message': f'{status["test"]} {when}. Record why it was not done. The subject stays on the list and is '
                       f'due again {status["retry_months"]} month(s) from the date recorded; enter the result as '
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


class MonitoringRuleListView(PermissionRequiredMixin, View):
    """Administration → Monitoring settings: how often each routine test is due."""
    permission_required = 'laboratory.view_monitoringrule'

    def get(self, request):
        from .monitoring import DEFAULTS, get_rule

        for code in DEFAULTS:
            get_rule(code)  # make sure the default rules exist
        return render(request, 'laboratory/monitoring_rules.html',
                      {'rules': MonitoringRule.objects.select_related('test', 'disease', 'updated_by')})


class MonitoringRuleUpdateView(PermissionRequiredMixin, UpdateView):
    permission_required = 'laboratory.change_monitoringrule'
    model = MonitoringRule
    form_class = MonitoringRuleForm
    template_name = 'generic_form.html'

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            title=f'{self.object.test.name} monitoring', cancel_url=reverse('laboratory:monitoring_rules'),
            subtitle='Changes apply straight away to the due list, the subject banner and the overdue data issues.',
            **kwargs)

    def form_valid(self, form):
        rule = form.save(commit=False)
        rule.updated_by = self.request.user
        rule.save()
        log_event(request=self.request, action=AuditEvent.Action.UPDATE, obj=rule,
                  changes={f: str(form.cleaned_data[f]) for f in form.changed_data})
        if rule.test.code == 'hba1c' and form.changed_data:
            hba1c_overdue()  # re-flag patients with the new interval
        messages.success(self.request, f'Saved: {rule}.')
        return redirect('laboratory:monitoring_rules')

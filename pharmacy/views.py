import datetime

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from core.forms import ReasonForm
from core.mixins import AuditedCreateMixin, AuditedUpdateMixin, PatientChildCreateMixin, ScopedQuerysetMixin
from facilities.scope import facilities_for_user, scope_queryset

from .forms import AdjustStockForm, ConsumableUseForm, DispenseForm, MedicationForm, PrescriptionForm, ReceiveStockForm
from .models import (EXPIRY_WARNING_DAYS, ConsumableUse, Medication, Prescription, StockBatch, StockMovement,
                     StockShortfall)
from .services import (StockError, adjust_stock, available_batches, cancel_prescription, dispense,
                       low_stock_rows, medications_with_stock, receive_stock)


def selected_facilities(request):
    facilities = facilities_for_user(request.user)
    if request.GET.get('facility'):
        facilities = facilities.filter(pk=request.GET['facility'])
    return facilities


class MedicationListView(PermissionRequiredMixin, ListView):
    permission_required = 'pharmacy.view_medication'
    template_name = 'pharmacy/medication_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = medications_with_stock(selected_facilities(self.request))
        if self.request.GET.get('q'):
            qs = qs.filter(name__icontains=self.request.GET['q'])
        if self.request.GET.get('show') != 'all':
            qs = qs.filter(is_active=True)
        if self.request.GET.get('type'):
            qs = qs.filter(item_type=self.request.GET['type'])
        return qs.order_by('item_type', 'name', 'strength')

    def get_context_data(self, **kwargs):
        open_shortfalls = scope_queryset(StockShortfall.objects.filter(resolved=False), self.request.user, 'facility')
        return super().get_context_data(facilities=facilities_for_user(self.request.user),
                                        item_types=Medication.ItemType.choices,
                                        open_shortfalls=open_shortfalls.count(),
                                        low_stock=low_stock_rows(list(selected_facilities(self.request))), **kwargs)


class MedicationCreateView(PermissionRequiredMixin, AuditedCreateMixin, CreateView):
    permission_required = 'pharmacy.add_medication'
    model = Medication
    form_class = MedicationForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Add medication to catalogue'}

    def get_success_url(self):
        return reverse('pharmacy:medication_list')


class MedicationUpdateView(PermissionRequiredMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'pharmacy.change_medication'
    model = Medication
    form_class = MedicationForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Edit medication'}

    def get_success_url(self):
        return reverse('pharmacy:medication_list')


class StockListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'pharmacy.view_stockbatch'
    model = StockBatch
    scope_field = 'facility'
    template_name = 'pharmacy/stock_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('medication', 'facility')
        params = self.request.GET
        today = timezone.localdate()
        view = params.get('view', 'in_stock')
        if view == 'in_stock':
            qs = qs.filter(quantity_on_hand__gt=0, expiry_date__gte=today)
        elif view == 'expiring':
            qs = qs.filter(quantity_on_hand__gt=0, expiry_date__gte=today,
                           expiry_date__lte=today + datetime.timedelta(days=EXPIRY_WARNING_DAYS))
        elif view == 'expired':
            qs = qs.filter(quantity_on_hand__gt=0, expiry_date__lt=today)
        if params.get('facility'):
            qs = qs.filter(facility_id=params['facility'])
        if params.get('medication'):
            qs = qs.filter(medication_id=params['medication'])
        return qs.order_by('expiry_date', 'medication__name')

    def get_context_data(self, **kwargs):
        return super().get_context_data(view=self.request.GET.get('view', 'in_stock'),
                                        facilities=facilities_for_user(self.request.user),
                                        medications=Medication.objects.filter(is_active=True),
                                        warning_days=EXPIRY_WARNING_DAYS, **kwargs)


class ReceiveStockView(PermissionRequiredMixin, View):
    permission_required = 'pharmacy.add_stockbatch'

    def render_form(self, request, form):
        return render(request, 'generic_form.html', {
            'title': 'Receive stock', 'form': form, 'cancel_url': reverse('pharmacy:stock_list'),
            'subtitle': 'Adds a new batch, or more units to an existing batch with the same number and expiry.'})

    def get(self, request):
        initial = {'medication': request.GET.get('medication'), 'facility': request.GET.get('facility')}
        return self.render_form(request, ReceiveStockForm(user=request.user, initial=initial))

    def post(self, request):
        form = ReceiveStockForm(request.POST, user=request.user)
        if not form.is_valid():
            return self.render_form(request, form)
        d = form.cleaned_data
        try:
            batch = receive_stock(d['medication'], d['facility'], d['batch_number'].strip(), d['expiry_date'],
                                  d['quantity'], request.user, d['supplier'], d['received_date'], request)
        except StockError as exc:
            form.add_error(None, str(exc))
            return self.render_form(request, form)
        messages.success(request, f'Received {d["quantity"]} into batch {batch.batch_number}. '
                                  f'Balance {batch.quantity_on_hand}.')
        return redirect(reverse('pharmacy:stock_list') + f'?facility={batch.facility_id}')


class AdjustStockView(PermissionRequiredMixin, View):
    permission_required = 'pharmacy.adjust_stock'

    def get_batch(self, request, pk):
        return get_object_or_404(scope_queryset(StockBatch.objects.select_related('medication', 'facility'),
                                                request.user, 'facility'), pk=pk)

    def render_form(self, request, batch, form):
        return render(request, 'confirm_action.html', {
            'title': 'Adjust stock', 'form': form, 'button': 'Save adjustment',
            'message': f'{batch} at {batch.facility}.', 'cancel_url': reverse('pharmacy:stock_list')})

    def get(self, request, pk):
        batch = self.get_batch(request, pk)
        initial = {'kind': 'write_off', 'quantity': batch.quantity_on_hand} if batch.is_expired else {}
        return self.render_form(request, batch, AdjustStockForm(initial=initial))

    def post(self, request, pk):
        batch = self.get_batch(request, pk)
        form = AdjustStockForm(request.POST)
        if not form.is_valid():
            return self.render_form(request, batch, form)
        try:
            adjust_stock(batch, form.signed_quantity(), form.cleaned_data['reason'], request.user,
                         write_off=form.cleaned_data['kind'] == 'write_off', request=request)
        except StockError as exc:
            form.add_error('quantity', str(exc))
            return self.render_form(request, batch, form)
        messages.success(request, 'Stock adjusted and recorded in the stock ledger.')
        return redirect(reverse('pharmacy:movement_list') + f'?batch={batch.pk}')


class MovementListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'pharmacy.view_stockmovement'
    model = StockMovement
    scope_field = 'batch__facility'
    template_name = 'pharmacy/movement_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('batch__medication', 'batch__facility', 'performed_by',
                                                   'dispensing__prescription__patient')
        for key, field in (('batch', 'batch_id'), ('type', 'movement_type'), ('medication', 'batch__medication_id'),
                           ('facility', 'batch__facility_id')):
            if self.request.GET.get(key):
                qs = qs.filter(**{field: self.request.GET[key]})
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(types=StockMovement.MovementType.choices,
                                        facilities=facilities_for_user(self.request.user), **kwargs)


class PrescriptionCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'pharmacy.add_prescription'
    model = Prescription
    form_class = PrescriptionForm
    title = 'Prescribe medication'
    success_tab = '#medications'

    def get_initial(self):
        initial = super().get_initial()
        initial['encounter'] = self.request.GET.get('encounter')
        return initial

    def form_valid(self, form):
        if self.patient.is_deceased:
            form.add_error(None, 'Cannot prescribe for a deceased patient.')
            return self.form_invalid(form)
        form.instance.prescriber = self.request.user
        response = super().form_valid(form)
        if not available_batches(self.object.medication, self.object.facility).exists():
            messages.warning(self.request, f'{self.object.medication} is currently out of stock at {self.object.facility}.')
        return response

    def get_success_url(self):
        return self.object.encounter.get_absolute_url() if self.object.encounter else super().get_success_url()


class PrescriptionListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    """Dispensing worklist for the facilities in the user's scope."""

    permission_required = 'pharmacy.view_prescription'
    model = Prescription
    scope_field = 'facility'
    template_name = 'pharmacy/prescription_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('patient', 'medication', 'facility', 'prescriber')
        status = self.request.GET.get('status', 'open')
        if status == 'open':
            qs = qs.filter(status__in=Prescription.OPEN_STATUSES)
        elif status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(statuses=Prescription.Status.choices,
                                        current_status=self.request.GET.get('status', 'open'), **kwargs)


class DispenseView(PermissionRequiredMixin, View):
    permission_required = 'pharmacy.add_dispensing'
    template_name = 'pharmacy/dispense.html'

    def get_prescription(self, request, pk):
        return get_object_or_404(scope_queryset(
            Prescription.objects.select_related('patient', 'medication', 'facility', 'prescriber'),
            request.user, 'facility'), pk=pk)

    def render_form(self, request, prescription, form):
        return render(request, self.template_name, {
            'prescription': prescription, 'patient': prescription.patient, 'form': form,
            'dispensings': prescription.dispensings.select_related('batch', 'dispensed_by'),
            'next': request.GET.get('next') or request.POST.get('next') or reverse('pharmacy:prescription_list')})

    def get(self, request, pk):
        prescription = self.get_prescription(request, pk)
        first = available_batches(prescription.medication, prescription.facility).first()
        initial = {'batch': first, 'quantity': min(prescription.quantity_remaining, first.quantity_on_hand)} if first else {}
        return self.render_form(request, prescription, DispenseForm(prescription=prescription, initial=initial))

    def post(self, request, pk):
        prescription = self.get_prescription(request, pk)
        form = DispenseForm(request.POST, prescription=prescription)
        if not form.is_valid():
            return self.render_form(request, prescription, form)
        try:
            dispense(prescription, form.cleaned_data['batch'], form.cleaned_data['quantity'], request.user,
                     form.cleaned_data['counselling_notes'], request)
        except StockError as exc:
            form.add_error(None, str(exc))
            return self.render_form(request, prescription, form)
        prescription.refresh_from_db()
        messages.success(request, f'Dispensed. Prescription is now {prescription.get_status_display().lower()}.')
        if prescription.is_open:
            return redirect(reverse('pharmacy:dispense', args=[prescription.pk]))
        return redirect(request.POST.get('next') or reverse('pharmacy:prescription_list'))


class PrescriptionCancelView(PermissionRequiredMixin, View):
    permission_required = 'pharmacy.change_prescription'

    def get_prescription(self, request, pk):
        return get_object_or_404(scope_queryset(Prescription.objects.select_related('patient', 'medication'),
                                                request.user, 'patient__current_facility'), pk=pk)

    def render_form(self, request, prescription, form):
        return render(request, 'confirm_action.html', {
            'title': 'Cancel prescription', 'patient': prescription.patient, 'form': form,
            'message': f'{prescription} (quantity {prescription.quantity}). Units already dispensed stay recorded.',
            'button': 'Cancel prescription', 'button_class': 'btn-danger',
            'cancel_url': prescription.patient.get_absolute_url() + '#medications'})

    def get(self, request, pk):
        return self.render_form(request, self.get_prescription(request, pk), ReasonForm())

    def post(self, request, pk):
        prescription = self.get_prescription(request, pk)
        form = ReasonForm(request.POST)
        if not form.is_valid():
            return self.render_form(request, prescription, form)
        try:
            cancel_prescription(prescription, form.cleaned_data['reason'], request.user, request)
            messages.success(request, 'Prescription cancelled.')
        except StockError as exc:
            messages.error(request, str(exc))
        return redirect(prescription.patient.get_absolute_url() + '#medications')


class ConsumableTargetMixin(PermissionRequiredMixin):
    """Consumables belong to a lab test (?lab_test=) or an investigation type (?investigation_type=)."""

    permission_required = 'pharmacy.change_consumableuse'

    def target(self, request):
        from investigations.models import InvestigationType
        from laboratory.models import LabTest
        if request.GET.get('lab_test'):
            test = get_object_or_404(LabTest, pk=request.GET['lab_test'])
            return {'lab_test': test}, test, reverse('laboratory:test_update', args=[test.pk])
        itype = get_object_or_404(InvestigationType, pk=request.GET.get('investigation_type'))
        return {'investigation_type': itype}, itype, reverse('investigations:type_detail', args=[itype.pk])


class ConsumableCreateView(ConsumableTargetMixin, View):
    permission_required = 'pharmacy.add_consumableuse'

    def get(self, request, form=None):
        _link, target, back_url = self.target(request)
        return render(request, 'generic_form.html', {
            'form': form or ConsumableUseForm(), 'title': f'Stock used per {target}', 'cancel_url': back_url,
            'subtitle': 'Deducted from the facility stock (first expiry first out) each time a result is entered.'})

    def post(self, request):
        link, target, back_url = self.target(request)
        form = ConsumableUseForm(request.POST)
        if not form.is_valid():
            return self.get(request, form)
        rule, created = ConsumableUse.objects.update_or_create(item=form.cleaned_data['item'], **link, defaults={
            'quantity': form.cleaned_data['quantity'], 'is_active': True})
        log_event(request=request, action=AuditEvent.Action.CREATE if created else AuditEvent.Action.UPDATE, obj=rule)
        messages.success(request, f'{rule} saved.')
        return redirect(back_url)


class ConsumableRemoveView(PermissionRequiredMixin, View):
    permission_required = 'pharmacy.change_consumableuse'

    def post(self, request, pk):
        rule = get_object_or_404(ConsumableUse, pk=pk)
        rule.is_active = not rule.is_active
        rule.save(update_fields=['is_active'])
        log_event(request=request, action=AuditEvent.Action.UPDATE, obj=rule, changes={'is_active': rule.is_active})
        return redirect(request.POST.get('next') or reverse('pharmacy:medication_list'))


class ShortfallListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    """Stock-out events: tests and investigations done without enough consumables."""

    permission_required = 'pharmacy.view_stockshortfall'
    model = StockShortfall
    template_name = 'pharmacy/shortfall_list.html'
    paginate_by = 50
    scope_field = 'facility'

    def get_queryset(self):
        qs = super().get_queryset().select_related('item', 'facility', 'lab_order__test', 'investigation__investigation_type')
        if self.request.GET.get('show') != 'all':
            qs = qs.filter(resolved=False)
        return qs


class ShortfallResolveView(PermissionRequiredMixin, View):
    permission_required = 'pharmacy.change_stockshortfall'

    def post(self, request, pk):
        shortfall = get_object_or_404(scope_queryset(StockShortfall.objects.all(), request.user, 'facility'), pk=pk)
        shortfall.resolved, shortfall.resolution_note = True, request.POST.get('note', '')[:255] or 'Restocked'
        shortfall.save(update_fields=['resolved', 'resolution_note'])
        log_event(request=request, action=AuditEvent.Action.STATUS, obj=shortfall, reason=shortfall.resolution_note)
        return redirect('pharmacy:shortfall_list')

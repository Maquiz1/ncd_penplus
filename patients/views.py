from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from core.forms import ReasonForm
from core.mixins import AuditedUpdateMixin, PatientChildCreateMixin
from facilities.scope import default_facility, facilities_for_user

from .forms import PatientForm, PatientIdentifierForm, PatientTransferForm, RecordDeathForm, TransferActionForm
from .models import Patient, PatientIdentifier, PatientTransfer, with_old_study_id
from .services import apply_transfer_action, record_death, register_patient


def search_patients(queryset, query, can_view_identity):
    """Search by patient ID/identifiers; names and phones only when identity access is granted."""
    condition = Q(patient_number__icontains=query)
    if query.isdigit():
        condition |= Q(old_id=int(query))
    # The full old EDCS study ID (e.g. 1-001) is a study code like the patient number, not an identity field.
    condition |= Q(identifiers__id_type=PatientIdentifier.IdType.EDCS_STUDY, identifiers__value__iexact=query)
    if can_view_identity:
        condition |= Q(identifiers__value__iexact=query)
        for term in query.split():
            condition |= Q(first_name__icontains=term) | Q(last_name__icontains=term) | Q(middle_name__icontains=term)
        condition |= Q(phone__icontains=query) | Q(alternate_phone__icontains=query)
    return queryset.filter(condition).distinct()


class PatientListView(PermissionRequiredMixin, ListView):
    permission_required = 'patients.view_patient'
    template_name = 'patients/patient_list.html'
    paginate_by = 25

    def get_queryset(self):
        qs = with_old_study_id(Patient.objects.in_scope(self.request.user).select_related('current_facility'))
        params = self.request.GET
        if params.get('q'):
            qs = search_patients(qs, params['q'].strip(), self.request.user.has_perm('patients.view_patient_identity'))
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('facility'):
            qs = qs.filter(current_facility_id=params['facility'])
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(facilities=facilities_for_user(self.request.user),
                                        statuses=Patient.Status.choices, **kwargs)


class PatientCreateView(PermissionRequiredMixin, CreateView):
    permission_required = ('patients.add_patient', 'patients.view_patient_identity')
    form_class = PatientForm
    template_name = 'patients/patient_form.html'

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'user': self.request.user}

    def get_initial(self):
        return {'registration_facility': default_facility(self.request.user)}

    def form_valid(self, form):
        patient = register_patient(form.save(commit=False), self.request.user)
        self.object = patient
        log_event(request=self.request, action=AuditEvent.Action.CREATE, obj=patient,
                  reason='Registered after duplicate review' if form.duplicates else '')
        messages.success(self.request, f'Subject registered with ID {patient.patient_number}.')
        return redirect(patient.get_absolute_url())


class PatientDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'patients.view_patient'
    template_name = 'patients/patient_detail.html'

    def get_queryset(self):
        return Patient.objects.in_scope(self.request.user).select_related(
            'current_facility', 'registration_facility', 'residence_district__region', 'residence_ward', 'residence_street')

    def get_context_data(self, **kwargs):
        from clinical.flow import patient_flow
        from crf.services import applicable_versions

        p = self.object
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            patient=p,
            identifiers=p.identifiers.select_related('issuer'),
            prescreenings=p.prescreenings.select_related('facility'),
            screenings=p.screenings.select_related('disease', 'facility', 'screened_by'),
            diagnoses=p.diagnoses.select_related('disease', 'clinician'),
            programme_enrollments=p.programme_enrollments.select_related('programme__disease', 'facility')
            .prefetch_related('events'),
            returnable_statuses=('ltfu', 'transferred_out', 'withdrawn', 'other', 'completed'),
            eligibility=p.study_eligibility.select_related('study'),
            consents=p.consents.select_related('study'),
            study_enrollments=p.study_enrollments.select_related('study', 'facility'),
            appointments=p.appointments.select_related('facility', 'plan').order_by('scheduled_date')[:100],
            plans=p.followup_plans.select_related('facility'),
            encounters=p.encounters.select_related('facility', 'staff').order_by('-encounter_date')[:50],
            admissions=p.admissions.select_related('facility'),
            family_members=p.family_members.all(),
            lab_orders=p.lab_orders.select_related('test', 'facility').order_by('-ordered_at'),
            investigations=p.investigations.select_related('facility', 'investigation_type').order_by('-requested_at'),
            submissions=p.form_submissions.select_related('version__definition', 'encounter').order_by('-created_at'),
            medication_records=p.medication_records.select_related('encounter')[:200],
            prescriptions=p.prescriptions.select_related('medication', 'facility', 'prescriber')
            .prefetch_related('dispensings__batch'),
            transfers=p.transfers.select_related('from_facility', 'to_facility'),
            assignments=p.facility_assignments.select_related('facility'),
            available_forms=applicable_versions(p),
            open_transfer=p.transfers.filter(status__in=['requested', 'approved', 'sent']).first(),
            flow=patient_flow(p),
        )
        return ctx


class PatientUpdateView(PermissionRequiredMixin, AuditedUpdateMixin, UpdateView):
    permission_required = ('patients.change_patient', 'patients.view_patient_identity')
    form_class = PatientForm
    template_name = 'patients/patient_form.html'

    def get_queryset(self):
        return Patient.objects.in_scope(self.request.user)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'user': self.request.user}


class PatientSoftDeleteView(PermissionRequiredMixin, View):
    permission_required = 'patients.delete_patient'

    def get(self, request, pk):
        patient = get_object_or_404(Patient.objects.in_scope(request.user), pk=pk)
        return render(request, 'confirm_action.html', {
            'title': f'Delete subject {patient}', 'patient': patient, 'form': ReasonForm(),
            'message': 'The record is hidden from routine lists but kept with its full history. '
                       'Authorized users can restore it.',
            'button': 'Delete record', 'button_class': 'btn-danger', 'cancel_url': patient.get_absolute_url()})

    def post(self, request, pk):
        patient = get_object_or_404(Patient.objects.in_scope(request.user), pk=pk)
        form = ReasonForm(request.POST)
        if not form.is_valid():
            return render(request, 'confirm_action.html', {'title': f'Delete subject {patient}', 'form': form,
                                                           'patient': patient, 'button': 'Delete record',
                                                           'button_class': 'btn-danger',
                                                           'cancel_url': patient.get_absolute_url()})
        patient.soft_delete(request.user, form.cleaned_data['reason'])
        log_event(request=request, action=AuditEvent.Action.DELETE, obj=patient, reason=form.cleaned_data['reason'])
        messages.success(request, f'Subject {patient} deleted. It can be restored from Deleted subjects.')
        return redirect('patients:list')


class DeletedPatientListView(PermissionRequiredMixin, ListView):
    permission_required = 'patients.restore_patient'
    template_name = 'patients/deleted_list.html'
    paginate_by = 25

    def get_queryset(self):
        return with_old_study_id(Patient.all_objects.in_scope(self.request.user).filter(is_deleted=True).select_related(
            'current_facility', 'deleted_by'))


class PatientRestoreView(PermissionRequiredMixin, View):
    permission_required = 'patients.restore_patient'

    def post(self, request, pk):
        patient = get_object_or_404(Patient.all_objects.in_scope(request.user), pk=pk, is_deleted=True)
        patient.restore()
        log_event(request=request, action=AuditEvent.Action.RESTORE, obj=patient)
        messages.success(request, f'Subject {patient} restored.')
        return redirect(patient.get_absolute_url())


class PatientRecordDeathView(PermissionRequiredMixin, View):
    permission_required = 'patients.record_death'

    def get_patient(self, request, pk):
        return get_object_or_404(Patient.objects.in_scope(request.user), pk=pk, vital_status='alive')

    def render_form(self, request, patient, form):
        return render(request, 'confirm_action.html', {
            'title': 'Record death', 'patient': patient, 'form': form, 'button': 'Record death',
            'button_class': 'btn-danger', 'cancel_url': patient.get_absolute_url(),
            'message': 'Active programme and study enrollments will be closed as deceased, and future '
                       'appointments and undispensed prescriptions cancelled.'})

    def get(self, request, pk):
        return self.render_form(request, self.get_patient(request, pk), RecordDeathForm())

    def post(self, request, pk):
        patient = self.get_patient(request, pk)
        form = RecordDeathForm(request.POST)
        if not form.is_valid():
            return self.render_form(request, patient, form)
        record_death(patient, form.cleaned_data['date_of_death'], form.cleaned_data['cause'], request.user, request)
        messages.success(request, 'Death recorded.')
        return redirect(patient.get_absolute_url())


class IdentifierCreateView(PatientChildCreateMixin, CreateView):
    permission_required = ('patients.add_patientidentifier', 'patients.view_patient_identity')
    model = PatientIdentifier
    form_class = PatientIdentifierForm
    title = 'Add identifier'


class TransferCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'patients.add_patienttransfer'
    model = PatientTransfer
    form_class = PatientTransferForm
    title = 'Request transfer out'
    success_tab = '#transfers'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        from facilities.models import Facility
        form.fields['to_facility'].queryset = Facility.objects.filter(is_active=True).exclude(
            pk=self.patient.current_facility_id)
        return form

    def form_valid(self, form):
        if self.patient.transfers.filter(status__in=['requested', 'approved', 'sent']).exists():
            messages.error(self.request, 'This subject already has an open transfer.')
            return redirect(self.patient.get_absolute_url())
        form.instance.from_facility = self.patient.current_facility
        return super().form_valid(form)


class TransferListView(PermissionRequiredMixin, ListView):
    permission_required = 'patients.view_patienttransfer'
    template_name = 'patients/transfer_list.html'
    paginate_by = 25

    def get_queryset(self):
        facilities = facilities_for_user(self.request.user)
        qs = PatientTransfer.objects.select_related('patient', 'from_facility', 'to_facility')
        direction = self.request.GET.get('direction', 'all')
        if direction == 'in':
            qs = qs.filter(to_facility__in=facilities)
        elif direction == 'out':
            qs = qs.filter(from_facility__in=facilities)
        else:
            qs = qs.filter(Q(to_facility__in=facilities) | Q(from_facility__in=facilities))
        if self.request.GET.get('status'):
            qs = qs.filter(status=self.request.GET['status'])
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(statuses=PatientTransfer.Status.choices,
                                        my_facility_ids=set(facilities_for_user(self.request.user).values_list('pk', flat=True)),
                                        **kwargs)


class TransferActionView(PermissionRequiredMixin, View):
    permission_required = 'patients.change_patienttransfer'

    def dispatch(self, request, *args, **kwargs):
        if kwargs['action'] not in PatientTransfer.TRANSITIONS:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_transfer(self, request, pk, action):
        transfer = get_object_or_404(PatientTransfer.objects.select_related('patient', 'from_facility', 'to_facility'), pk=pk)
        side = PatientTransfer.TRANSITIONS[action][2]
        facility = transfer.from_facility if side == 'from' else transfer.to_facility
        if not facilities_for_user(request.user).filter(pk=facility.pk).exists():
            raise PermissionDenied('Only staff at the %s facility can %s this transfer.' % (
                'sending' if side == 'from' else 'receiving', action))
        return transfer

    def get(self, request, pk, action):
        transfer = self.get_transfer(request, pk, action)
        return render(request, 'confirm_action.html', {
            'title': f'{action.title()} transfer', 'form': TransferActionForm(), 'button': action.title(),
            'message': f'{transfer.patient}: {transfer.from_facility} → {transfer.to_facility}',
            'cancel_url': reverse('patients:transfers')})

    def post(self, request, pk, action):
        transfer = self.get_transfer(request, pk, action)
        form = TransferActionForm(request.POST)
        form.is_valid()
        try:
            apply_transfer_action(transfer, action, request.user, request, form.cleaned_data.get('notes', ''))
            messages.success(request, f'Transfer {PatientTransfer.TRANSITIONS[action][1]}.')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('patients:transfers')


class PossibleDuplicatesView(PermissionRequiredMixin, View):
    """Groups of patients sharing an identifier value or name + date of birth, for manual review."""

    permission_required = ('patients.change_patient', 'patients.view_patient_identity')

    def get(self, request):
        from django.db.models import Count

        patients = Patient.objects.in_scope(request.user)
        groups = []
        shared = (PatientIdentifier.objects.filter(patient__in=patients).values('id_type', 'value')
                  .annotate(n=Count('patient', distinct=True)).filter(n__gt=1).order_by('-n', 'value'))
        for row in shared[:200]:
            members = patients.filter(identifiers__id_type=row['id_type'], identifiers__value=row['value']).distinct()
            label = dict(PatientIdentifier.IdType.choices).get(row['id_type'], row['id_type'])
            groups.append((f'Same {label}: {row["value"]}', members.select_related('current_facility')))
        same_person = (patients.values('first_name', 'last_name', 'date_of_birth').annotate(n=Count('id'))
                       .filter(n__gt=1).order_by('last_name'))
        for row in same_person[:200]:
            members = patients.filter(first_name=row['first_name'], last_name=row['last_name'],
                                      date_of_birth=row['date_of_birth'])
            groups.append((f'Same name and date of birth: {row["first_name"]} {row["last_name"]}, '
                           f'{row["date_of_birth"]:%d %b %Y}', members.select_related('current_facility')))
        return render(request, 'patients/duplicates.html', {'groups': groups})


class SmsConsentView(PermissionRequiredMixin, View):
    """Record the patient's SMS consent answer from any patient page (asked at the visit)."""

    permission_required = ('patients.record_sms_consent', 'patients.view_patient_identity')

    def post(self, request, pk):
        from django.utils import timezone
        patient = get_object_or_404(Patient.objects.in_scope(request.user), pk=pk)
        answer = {'yes': True, 'no': False}.get(request.POST.get('answer'))
        if answer is None:
            raise PermissionDenied
        old = patient.sms_consent
        patient.sms_consent, patient.sms_consent_recorded_at = answer, timezone.now()
        patient.save(update_fields=['sms_consent', 'sms_consent_recorded_at', 'updated_at'])
        log_event(request=request, action=AuditEvent.Action.UPDATE, obj=patient,
                  changes={'sms_consent': [old, answer]}, reason='SMS consent recorded at visit')
        messages.success(request, 'SMS consent recorded: ' + ('Yes.' if answer else 'No – no SMS will be sent.'))
        return redirect(request.POST.get('next') or patient.get_absolute_url())


class SummaryListView(PermissionRequiredMixin, ListView):
    """Find subjects by ID and open their summary. Names are never shown or searched here."""

    permission_required = 'patients.view_subject_summary'
    template_name = 'patients/summary_list.html'
    paginate_by = 25

    def get_queryset(self):
        from django.db.models import OuterRef, Subquery
        from django.utils import timezone

        from clinical.models import ClinicalProgramme
        from encounters.models import Appointment, Encounter

        qs = with_old_study_id(Patient.objects.in_scope(self.request.user).select_related('current_facility'))
        params = self.request.GET
        if params.get('q'):
            qs = search_patients(qs, params['q'].strip(), can_view_identity=False)
        if params.get('facility'):
            qs = qs.filter(current_facility_id=params['facility'])
        if params.get('programme'):
            qs = qs.filter(programme_enrollments__programme_id=params['programme'])
        if params.get('status'):
            qs = qs.filter(programme_enrollments__status=params['status'])
        self.programmes = ClinicalProgramme.objects.select_related('disease')
        today = timezone.localdate()
        last_visit = Encounter.objects.filter(patient=OuterRef('pk')).exclude(status='cancelled') \
            .order_by('-encounter_date').values('encounter_date')[:1]
        next_visit = Appointment.objects.filter(patient=OuterRef('pk'), scheduled_date__gte=today,
                                                status__in=['scheduled', 'confirmed', 'rescheduled']) \
            .order_by('scheduled_date').values('scheduled_date')[:1]
        return (qs.distinct().prefetch_related('programme_enrollments__programme')
                .annotate(last_visit=Subquery(last_visit), next_visit=Subquery(next_visit))
                .order_by('-registration_date', '-pk'))

    def get_context_data(self, **kwargs):
        from clinical.models import EnrollmentStatus
        return super().get_context_data(facilities=facilities_for_user(self.request.user), programmes=self.programmes,
                                        statuses=EnrollmentStatus.choices, **kwargs)


class SubjectSummaryView(PermissionRequiredMixin, DetailView):
    """One subject on one page, by subject ID only: for investigators, coordinators and specialists."""

    permission_required = 'patients.view_subject_summary'
    template_name = 'patients/summary.html'
    context_object_name = 'subject'

    def get_queryset(self):
        return with_old_study_id(Patient.objects.in_scope(self.request.user)
                                 .select_related('current_facility', 'residence_district__region'))

    def get_context_data(self, **kwargs):
        from .summary import subject_summary
        ctx = super().get_context_data(**kwargs)
        ctx.update(subject_summary(self.object))
        return ctx

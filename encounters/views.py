import datetime

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from core.mixins import (AuditedCreateMixin, AuditedUpdateMixin, PatientChildCreateMixin, PatientChildUpdateMixin,
                         ScopedQuerysetMixin)
from core.models import WorkflowStage
from facilities.scope import facilities_for_user, scope_queryset

from .forms import (AdmissionForm, AppointmentForm, AppointmentStatusForm, ClinicSessionForm, DischargeForm,
                    EncounterForm, FollowUpPlanForm, VitalsForm)
from .models import Admission, Appointment, ClinicSession, Encounter, FollowUpPlan, Vitals
from .services import generate_visits, revise_plan


class AppointmentListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'encounters.view_appointment'
    model = Appointment
    template_name = 'encounters/appointment_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('patient', 'facility')
        params = self.request.GET
        today = timezone.localdate()
        view = params.get('view', 'upcoming')
        if view == 'today':
            qs = qs.filter(scheduled_date=today)
        elif view == 'upcoming':
            qs = qs.filter(scheduled_date__gte=today, status__in=['scheduled', 'confirmed', 'rescheduled'])
        elif view == 'overdue':
            qs = qs.filter(scheduled_date__lt=today, status__in=['scheduled', 'confirmed', 'rescheduled'])
        elif view == 'reconcile':
            qs = qs.filter(status='expected')
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('facility'):
            qs = qs.filter(facility_id=params['facility'])
        return qs.order_by('scheduled_date', 'scheduled_time')

    def get_context_data(self, **kwargs):
        return super().get_context_data(view=self.request.GET.get('view', 'upcoming'),
                                        statuses=Appointment.Status.choices,
                                        facilities=facilities_for_user(self.request.user), **kwargs)


class AppointmentCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'encounters.add_appointment'
    model = Appointment
    form_class = AppointmentForm
    title = 'Schedule appointment'
    success_tab = '#visits'


class AppointmentUpdateView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'encounters.change_appointment'
    model = Appointment
    form_class = AppointmentForm
    title = 'Reschedule / update appointment'
    success_tab = '#visits'


class AppointmentStatusView(PermissionRequiredMixin, View):
    """Quick status changes from worklists (confirm, missed, cancel)."""

    permission_required = 'encounters.change_appointment'

    def post(self, request, pk):
        appointment = get_object_or_404(
            scope_queryset(Appointment.objects.all(), request.user, 'patient__current_facility'), pk=pk)
        form = AppointmentStatusForm(request.POST)
        if not form.is_valid() or not appointment.is_open:
            messages.error(request, 'This appointment cannot be changed.')
        elif form.cleaned_data['status'] == 'cancelled' and not form.cleaned_data['reason']:
            messages.error(request, 'A reason is required to cancel an appointment.')
        else:
            old = appointment.status
            appointment.status = form.cleaned_data['status']
            if form.cleaned_data['reason']:
                appointment.reason_for_change = form.cleaned_data['reason']
            appointment.save(update_fields=['status', 'reason_for_change', 'updated_at'])
            log_event(request=request, action=AuditEvent.Action.STATUS, obj=appointment,
                      changes={'status': [old, appointment.status]}, reason=form.cleaned_data['reason'])
            messages.success(request, f'Appointment marked {appointment.get_status_display().lower()}.')
        return redirect(request.POST.get('next') or appointment.patient.get_absolute_url() + '#visits')


class EncounterListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'encounters.view_encounter'
    model = Encounter
    template_name = 'encounters/encounter_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('patient', 'facility', 'staff')
        view = self.request.GET.get('view', 'worklist')
        if view == 'worklist':
            qs = qs.filter(status__in=['open', 'in_progress', 'incomplete'])
        elif view == 'today':
            qs = qs.filter(encounter_date__date=timezone.localdate())
        if self.request.GET.get('stage'):
            qs = qs.filter(stage=self.request.GET['stage'])
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(view=self.request.GET.get('view', 'worklist'), stages=WorkflowStage.choices,
                                        **kwargs)


class EncounterCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'encounters.add_encounter'
    model = Encounter
    form_class = EncounterForm
    title = 'New encounter'

    def get_initial(self):
        initial = super().get_initial()
        appointment = self.patient.appointments.filter(pk=self.request.GET.get('appointment')).first()
        if appointment:
            initial.update(appointment=appointment, stage=appointment.stage, location_type=appointment.location_type,
                           facility=appointment.facility, programme_enrollment=appointment.programme_enrollment,
                           study_enrollment=appointment.study_enrollment)
        elif self.request.GET.get('stage') in WorkflowStage.values:
            initial['stage'] = self.request.GET['stage']
        return initial

    def form_valid(self, form):
        form.instance.staff = self.request.user
        response = super().form_valid(form)
        from clinical.services import return_on_attendance
        returned = return_on_attendance(self.patient, timezone.localtime(self.object.encounter_date).date(),
                                        self.request.user, self.request)
        if returned:
            messages.info(self.request, 'Returned to care: ' + ', '.join(str(e.programme) for e in returned)
                          + ' (the subject was lost to follow-up).')
        appointment = self.object.appointment
        if appointment and appointment.status != 'attended':
            old = appointment.status
            appointment.status = Appointment.Status.ATTENDED
            appointment.save(update_fields=['status', 'updated_at'])
            log_event(request=self.request, action=AuditEvent.Action.STATUS, obj=appointment,
                      changes={'status': [old, 'attended']})
        return response

    def get_success_url(self):
        return self.object.get_absolute_url()


class EncounterDetailView(PermissionRequiredMixin, ScopedQuerysetMixin, DetailView):
    permission_required = 'encounters.view_encounter'
    model = Encounter
    template_name = 'encounters/encounter_detail.html'

    def get_context_data(self, **kwargs):
        from crf.services import encounter_form_status

        enc = self.object
        return super().get_context_data(
            patient=enc.patient,
            vitals=enc.vitals.all(),
            screenings=enc.screenings.select_related('disease'),
            diagnoses=enc.diagnoses.select_related('disease'),
            lab_orders=enc.lab_orders.select_related('test'),
            investigations=enc.investigations.all(),
            prescriptions=enc.prescriptions.select_related('medication'),
            form_status=encounter_form_status(enc),
            **kwargs)


class EncounterUpdateView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'encounters.change_encounter'
    model = Encounter
    form_class = EncounterForm
    title = 'Update encounter'

    def get_success_url(self):
        return self.object.get_absolute_url()


class VitalsCreateView(PermissionRequiredMixin, CreateView):
    permission_required = 'encounters.add_vitals'
    model = Vitals
    form_class = VitalsForm
    template_name = 'generic_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.encounter = get_object_or_404(
            scope_queryset(Encounter.objects.all(), request.user, 'patient__current_facility'), pk=kwargs['encounter_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'patient': self.encounter.patient, 'user': self.request.user}

    def form_valid(self, form):
        form.instance.encounter = self.encounter
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        log_event(request=self.request, action=AuditEvent.Action.CREATE, obj=self.object)
        messages.success(self.request, 'Vitals recorded.')
        return response

    def get_success_url(self):
        return self.encounter.get_absolute_url()

    def get_context_data(self, **kwargs):
        return super().get_context_data(title='Triage / vital signs', patient=self.encounter.patient,
                                        cancel_url=self.encounter.get_absolute_url(), **kwargs)


class FollowUpPlanCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'encounters.add_followupplan'
    model = FollowUpPlan
    form_class = FollowUpPlanForm
    title = 'Create follow-up schedule'
    success_tab = '#visits'

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.object.generation == FollowUpPlan.Generation.AUTOMATIC:
            created = generate_visits(self.object, self.request.user)
            messages.info(self.request, f'{created} visits generated. Past dates are marked "expected" for reconciliation.')
        return response


class FollowUpPlanUpdateView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'encounters.change_followupplan'
    model = FollowUpPlan
    form_class = FollowUpPlanForm
    title = 'Change follow-up schedule'
    success_tab = '#visits'
    success_message = ''

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.has_changed() and set(form.changed_data) - {'change_reason'}:
            cancelled, created = revise_plan(self.object, self.request.user, form.cleaned_data['change_reason'],
                                             self.request)
            messages.success(self.request, f'Schedule updated to v{self.object.version}: {cancelled} future visits '
                                           f'replaced, {created} created. Completed and past visits were kept.')
        return response


class FollowUpPlanGenerateView(PermissionRequiredMixin, View):
    permission_required = 'encounters.add_appointment'

    def post(self, request, pk):
        plan = get_object_or_404(scope_queryset(FollowUpPlan.objects.all(), request.user, 'patient__current_facility'),
                                 pk=pk)
        created = generate_visits(plan, request.user)
        messages.success(request, f'{created} new visits generated (existing visits were not duplicated).')
        return redirect(plan.patient.get_absolute_url() + '#visits')


class AdmissionListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'encounters.view_admission'
    model = Admission
    template_name = 'encounters/admission_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('patient', 'facility')
        if self.request.GET.get('view', 'current') == 'current':
            qs = qs.filter(discharged_at__isnull=True)
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(view=self.request.GET.get('view', 'current'), **kwargs)


class AdmissionCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'encounters.add_admission'
    model = Admission
    form_class = AdmissionForm
    title = 'Admit subject'
    success_tab = '#visits'

    def form_valid(self, form):
        if self.patient.admissions.filter(discharged_at__isnull=True).exists():
            form.add_error(None, 'The subject is already admitted. Discharge the current admission first.')
            return self.form_invalid(form)
        return super().form_valid(form)


class AdmissionDischargeView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'encounters.change_admission'
    model = Admission
    form_class = DischargeForm
    title = 'Discharge subject'
    success_tab = '#visits'

    def get_initial(self):
        return {'discharged_at': timezone.now()}


# ---------------------------------------------------------------- clinic days

def scoped_sessions(user):
    return scope_queryset(ClinicSession.objects.all(), user, 'facility')


class ClinicSessionListView(PermissionRequiredMixin, ListView):
    permission_required = 'encounters.view_clinicsession'
    template_name = 'encounters/clinic_list.html'
    paginate_by = 25

    def get_queryset(self):
        from django.db.models import Count, Q
        return (scoped_sessions(self.request.user).select_related('facility').prefetch_related('programmes')
                .annotate(booked=Count('appointments', distinct=True),
                          attended=Count('appointments', filter=Q(appointments__status='attended'), distinct=True)))


class ClinicSessionCreateView(PermissionRequiredMixin, AuditedCreateMixin, CreateView):
    permission_required = 'encounters.add_clinicsession'
    model = ClinicSession
    form_class = ClinicSessionForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'New clinic day',
                     'subtitle': 'Schedule a group of subjects for a PEN-Plus / specialist clinic and invite them by SMS.'}
    success_message = 'Clinic day saved. Check the preview, then schedule the appointments.'

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'user': self.request.user}

    def get_initial(self):
        from facilities.scope import default_facility
        from notifications.sms import sms_config
        return {'facility': default_facility(self.request.user), 'contact_phone': sms_config().default_contact}


class ClinicSessionUpdateView(PermissionRequiredMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'encounters.change_clinicsession'
    form_class = ClinicSessionForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Edit clinic day'}

    def get_queryset(self):
        return scoped_sessions(self.request.user).exclude(status='cancelled')

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'user': self.request.user}


class ClinicSessionDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'encounters.view_clinicsession'
    template_name = 'encounters/clinic_detail.html'

    def get_queryset(self):
        return scoped_sessions(self.request.user).select_related('facility')

    def get_context_data(self, **kwargs):
        from django.db.models import Count
        from encounters.clinic import preview
        session = self.object
        appointments = session.appointments.select_related('patient').order_by('scheduled_date', 'patient__patient_number')
        latest_sms = {}
        for message in session.sms_messages.order_by('created_at'):
            latest_sms[message.appointment_id] = message
        rows = [(a, latest_sms.get(a.pk)) for a in appointments]
        return super().get_context_data(
            preview=preview(session) if session.status == 'draft' else None, rows=rows,
            attendance=dict(appointments.values_list('status').annotate(n=Count('id'))),
            sms_counts=dict(session.sms_messages.values_list('status').annotate(n=Count('id'))),
            sample_sms=session.render_sms(session.start_date), **kwargs)


class ClinicSessionActionView(PermissionRequiredMixin, View):
    permission_required = 'encounters.change_clinicsession'

    def post(self, request, pk, action):
        from encounters import clinic
        session = get_object_or_404(scoped_sessions(request.user), pk=pk)
        if session.status == 'cancelled':
            messages.error(request, 'This clinic day was cancelled.')
        elif action == 'schedule':
            counts = clinic.schedule(session, request.user, request)
            messages.success(request, 'Appointments: ' + ', '.join(f'{v} {k}' for k, v in counts.items()) if counts
                             else 'No eligible subjects found.')
        elif action == 'sms':
            if not request.user.has_perm('notifications.add_smsmessage'):
                raise PermissionDenied
            reasons, sent = clinic.send_invitations(session, request.user, request)
            messages.success(request, f'SMS: {reasons.get("queued", 0)} queued, {sent["sent"]} sent, '
                                      f'{sent["failed"]} failed.')
            skipped = {k: v for k, v in reasons.items() if k != 'queued'}
            if skipped:
                messages.warning(request, 'Not sent: ' + ', '.join(f'{v} {k}' for k, v in skipped.items()))
        elif action == 'close':
            count = clinic.close_attendance(session, request.user, request)
            messages.success(request, f'{count} subject(s) who did not attend were marked missed.')
        elif action == 'cancel':
            reason = request.POST.get('reason', '').strip()
            if not reason:
                messages.error(request, 'Give the reason for cancelling the clinic day.')
            else:
                count = clinic.cancel(session, request.user, reason, request)
                messages.success(request, f'Clinic day cancelled; {count} appointment(s) cancelled.')
        else:
            raise PermissionDenied
        return redirect(session.get_absolute_url())

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from core.mixins import (AuditedCreateMixin, AuditedUpdateMixin, PatientChildCreateMixin, PatientChildUpdateMixin,
                         ScopedQuerysetMixin)
from facilities.scope import default_facility, scope_queryset
from research.services import mirror_programme_status
from patients.models import Patient
from patients.services import register_patient

from issues.detectors import diagnosis_prompts
from .forms import (DiagnosisCreateForm, FamilyMemberForm, DiagnosisForm, PatientQuickForm, ReturnToCareForm, PreScreeningForm, ProgrammeEnrollmentForm, ScreeningCreateForm,
                    ScreeningSessionForm, ScreeningUpdateForm, SessionScreeningForm)
from .models import (CLOSED_RETURNABLE, ClinicalProgramme, FamilyMember, Diagnosis, Disease, EnrollmentEvent, PreScreening,
                     ProgrammeEnrollment, Screening, ScreeningSession)


class PreScreeningCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'clinical.add_prescreening'
    model = PreScreening
    form_class = PreScreeningForm
    title = 'Pre-screening'
    success_tab = '#screening'


class ScreeningCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'clinical.add_screening'
    model = Screening
    form_class = ScreeningCreateForm
    title = 'Disease screening'
    success_tab = '#screening'

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get('encounter'):
            initial['encounter'] = self.request.GET['encounter']
        return initial

    def form_valid(self, form):
        data = {k: v for k, v in form.cleaned_data.items() if k != 'diseases'}
        created = []
        for disease in form.cleaned_data['diseases']:
            screening = Screening.objects.create(patient=self.patient, disease=disease, screened_by=self.request.user,
                                                 created_by=self.request.user, **data)
            log_event(request=self.request, action=AuditEvent.Action.CREATE, obj=screening)
            created.append(disease.name)
        messages.success(self.request, f'Screening recorded for: {", ".join(created)}.')
        return redirect(self.get_success_url())


class ScreeningUpdateView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'clinical.change_screening'
    model = Screening
    form_class = ScreeningUpdateForm
    title = 'Edit screening'
    success_tab = '#screening'


class DiagnosisCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'clinical.add_diagnosis'
    model = Diagnosis
    form_class = DiagnosisCreateForm
    title = 'Record diagnosis'
    success_tab = '#clinical'

    def get_initial(self):
        initial = super().get_initial()
        initial['encounter'] = self.request.GET.get('encounter')
        return initial

    def form_valid(self, form):
        data = {k: v for k, v in form.cleaned_data.items() if k not in ('conditions', 'description')}
        details = form.cleaned_data.get('description', '').strip()
        conditions = list(form.cleaned_data.get('conditions') or []) or [None]
        created = []
        for condition in conditions:
            description = condition.label if condition else details
            if condition and details:
                description = f'{condition.label} – {details}'
            diagnosis = Diagnosis.objects.create(patient=self.patient, condition=condition, description=description[:255],
                                                 clinician=self.request.user, created_by=self.request.user, **data)
            log_event(request=self.request, action=AuditEvent.Action.CREATE, obj=diagnosis)
            created.append(diagnosis.description)
        messages.success(self.request, f'{len(created)} diagnosis/diagnoses recorded: {"; ".join(created)}.')
        diagnosis_prompts([self.patient.pk])
        return redirect(self.get_success_url())


class DiagnosisUpdateView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'clinical.change_diagnosis'
    model = Diagnosis
    form_class = DiagnosisForm
    title = 'Update diagnosis'
    success_tab = '#clinical'

    def form_valid(self, form):
        response = super().form_valid(form)
        diagnosis_prompts([self.object.patient_id])
        return response


class ProgrammeEnrollmentCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'clinical.add_programmeenrollment'
    model = ProgrammeEnrollment
    form_class = ProgrammeEnrollmentForm
    title = 'Enroll in clinical programme'
    success_tab = '#clinical'


class ProgrammeEnrollmentUpdateView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'clinical.change_programmeenrollment'
    model = ProgrammeEnrollment
    form_class = ProgrammeEnrollmentForm
    title = 'Update programme enrollment / outcome'
    success_tab = '#clinical'

    def form_valid(self, form):
        old_status = ProgrammeEnrollment.objects.get(pk=self.object.pk).status
        response = super().form_valid(form)
        if old_status != self.object.status:
            EnrollmentEvent.objects.create(
                enrollment=self.object, from_status=old_status, to_status=self.object.status,
                event_date=self.object.outcome_date or timezone.localdate(), reason=self.object.outcome_reason,
                created_by=self.request.user)
            changed, _skipped = mirror_programme_status(self.object, self.request.user, self.request)
            if changed:
                messages.info(self.request, 'Research enrollment updated to match: '
                              + ', '.join(f'{s.study.code} → {s.get_status_display()}' for s in changed))
        return response


class ReturnToCareView(PermissionRequiredMixin, View):
    """Reopen a closed enrollment (default/LTFU, withdrawal, transfer out) while keeping its history."""

    permission_required = 'clinical.change_programmeenrollment'

    def get_enrollment(self, request, pk):
        enrollment = get_object_or_404(scope_queryset(ProgrammeEnrollment.objects.select_related('patient', 'programme'),
                                                      request.user, 'patient__current_facility'), pk=pk)
        if enrollment.status not in CLOSED_RETURNABLE:
            raise PermissionDenied('Only closed enrollments (default, lost to follow-up, withdrawn, transferred out) '
                                   'can be reopened.')
        if enrollment.patient.is_deceased:
            raise PermissionDenied('The patient is recorded as deceased.')
        return enrollment

    def render_form(self, request, enrollment, form):
        return render(request, 'confirm_action.html', {
            'title': f'Return to care – {enrollment.programme}', 'patient': enrollment.patient, 'form': form,
            'message': f'Currently {enrollment.get_status_display().lower()}'
                       + (f' since {enrollment.outcome_date:%d %b %Y}' if enrollment.outcome_date else '')
                       + '. The earlier status stays in the enrollment history. Research participation closed '
                         'the same way is reopened too (unless consent was withdrawn).',
            'button': 'Return to care', 'cancel_url': enrollment.patient.get_absolute_url() + '#clinical'})

    def get(self, request, pk):
        enrollment = self.get_enrollment(request, pk)
        return self.render_form(request, enrollment, ReturnToCareForm(enrollment=enrollment,
                                                                       initial={'return_date': timezone.localdate()}))

    def post(self, request, pk):
        from encounters.models import FollowUpPlan
        from encounters.services import generate_visits

        enrollment = self.get_enrollment(request, pk)
        form = ReturnToCareForm(request.POST, enrollment=enrollment)
        if not form.is_valid():
            return self.render_form(request, enrollment, form)
        if ProgrammeEnrollment.objects.filter(patient=enrollment.patient, programme=enrollment.programme,
                                              status__in=['pending', 'active']).exclude(pk=enrollment.pk).exists():
            form.add_error(None, 'The patient already has another open enrollment in this programme.')
            return self.render_form(request, enrollment, form)
        data = form.cleaned_data
        kind = 'transfer_in' if data['kind'] == 'transfer_in' else 'return'
        label = dict(ReturnToCareForm.KIND_CHOICES)[data['kind']]
        with transaction.atomic():
            old_status = enrollment.status
            EnrollmentEvent.objects.create(enrollment=enrollment, kind=kind, from_status=old_status, to_status='active',
                                           event_date=data['return_date'],
                                           reason=f'{label}. {data["reason"]}'.strip(' .')[:255],
                                           created_by=request.user)
            enrollment.status, enrollment.outcome_date, enrollment.outcome_reason = 'active', None, ''
            enrollment.save(update_fields=['status', 'outcome_date', 'outcome_reason', 'updated_at'])
            log_event(request=request, action=AuditEvent.Action.STATUS, obj=enrollment,
                      changes={'status': [old_status, 'active']}, reason=label)
            studies, skipped = mirror_programme_status(enrollment, request.user, request)
            created = 0
            if data['create_schedule']:
                plan = FollowUpPlan.objects.create(
                    patient=enrollment.patient, facility=enrollment.patient.current_facility,
                    programme_enrollment=enrollment, interval_months=data['interval_months'], anchor_type='custom',
                    anchor_date=data['return_date'], visit_count=data['visit_count'], created_by=request.user)
                created = generate_visits(plan, request.user)
        messages.success(request, f'{enrollment.programme}: back in care from {data["return_date"]:%d %b %Y}.'
                                  + (f' {created} follow-up visits scheduled.' if created else '')
                                  + (f' Research reopened: {", ".join(s.study.code for s in studies)}.' if studies else ''))
        for study in skipped:
            messages.warning(request, f'{study.study.code}: consent was withdrawn, so research participation was not '
                                      f'reopened. Record new consent to re-enroll.')
        return redirect(enrollment.patient.get_absolute_url() + '#visits')


class ScreeningListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'clinical.view_screening'
    model = Screening
    template_name = 'clinical/screening_list.html'
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset().select_related('patient', 'disease', 'facility')
        for key in ('disease', 'result', 'source', 'session'):
            if self.request.GET.get(key):
                qs = qs.filter(**{key: self.request.GET[key]})
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(diseases=Disease.objects.all(), results=Screening.Result.choices,
                                        sources=Screening.Source.choices, **kwargs)


class ProgrammeEnrollmentListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'clinical.view_programmeenrollment'
    model = ProgrammeEnrollment
    template_name = 'clinical/enrollment_list.html'
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset().select_related('patient', 'programme__disease', 'facility')
        if self.request.GET.get('programme'):
            qs = qs.filter(programme_id=self.request.GET['programme'])
        if self.request.GET.get('status'):
            qs = qs.filter(status=self.request.GET['status'])
        return qs

    def get_context_data(self, **kwargs):
        from .models import EnrollmentStatus
        return super().get_context_data(programmes=ClinicalProgramme.objects.all(),
                                        statuses=EnrollmentStatus.choices, **kwargs)


# ---------------------------------------------------------------- screening campaigns

class ScreeningSessionListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'clinical.view_screeningsession'
    model = ScreeningSession
    scope_field = 'facility'
    template_name = 'clinical/session_list.html'
    paginate_by = 25

    def get_queryset(self):
        return (super().get_queryset().select_related('facility').prefetch_related('diseases')
                .annotate(people=Count('screenings__patient', distinct=True),
                          positives=Count('screenings', filter=Q(screenings__result='positive'))))


class ScreeningSessionCreateView(PermissionRequiredMixin, AuditedCreateMixin, CreateView):
    permission_required = 'clinical.add_screeningsession'
    model = ScreeningSession
    form_class = ScreeningSessionForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'New screening session',
                     'subtitle': 'One session per school, village/street or clinic day.'}

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'user': self.request.user}

    def get_initial(self):
        return {'facility': default_facility(self.request.user), 'diseases': Disease.objects.filter(is_active=True)}


class ScreeningSessionUpdateView(PermissionRequiredMixin, ScopedQuerysetMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'clinical.change_screeningsession'
    model = ScreeningSession
    scope_field = 'facility'
    form_class = ScreeningSessionForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Edit screening session'}

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'user': self.request.user}


AGE_GROUPS = [(6, 17, '6–17'), (18, 24, '18–24'), (25, 64, '25–64'), (65, 200, '65+'), (0, 5, 'Under 6')]


def age_on(dob, date):
    return date.year - dob.year - ((date.month, date.day) < (dob.month, dob.day))


class ScreeningSessionDetailView(PermissionRequiredMixin, ScopedQuerysetMixin, DetailView):
    permission_required = 'clinical.view_screeningsession'
    model = ScreeningSession
    scope_field = 'facility'
    template_name = 'clinical/session_detail.html'

    def get_context_data(self, **kwargs):
        session = self.object
        screenings = session.screenings.select_related('patient', 'disease', 'referred_to', 'screened_by')
        patients = {s.patient_id: s.patient for s in screenings}.values()
        by_sex, by_age = {}, {label: 0 for _lo, _hi, label in AGE_GROUPS}
        for p in patients:
            by_sex[p.get_sex_display()] = by_sex.get(p.get_sex_display(), 0) + 1
            age = age_on(p.date_of_birth, session.session_date)
            label = next(label for lo, hi, label in AGE_GROUPS if lo <= age <= hi)
            by_age[label] += 1
        results = (session.screenings.values('disease__name').annotate(
            total=Count('id'), positive=Count('id', filter=Q(result='positive')),
            negative=Count('id', filter=Q(result='negative')),
            other=Count('id', filter=Q(result__in=['pending', 'inconclusive']))).order_by('disease__name'))
        return super().get_context_data(
            screenings=screenings, people=len(patients), by_sex=by_sex,
            by_age={k: v for k, v in by_age.items() if v or k != 'Under 6'}, results=results,
            referred=screenings.filter(referred=True), **kwargs)


class SessionScreenView(PermissionRequiredMixin, View):
    """Register (or find) a person and record their screening in one step, then move to the next person."""

    permission_required = ('clinical.add_screening', 'patients.view_patient_identity')
    template_name = 'clinical/session_screen.html'

    def setup_session(self, request, pk):
        self.session = get_object_or_404(scope_queryset(ScreeningSession.objects.all(), request.user, 'facility'), pk=pk)
        if self.session.is_closed:
            raise PermissionDenied('This screening session is closed.')

    def existing_patient(self, request):
        pk = request.GET.get('patient') or request.POST.get('patient')
        return get_object_or_404(Patient.objects.in_scope(request.user), pk=pk) if pk else None

    def render_page(self, request, patient, pform, sform):
        from patients.views import search_patients
        query = request.GET.get('q', '').strip()
        matches = search_patients(Patient.objects.in_scope(request.user), query, True)[:15] if query else []
        scope_ids = set(Patient.objects.in_scope(request.user).values_list('pk', flat=True)) if pform and pform.duplicates else set()
        last = Patient.objects.in_scope(request.user).filter(pk=request.GET.get('saved')).first()
        return render(request, self.template_name, {
            'session': self.session, 'existing': patient, 'pform': pform, 'sform': sform, 'query': query,
            'matches': matches, 'scope_ids': scope_ids, 'last_saved': last,
            'count': self.session.screenings.values('patient').distinct().count(),
            'can_register': request.user.has_perm('patients.add_patient')})

    def get(self, request, pk):
        self.setup_session(request, pk)
        patient = self.existing_patient(request)
        pform = None if patient else PatientQuickForm(prefix='p', user=request.user)
        return self.render_page(request, patient, pform, SessionScreeningForm(prefix='s', session=self.session))

    def post(self, request, pk):
        self.setup_session(request, pk)
        patient = self.existing_patient(request)
        if patient is None and not request.user.has_perm('patients.add_patient'):
            raise PermissionDenied('Your role cannot register new people.')
        pform = None if patient else PatientQuickForm(request.POST, prefix='p', user=request.user)
        sform = SessionScreeningForm(request.POST, prefix='s', session=self.session)
        valid = sform.is_valid() & (pform is None or pform.is_valid())
        if valid:
            dob = patient.date_of_birth if patient else pform.cleaned_data['date_of_birth']
            sform.check_person(age_on(dob, self.session.session_date), patient)
            valid = not sform.errors
        if not valid:
            messages.error(request, 'Please correct the highlighted fields.')
            return self.render_page(request, patient, pform, sform)
        with transaction.atomic():
            if patient is None:
                new = pform.save(commit=False)
                new.registration_facility = self.session.facility
                new.registration_date = self.session.session_date
                patient = register_patient(new, request.user)
                log_event(request=request, action=AuditEvent.Action.CREATE, obj=patient,
                          reason=f'Registered at screening session {self.session.pk}')
            data = sform.cleaned_data
            for disease, result in sform.results():
                screening = Screening.objects.create(
                    patient=patient, disease=disease, session=self.session, facility=self.session.facility,
                    source=self.session.source, location_type=self.session.location_type,
                    screening_date=self.session.session_date, result=result, risk_factors=data['risk_factors'],
                    consent_obtained=data['consent_obtained'], assent_obtained=data['assent_obtained'],
                    guardian_name=data['guardian_name'], referred=data['referred'], referred_to=data['referred_to'],
                    next_action=data['next_action'], notes=data['notes'], screened_by=request.user,
                    created_by=request.user)
                log_event(request=request, action=AuditEvent.Action.CREATE, obj=screening)
        messages.success(request, f'{patient.patient_number} screened. Ready for the next person.')
        return redirect(reverse('clinical:session_screen', args=[self.session.pk]) + f'?saved={patient.pk}')


class FamilyMemberCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'clinical.add_familymember'
    model = FamilyMember
    form_class = FamilyMemberForm
    title = 'Add family member'
    success_tab = '#clinical'


class FamilyMemberUpdateView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'clinical.change_familymember'
    model = FamilyMember
    form_class = FamilyMemberForm
    title = 'Edit family member'
    success_tab = '#clinical'

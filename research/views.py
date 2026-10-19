from django.contrib.auth.mixins import PermissionRequiredMixin
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core.mixins import AuditedCreateMixin, AuditedUpdateMixin, PatientChildCreateMixin, PatientChildUpdateMixin
from facilities.scope import scope_queryset

from .forms import ConsentForm, EligibilityForm, StudyEnrollmentForm, StudyForm
from .models import ConsentRecord, Study, StudyEligibilityAssessment, StudyEnrollment


class StudyListView(PermissionRequiredMixin, ListView):
    permission_required = 'research.view_study'
    model = Study
    template_name = 'research/study_list.html'

    def get_queryset(self):
        return Study.objects.prefetch_related('diseases').select_related('principal_investigator')


class StudyDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'research.view_study'
    model = Study
    template_name = 'research/study_detail.html'

    def get_context_data(self, **kwargs):
        enrollments = scope_queryset(self.object.enrollments.select_related('patient', 'facility'),
                                     self.request.user, 'patient__current_facility')
        status = self.request.GET.get('status')
        if status:
            enrollments = enrollments.filter(status=status)
        return super().get_context_data(enrollments=enrollments, statuses=StudyEnrollment.Status.choices, **kwargs)


class StudyCreateView(PermissionRequiredMixin, AuditedCreateMixin, CreateView):
    permission_required = 'research.add_study'
    model = Study
    form_class = StudyForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'New study'}


class StudyUpdateView(PermissionRequiredMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'research.change_study'
    model = Study
    form_class = StudyForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Edit study'}


class EligibilityCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'research.add_studyeligibilityassessment'
    model = StudyEligibilityAssessment
    form_class = EligibilityForm
    title = 'Study eligibility assessment'
    success_tab = '#research'

    def form_valid(self, form):
        form.instance.assessed_by = self.request.user
        return super().form_valid(form)


class ConsentCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'research.add_consentrecord'
    model = ConsentRecord
    form_class = ConsentForm
    title = 'Record consent'
    success_tab = '#research'

    def get_initial(self):
        initial = super().get_initial()
        study = Study.objects.filter(pk=self.request.GET.get('study')).first()
        if study:
            initial.update(study=study, consent_version=study.current_consent_version)
        return initial

    def form_valid(self, form):
        form.instance.obtained_by = self.request.user
        return super().form_valid(form)


class ConsentUpdateView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'research.change_consentrecord'
    model = ConsentRecord
    form_class = ConsentForm
    title = 'Update consent (e.g. withdrawal)'
    success_tab = '#research'


class StudyEnrollmentCreateView(PatientChildCreateMixin, CreateView):
    permission_required = 'research.add_studyenrollment'
    model = StudyEnrollment
    form_class = StudyEnrollmentForm
    title = 'Enroll in research study'
    success_tab = '#research'

    def get_initial(self):
        initial = super().get_initial()
        study = Study.objects.filter(pk=self.request.GET.get('study')).first()
        if study:
            count = study.enrollments.count() + 1
            initial.update(study=study, participant_id=f'{study.participant_id_prefix}{count:04d}',
                           eligibility=self.patient.study_eligibility.filter(study=study, is_eligible=True).first(),
                           consent=self.patient.consents.filter(study=study, decision='consented').first())
        return initial


class StudyEnrollmentUpdateView(PatientChildUpdateMixin, UpdateView):
    permission_required = 'research.change_studyenrollment'
    model = StudyEnrollment
    form_class = StudyEnrollmentForm
    title = 'Update study enrollment / outcome'
    success_tab = '#research'

    def get_success_url(self):
        return reverse('patients:detail', args=[self.object.patient_id]) + '#research'

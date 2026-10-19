from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import log_event
from core.mixins import AuditedCreateMixin, AuditedUpdateMixin, ScopedQuerysetMixin
from encounters.models import Encounter
from facilities.scope import scope_queryset
from patients.models import Patient

from .forms import FormDefinitionForm, FormFieldForm, FormSectionForm, SubmissionReviewForm
from .models import FormDefinition, FormField, FormSection, FormSubmission, FormVersion
from .services import DynamicCRFForm, clone_version, mark_submitted, publish_version


# ---------------------------------------------------------------- form builder

class DefinitionListView(PermissionRequiredMixin, ListView):
    permission_required = 'crf.view_formdefinition'
    model = FormDefinition
    template_name = 'crf/definition_list.html'

    def get_queryset(self):
        return FormDefinition.objects.prefetch_related('diseases', 'versions').select_related('study')


class DefinitionCreateView(PermissionRequiredMixin, AuditedCreateMixin, CreateView):
    permission_required = 'crf.add_formdefinition'
    model = FormDefinition
    form_class = FormDefinitionForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'New form definition'}

    def form_valid(self, form):
        response = super().form_valid(form)
        FormVersion.objects.create(definition=self.object, number=1, created_by=self.request.user)
        return response


class DefinitionUpdateView(PermissionRequiredMixin, AuditedUpdateMixin, UpdateView):
    permission_required = 'crf.change_formdefinition'
    model = FormDefinition
    form_class = FormDefinitionForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Edit form definition and applicability'}


class DefinitionDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'crf.view_formdefinition'
    model = FormDefinition
    template_name = 'crf/definition_detail.html'


class VersionDetailView(PermissionRequiredMixin, DetailView):
    """The form builder for a version: sections, fields and preview."""

    permission_required = 'crf.view_formversion'
    model = FormVersion
    template_name = 'crf/version_detail.html'

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            sections=self.object.sections.prefetch_related('fields'),
            section_form=FormSectionForm(initial={'order': self.object.sections.count() + 1}),
            preview=DynamicCRFForm(version=self.object), **kwargs)


class VersionEditMixin(PermissionRequiredMixin):
    permission_required = 'crf.change_formversion'

    def dispatch(self, request, *args, **kwargs):
        self.version = get_object_or_404(FormVersion, pk=kwargs['version_pk'])
        if not self.version.is_editable:
            messages.error(request, 'Published versions are read-only. Create a new version to make changes.')
            return redirect(self.version.get_absolute_url())
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return self.version.get_absolute_url()


class SectionCreateView(VersionEditMixin, CreateView):
    model = FormSection
    form_class = FormSectionForm
    template_name = 'generic_form.html'

    def form_valid(self, form):
        form.instance.version = self.version
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        return super().get_context_data(title=f'Add section to {self.version}', cancel_url=self.get_success_url(), **kwargs)


class SectionUpdateView(VersionEditMixin, UpdateView):
    model = FormSection
    form_class = FormSectionForm
    template_name = 'generic_form.html'

    def get_queryset(self):
        return self.version.sections.all()

    def get_context_data(self, **kwargs):
        return super().get_context_data(title='Edit section', cancel_url=self.get_success_url(), **kwargs)


class FieldCreateView(VersionEditMixin, CreateView):
    model = FormField
    form_class = FormFieldForm
    template_name = 'generic_form.html'

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'version': self.version}

    def get_initial(self):
        section = self.version.sections.filter(pk=self.request.GET.get('section')).first()
        return {'section': section, 'order': (section.fields.count() + 1) if section else 1}

    def get_context_data(self, **kwargs):
        return super().get_context_data(title=f'Add field to {self.version}', cancel_url=self.get_success_url(), **kwargs)


class FieldUpdateView(VersionEditMixin, UpdateView):
    model = FormField
    form_class = FormFieldForm
    template_name = 'generic_form.html'

    def get_queryset(self):
        return FormField.objects.filter(section__version=self.version)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'version': self.version}

    def get_context_data(self, **kwargs):
        return super().get_context_data(title='Edit field', cancel_url=self.get_success_url(), **kwargs)


class BuilderDeleteView(VersionEditMixin, View):
    def post(self, request, version_pk, kind, pk):
        model = {'field': FormField, 'section': FormSection}.get(kind)
        if model is None:
            raise PermissionDenied
        lookup = {'section__version': self.version} if kind == 'field' else {'version': self.version}
        get_object_or_404(model, pk=pk, **lookup).delete()
        messages.success(request, f'{kind.title()} removed from draft.')
        return redirect(self.get_success_url())


class VersionPublishView(PermissionRequiredMixin, View):
    permission_required = 'crf.publish_formversion'

    def post(self, request, pk):
        version = get_object_or_404(FormVersion, pk=pk, status='draft')
        try:
            publish_version(version, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(version.get_absolute_url())
        log_event(request=request, action=AuditEvent.Action.PUBLISH, obj=version)
        messages.success(request, f'{version} published. Earlier versions were retired; their submissions keep their version.')
        return redirect(version.get_absolute_url())


class VersionCloneView(PermissionRequiredMixin, View):
    permission_required = 'crf.add_formversion'

    def post(self, request, pk):
        version = get_object_or_404(FormVersion, pk=pk)
        if version.definition.draft_version:
            messages.info(request, 'A draft version already exists.')
            return redirect(version.definition.draft_version.get_absolute_url())
        new = clone_version(version, request.user)
        log_event(request=request, action=AuditEvent.Action.CREATE, obj=new, reason=f'New draft from v{version.number}')
        messages.success(request, f'Draft {new} created.')
        return redirect(new.get_absolute_url())


# ---------------------------------------------------------------- data capture

def scoped_submissions(user):
    return scope_queryset(FormSubmission.objects.all(), user, 'patient__current_facility')


class SubmissionFormView(PermissionRequiredMixin, View):
    """Fill in (or continue) a form for a patient, optionally linked to an encounter."""

    template_name = 'crf/submission_form.html'

    def get_permission_required(self):
        return ('crf.change_formsubmission',) if 'pk' in self.kwargs else ('crf.add_formsubmission',)

    def setup_context(self, request, **kwargs):
        if 'pk' in kwargs:
            self.submission = get_object_or_404(scoped_submissions(request.user).select_related('version', 'patient'),
                                                pk=kwargs['pk'])
            if not self.submission.is_editable:
                raise PermissionDenied('This submission is no longer editable.')
            self.version, self.patient = self.submission.version, self.submission.patient
            self.encounter = self.submission.encounter
        else:
            self.submission = None
            self.patient = get_object_or_404(Patient.objects.in_scope(request.user), pk=kwargs['patient_pk'])
            self.version = get_object_or_404(FormVersion, pk=kwargs['version_pk'], status='published')
            encounter_id = request.GET.get('encounter') or request.POST.get('encounter')
            self.encounter = get_object_or_404(self.patient.encounters, pk=encounter_id) if encounter_id else None

    def render_form(self, request, form):
        return render(request, self.template_name, {
            'form': form, 'version': self.version, 'patient': self.patient, 'encounter': self.encounter,
            'submission': self.submission,
            'cancel_url': self.encounter.get_absolute_url() if self.encounter else self.patient.get_absolute_url()})

    def get(self, request, **kwargs):
        self.setup_context(request, **kwargs)
        initial = self.submission.data if self.submission else {}
        return self.render_form(request, DynamicCRFForm(version=self.version, initial=initial))

    def post(self, request, **kwargs):
        self.setup_context(request, **kwargs)
        final = request.POST.get('action') != 'draft'
        form = DynamicCRFForm(request.POST, version=self.version, final=final)
        if not form.is_valid():
            messages.error(request, 'Please correct the highlighted fields.')
            return self.render_form(request, form)
        submission = self.submission or FormSubmission(
            version=self.version, patient=self.patient, encounter=self.encounter, created_by=request.user,
            programme_enrollment=self.encounter.programme_enrollment if self.encounter else None,
            study_enrollment=self.encounter.study_enrollment if self.encounter else None)
        is_new = submission.pk is None
        before = dict(submission.data)
        submission.data = form.answers()
        if final:
            mark_submitted(submission, request.user)
        else:
            submission.status = FormSubmission.Status.IN_PROGRESS
        submission.save()
        changes = {} if is_new else {k: [before.get(k), v] for k, v in submission.data.items() if before.get(k) != v}
        log_event(request=request, action=AuditEvent.Action.CREATE if is_new else AuditEvent.Action.UPDATE,
                  obj=submission, changes=changes)
        messages.success(request, 'Form submitted.' if final else 'Draft saved.')
        return redirect(submission.get_absolute_url())


class SubmissionDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'crf.view_formsubmission'
    template_name = 'crf/submission_detail.html'

    def get_queryset(self):
        return scoped_submissions(self.request.user).select_related('version__definition', 'patient', 'encounter')

    def get_context_data(self, **kwargs):
        sub = self.object
        rows = []
        for spec in sub.version.fields():
            value = sub.data.get(spec.key)
            choices = dict(spec.choices())
            if isinstance(value, list):
                value = ', '.join(choices.get(v, v) for v in value)
            elif spec.field_type == 'yesno' and value:
                value = value.title()
            elif value is not None:
                value = choices.get(value, value)
            rows.append((spec.section, spec, value))
        return super().get_context_data(rows=rows, patient=sub.patient, review_form=SubmissionReviewForm(), **kwargs)


class SubmissionReviewView(PermissionRequiredMixin, View):
    permission_required = 'crf.review_formsubmission'

    def post(self, request, pk):
        submission = get_object_or_404(scoped_submissions(request.user), pk=pk)
        form = SubmissionReviewForm(request.POST)
        if not form.is_valid() or submission.status not in ('submitted', 'under_review'):
            messages.error(request, 'This submission cannot be reviewed now.')
            return redirect(submission.get_absolute_url())
        if form.cleaned_data['decision'] == 'returned' and not form.cleaned_data['comment']:
            messages.error(request, 'Explain what needs correcting when returning a form.')
            return redirect(submission.get_absolute_url())
        old = submission.status
        submission.status = form.cleaned_data['decision']
        submission.review_comment = form.cleaned_data['comment']
        submission.reviewed_by = request.user
        submission.reviewed_at = timezone.now()
        submission.save()
        log_event(request=request, action=AuditEvent.Action.REVIEW, obj=submission,
                  changes={'status': [old, submission.status]}, reason=form.cleaned_data['comment'])
        messages.success(request, f'Submission marked {submission.get_status_display().lower()}.')
        return redirect(submission.get_absolute_url())


class SubmissionNotApplicableView(PermissionRequiredMixin, View):
    """Record that a required form does not apply to an encounter, with a reason."""

    permission_required = 'crf.add_formsubmission'

    def post(self, request, encounter_pk, version_pk):
        encounter = get_object_or_404(scope_queryset(Encounter.objects.all(), request.user, 'patient__current_facility'),
                                      pk=encounter_pk)
        version = get_object_or_404(FormVersion, pk=version_pk, status='published')
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, 'A reason is required to mark a form not applicable.')
            return redirect(encounter.get_absolute_url())
        submission = FormSubmission.objects.create(
            version=version, patient=encounter.patient, encounter=encounter, status='not_applicable',
            not_applicable_reason=reason[:255], submitted_by=request.user, submitted_at=timezone.now(),
            created_by=request.user)
        log_event(request=request, action=AuditEvent.Action.CREATE, obj=submission, reason=reason)
        messages.success(request, f'{version.definition} marked not applicable.')
        return redirect(encounter.get_absolute_url())


class SubmissionListView(PermissionRequiredMixin, ScopedQuerysetMixin, ListView):
    permission_required = 'crf.view_formsubmission'
    model = FormSubmission
    template_name = 'crf/submission_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related('version__definition', 'patient', 'encounter', 'submitted_by')
        status = self.request.GET.get('status', 'submitted')
        if status:
            qs = qs.filter(status=status)
        if self.request.GET.get('form'):
            qs = qs.filter(version__definition_id=self.request.GET['form'])
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(statuses=FormSubmission.Status.choices,
                                        current_status=self.request.GET.get('status', 'submitted'),
                                        definitions=FormDefinition.objects.all(), **kwargs)

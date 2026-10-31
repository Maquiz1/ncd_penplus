from django.contrib.auth import logout, views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from audit.models import AuditEvent
from audit.services import diff, log_event, snapshot
from core.forms import BootstrapModelForm
from facilities.models import StaffAssignment
from facilities.scope import facilities_for_user

from .forms import (IdentityAccessForm, ProfileForm, LoggedPasswordResetForm, StaffAssignmentForm, StaffCreateForm,
                    StaffSetPasswordForm, StaffUpdateForm)
from .models import Position, User
from .services import pending_activation, send_activation_email, send_bulk_activation


def staff_in_scope(user):
    """Staff accounts the user may manage: those assigned within the user's facilities."""
    qs = User.objects.all()
    if user.is_superuser:
        return qs
    facilities = facilities_for_user(user)
    return qs.filter(Q(assignments__facility__in=facilities) | Q(pk=user.pk)).filter(is_superuser=False).distinct()


def staff_to_manage(user):
    """Staff in scope whose roles are all within the user's own rights (others can be seen, not changed)."""
    from .forms import ungrantable_roles
    blocked = ungrantable_roles(user)
    return staff_in_scope(user).exclude(groups__in=blocked)


STAFF_TABS = [
    ('all', 'All', Q(is_deleted=False)),
    ('active', 'Active', Q(is_deleted=False, is_active=True)),
    ('inactive', 'Deactivated', Q(is_deleted=False, is_active=False)),
    ('not_activated', 'Not activated yet', Q(is_deleted=False, password__startswith='!')),
    ('deleted', 'Deleted', Q(is_deleted=True)),
]


class StaffListView(PermissionRequiredMixin, ListView):
    permission_required = 'users.view_user'
    template_name = 'users/staff_list.html'
    paginate_by = 25

    def scoped(self):
        qs = staff_in_scope(self.request.user)
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
        return qs

    def tab(self):
        status = self.request.GET.get('status', 'all')
        return next((t for t in STAFF_TABS if t[0] == status), STAFF_TABS[0])

    def get_queryset(self):
        return (self.scoped().filter(self.tab()[2]).select_related('position', 'deleted_by')
                .prefetch_related('groups', 'assignments').order_by('username'))

    def get_context_data(self, **kwargs):
        scoped = self.scoped()
        counts = scoped.aggregate(**{key: Count('pk', filter=condition, distinct=True) for key, _l, condition in STAFF_TABS})
        recent = timezone.now() - timezone.timedelta(days=30)
        return super().get_context_data(
            tabs=[(key, label, counts[key]) for key, label, _c in STAFF_TABS], status=self.tab()[0],
            logged_in_30d=scoped.filter(is_deleted=False, last_login__gte=recent).count(),
            never_logged_in=scoped.filter(is_deleted=False, last_login__isnull=True).count(), **kwargs)


class StaffDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'users.view_user'
    template_name = 'users/staff_detail.html'
    context_object_name = 'staff'

    def get_queryset(self):
        return staff_in_scope(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['can_manage'] = staff_to_manage(self.request.user).filter(pk=self.object.pk).exists()
        ctx['assignments'] = self.object.assignments.select_related('facility', 'district', 'region', 'zone')
        ctx['assignment_form'] = StaffAssignmentForm(user=self.request.user)
        ctx['identity_form'] = IdentityAccessForm(instance=self.object)
        ctx['role_gives_identity'] = self.object.groups.filter(
            permissions__codename='view_patient_identity', permissions__content_type__app_label='patients').exists()
        ctx['sees_identity'] = self.object.has_perm('patients.view_patient_identity')
        return ctx


class StaffCreateView(PermissionRequiredMixin, CreateView):
    permission_required = 'users.add_user'
    form_class = StaffCreateForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Create staff account',
                     'subtitle': 'With an email address, the staff member receives a secure link to set their own '
                                 'password. Without one, set a first password on the account page after saving.'}

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'editor': self.request.user}

    def form_valid(self, form):
        user = form.save(commit=False)
        user.set_unusable_password()
        user.save()
        form.save_m2m()
        self.object = user
        log_event(request=self.request, action=AuditEvent.Action.CREATE, obj=user,
                  changes={'groups': [g.name for g in user.groups.all()]})
        if not user.email:
            messages.success(self.request, f'Account "{user.username}" created without an email address. Use Set '
                                           'password to activate it, then assign a facility or geographic scope.')
            return redirect('users:detail', pk=user.pk)
        try:
            send_activation_email(user, sent_by=self.request.user)
        except Exception as exc:  # noqa: BLE001 - the account stays; the failure is in the email log
            messages.warning(self.request, f'Account created, but the activation email to {user.email} could not be '
                                           f'sent ({str(exc)[:150]}). Fix the email settings, then use Re-send activation.')
        else:
            messages.success(self.request, f'Account created. An activation email was sent to {user.email}. '
                                           'Now assign a facility or geographic scope.')
        return redirect('users:detail', pk=user.pk)

    def get_context_data(self, **kwargs):
        return super().get_context_data(cancel_url=reverse('users:list'), **kwargs)


class StaffUpdateView(PermissionRequiredMixin, UpdateView):
    permission_required = 'users.change_user'
    form_class = StaffUpdateForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Edit staff account'}

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'editor': self.request.user}

    def get_queryset(self):
        return staff_to_manage(self.request.user)

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        self._before = snapshot(obj)
        self._groups_before = sorted(g.name for g in obj.groups.all())
        self._diseases_before = sorted(d.name for d in obj.diseases.all())
        return obj

    def form_valid(self, form):
        response = super().form_valid(form)
        changes = diff(self._before, snapshot(self.object))
        groups_after = sorted(g.name for g in self.object.groups.all())
        if groups_after != self._groups_before:
            changes['groups'] = [self._groups_before, groups_after]
        diseases_after = sorted(d.name for d in self.object.diseases.all())
        if diseases_after != self._diseases_before:
            changes['diseases'] = [self._diseases_before, diseases_after]
        log_event(request=self.request, action=AuditEvent.Action.UPDATE, obj=self.object, changes=changes)
        messages.success(self.request, 'Staff account updated.')
        return response

    def get_success_url(self):
        return reverse('users:detail', args=[self.object.pk])

    def get_context_data(self, **kwargs):
        return super().get_context_data(cancel_url=self.get_success_url(), **kwargs)


class StaffToggleActiveView(PermissionRequiredMixin, View):
    """Deactivate/reactivate without deleting historical attribution."""

    permission_required = 'users.change_user'

    def post(self, request, pk):
        staff = get_object_or_404(staff_to_manage(request.user), pk=pk)
        if staff == request.user:
            messages.error(request, 'You cannot deactivate your own account.')
            return redirect('users:detail', pk=pk)
        if staff.is_deleted:
            messages.error(request, 'This account is deleted. A superuser can restore it first.')
            return redirect('users:detail', pk=pk)
        staff.is_active = not staff.is_active
        staff.save(update_fields=['is_active'])
        log_event(request=request, action=AuditEvent.Action.ACCOUNT, obj=staff,
                  changes={'is_active': [not staff.is_active, staff.is_active]}, reason=request.POST.get('reason', ''))
        messages.success(request, f'Account {"reactivated" if staff.is_active else "deactivated"}.')
        return redirect('users:detail', pk=pk)


class StaffSetPasswordView(PermissionRequiredMixin, View):
    """An administrator sets a first or new password (accounts without email, or a forgotten password).
    The person must choose their own password at the next login."""

    permission_required = 'users.change_user'
    template_name = 'confirm_action.html'

    def get_staff(self, request, pk):
        staff = get_object_or_404(staff_to_manage(request.user), pk=pk)
        if staff == request.user or (staff.is_superuser and not request.user.is_superuser):
            raise PermissionDenied
        return staff

    def render(self, request, staff, form):
        return render(request, self.template_name, {
            'title': f'Set password for {staff.display_name} ({staff.username})', 'form': form, 'button': 'Set password',
            'message': 'Use this when the person has no email address or cannot use the reset link. '
                       'They log in with this password once and must then choose their own.',
            'cancel_url': reverse('users:detail', args=[staff.pk])})

    def get(self, request, pk):
        staff = self.get_staff(request, pk)
        return self.render(request, staff, StaffSetPasswordForm(staff))

    def post(self, request, pk):
        staff = self.get_staff(request, pk)
        form = StaffSetPasswordForm(staff, request.POST)
        if not form.is_valid():
            return self.render(request, staff, form)
        staff = form.save(commit=False)
        staff.must_change_password = True
        staff.save()
        log_event(request=request, action=AuditEvent.Action.ACCOUNT, obj=staff,
                  reason='Password set by an administrator (must be changed at next login)')
        messages.success(request, f'Password set. {staff.display_name} must choose a new one at the next login.')
        return redirect('users:detail', pk=pk)


class StaffDeleteView(PermissionRequiredMixin, View):
    """Delete an account: it can no longer log in and moves to the Deleted tab. The account is kept so the records
    the person entered keep their name; a superuser can restore it."""

    permission_required = 'users.delete_user'

    def post(self, request, pk):
        staff = get_object_or_404(staff_to_manage(request.user), pk=pk, is_deleted=False)
        if staff == request.user or staff.is_superuser:
            messages.error(request, 'You cannot delete your own account or a superuser.')
            return redirect('users:detail', pk=pk)
        reason = request.POST.get('reason', '').strip()[:255]
        staff.is_deleted, staff.is_active = True, False
        staff.deleted_at, staff.deleted_by, staff.deletion_reason = timezone.now(), request.user, reason
        staff.save(update_fields=['is_deleted', 'is_active', 'deleted_at', 'deleted_by', 'deletion_reason'])
        log_event(request=request, action=AuditEvent.Action.DELETE, obj=staff,
                  reason=f'Account deleted: {staff.display_name} ({staff.username}). {reason}'.strip())
        messages.success(request, f'Account {staff.display_name} ({staff.username}) deleted. It is listed under Deleted.')
        return redirect('users:list')


class StaffRestoreView(PermissionRequiredMixin, View):
    """Superusers only: bring back a deleted account. It stays deactivated until someone reactivates it."""

    permission_required = 'users.delete_user'

    def post(self, request, pk):
        if not request.user.is_superuser:
            raise PermissionDenied
        staff = get_object_or_404(User, pk=pk, is_deleted=True)
        staff.is_deleted, staff.deleted_at, staff.deleted_by, staff.deletion_reason = False, None, None, ''
        staff.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason'])
        log_event(request=request, action=AuditEvent.Action.RESTORE, obj=staff, reason='Account restored')
        messages.success(request, f'Account {staff.username} restored. It is deactivated: reactivate it when the '
                                  'person should log in again.')
        return redirect('users:detail', pk=pk)


class StaffResendActivationView(PermissionRequiredMixin, View):
    permission_required = 'users.change_user'

    def post(self, request, pk):
        staff = get_object_or_404(staff_to_manage(request.user), pk=pk)
        if not staff.email or not staff.is_active:
            messages.error(request, 'The account needs an email address and must be active.')
        else:
            try:
                send_activation_email(staff, sent_by=request.user)
            except Exception as exc:  # noqa: BLE001 - recorded in the email log
                messages.error(request, f'The email to {staff.email} could not be sent: {str(exc)[:200]}')
            else:
                log_event(request=request, action=AuditEvent.Action.ACCOUNT, obj=staff, reason='Activation link re-sent')
                messages.success(request, f'A new set-password link was sent to {staff.email}.')
        return redirect('users:detail', pk=pk)


class AssignmentCreateView(PermissionRequiredMixin, View):
    permission_required = 'facilities.add_staffassignment'

    def post(self, request, pk):
        staff = get_object_or_404(staff_to_manage(request.user), pk=pk)
        form = StaffAssignmentForm(request.POST, user=request.user)
        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.user = staff
            assignment.created_by = request.user
            assignment.save()
            log_event(request=request, action=AuditEvent.Action.ACCOUNT, obj=assignment,
                      reason=f'Scope granted to {staff.username}')
            messages.success(request, 'Scope assignment added.')
        else:
            errors = '; '.join(f'{k}: {", ".join(v)}' for k, v in form.errors.items())
            messages.error(request, f'Assignment not saved. {errors}')
        return redirect('users:detail', pk=pk)


class AssignmentEndView(PermissionRequiredMixin, View):
    permission_required = 'facilities.change_staffassignment'

    def post(self, request, pk, assignment_pk):
        staff = get_object_or_404(staff_to_manage(request.user), pk=pk)
        assignment = get_object_or_404(StaffAssignment, pk=assignment_pk, user=staff)
        if not request.user.is_superuser and assignment.facility not in facilities_for_user(request.user):
            messages.error(request, 'This assignment is outside your scope.')
            return redirect('users:detail', pk=pk)
        assignment.is_active = False
        from django.utils import timezone
        assignment.end_date = timezone.localdate()
        assignment.save(update_fields=['is_active', 'end_date', 'updated_at'])
        log_event(request=request, action=AuditEvent.Action.ACCOUNT, obj=assignment,
                  reason=f'Scope ended for {staff.username}')
        messages.success(request, 'Scope assignment ended.')
        return redirect('users:detail', pk=pk)


class StaffBulkActivationView(PermissionRequiredMixin, View):
    """Send set-password links to every staff account in scope that has not been activated yet."""

    permission_required = 'users.change_user'
    template_name = 'users/bulk_activation.html'

    def pending(self, request):
        return pending_activation(staff_to_manage(request.user), migrated_only=request.GET.get('migrated') == '1'
                                  or request.POST.get('migrated') == '1')

    def get(self, request):
        from django.shortcuts import render
        migrated = request.GET.get('migrated') == '1'
        return render(request, self.template_name, {'pending': self.pending(request), 'migrated': migrated})

    def post(self, request):
        sent, failed = send_bulk_activation(self.pending(request), request.user, request)
        if sent:
            messages.success(request, f'Activation links sent to {len(sent)} staff member(s).')
        for user, reason in failed:
            messages.error(request, f'{user.username}: not sent ({reason}).')
        if not sent and not failed:
            messages.info(request, 'No staff accounts are waiting for activation.')
        return redirect('users:list')


class LoggedPasswordResetView(auth_views.PasswordResetView):
    form_class = LoggedPasswordResetForm
    html_email_template_name = 'registration/password_reset_email_html.html'

    @property
    def extra_email_context(self):
        from notifications.email import site_url
        return {'site_url': site_url()}

    def form_valid(self, form):
        from notifications.email import from_email
        self.from_email = from_email()
        return super().form_valid(form)


class AccountPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Activation / reset link. Whoever is logged in on this browser is logged out first: the link is for the
    account owner, and an administrator testing it must never set someone else's password from their own session."""

    @method_decorator(login_not_required)
    def dispatch(self, request, *args, **kwargs):
        # Only on the emailed URL (with the real token); the follow-up set-password URL keeps the token in the
        # new session, so logging out there would lose it.
        if request.user.is_authenticated and kwargs.get('token') != self.reset_url_token:
            name = request.user.get_username()
            logout(request)
            messages.info(request, f'You were logged out ({name}) because this link is for setting another '
                                   'account\'s password.')
        return super().dispatch(request, *args, **kwargs)


class IdentityAccessView(PermissionRequiredMixin, View):
    """Grant or remove access to patient names and identifiers for one person, overriding their role."""

    permission_required = 'users.manage_identity_access'

    def post(self, request, pk):
        staff = get_object_or_404(staff_to_manage(request.user), pk=pk)
        before = (staff.identity_access, staff.identity_access_until)
        form = IdentityAccessForm(request.POST, instance=staff)
        if form.is_valid():
            form.save()
            log_event(request=request, action=AuditEvent.Action.ACCOUNT, obj=staff,
                      changes={'identity_access': [before[0], staff.identity_access],
                               'identity_access_until': [str(before[1] or ''), str(staff.identity_access_until or '')]},
                      reason=staff.identity_access_reason or 'Identity access follows the role')
            messages.success(request, f'Identity access for {staff.username}: {staff.get_identity_access_display()}.')
        else:
            messages.error(request, '; '.join(e for errors in form.errors.values() for e in errors))
        return redirect('users:detail', pk=pk)


CAPABILITIES = [
    ('Subjects', [('See names, phones and identifiers', 'patients.view_patient_identity'),
                  ('Register subjects', 'patients.add_patient'), ('Record SMS consent', 'patients.record_sms_consent'),
                  ('Record death', 'patients.record_death')]),
    ('Care', [('Screening', 'clinical.add_screening'), ('Diagnosis & conditions', 'clinical.add_diagnosis'),
              ('Programme enrolment / status', 'clinical.change_programmeenrollment'),
              ('Encounters & vitals', 'encounters.add_encounter'), ('Appointments', 'encounters.add_appointment'),
              ('Admissions', 'encounters.add_admission'), ('Clinic days & SMS', 'encounters.add_clinicsession')]),
    ('Services', [('Order lab tests', 'laboratory.add_laborder'), ('Enter lab results', 'laboratory.change_laborder'),
                  ('Order investigations', 'investigations.add_investigationorder'),
                  ('Report ECG / echo / imaging', 'investigations.change_investigationorder'),
                  ('Prescribe', 'pharmacy.add_prescription'), ('Dispense', 'pharmacy.add_dispensing'),
                  ('Receive / adjust stock', 'pharmacy.add_stockbatch')]),
    ('Research & forms', [('Fill CRF forms', 'crf.add_formsubmission'), ('Review CRF forms', 'crf.review_formsubmission'),
                          ('Study consent & enrolment', 'research.add_studyenrollment')]),
    ('Data & reports', [('See and resolve data issues', 'issues.change_dataissue'), ('Reports & indicators', 'reporting.view_reports'),
                        ('Export data', 'reporting.export_data'), ('Audit trail', 'audit.view_auditevent'),
                        ('Correct / delete records (restore: superuser)', 'encounters.delete_encounter')]),
    ('Administration', [('Manage staff accounts', 'users.add_user'), ('Assign access levels', 'facilities.add_staffassignment'),
                        ('Grant / remove identity access', 'users.manage_identity_access'),
                        ('System setup (national level only)', 'clinical.change_disease')]),
]


class RoleMatrixView(PermissionRequiredMixin, TemplateView):
    """What each role can do, read from the role permissions themselves."""

    permission_required = 'users.view_user'
    template_name = 'users/role_matrix.html'

    def get_context_data(self, **kwargs):
        from django.contrib.auth.models import Group

        from core.roles import ROLES

        groups = {g.name: {f'{p.content_type.app_label}.{p.codename}' for p in g.permissions.select_related('content_type')}
                  for g in Group.objects.filter(name__in=ROLES).prefetch_related('permissions')}
        roles = [name for name in ROLES if name in groups]
        sections = [(title, [(label, [perm in groups[r] for r in roles]) for label, perm in items])
                    for title, items in CAPABILITIES]
        positions = Position.objects.select_related('default_role')
        counts = dict(User.objects.filter(is_active=True).values_list('groups__name').annotate(n=Count('id')))
        return super().get_context_data(roles=[(r, counts.get(r, 0)) for r in roles], sections=sections,
                                        positions=positions, **kwargs)


class PositionListView(PermissionRequiredMixin, ListView):
    permission_required = 'users.view_position'
    template_name = 'users/position_list.html'

    def get_queryset(self):
        return Position.objects.select_related('default_role').annotate(
            staff_count=Count('staff', filter=Q(staff__is_active=True)))


class PositionForm(BootstrapModelForm):
    class Meta:
        model = Position
        fields = ['name', 'code', 'default_role', 'typical_level', 'description', 'order', 'is_active']


class PositionSaveMixin(PermissionRequiredMixin):
    model = Position
    form_class = PositionForm
    template_name = 'generic_form.html'
    success_url = reverse_lazy('users:positions')

    def form_valid(self, form):
        response = super().form_valid(form)
        log_event(request=self.request, action=self.audit_action, obj=self.object,
                  changes={f: str(form.cleaned_data[f]) for f in form.changed_data})
        return response

    def get_context_data(self, **kwargs):
        return super().get_context_data(cancel_url=reverse('users:positions'), **kwargs)


class PositionCreateView(PositionSaveMixin, CreateView):
    permission_required = 'users.add_position'
    audit_action = AuditEvent.Action.CREATE
    extra_context = {'title': 'Add position'}


class PositionUpdateView(PositionSaveMixin, UpdateView):
    permission_required = 'users.change_position'
    audit_action = AuditEvent.Action.UPDATE
    extra_context = {'title': 'Edit position', 'subtitle': 'Untick "active" to stop offering it for new staff.'}


class ProfileView(UpdateView):
    """The logged-in person's own profile: edit contact details, see the access an administrator gave them."""

    form_class = ProfileForm
    template_name = 'users/profile.html'
    success_url = reverse_lazy('users:profile')

    def get_object(self, queryset=None):
        # A fresh copy, so a form with errors does not change the name shown in the menu.
        user = User.objects.get(pk=self.request.user.pk)
        self._audit_before = snapshot(user)
        return user

    def form_valid(self, form):
        if 'phone' in form.changed_data:
            form.instance.phone_verified = False
        response = super().form_valid(form)
        changes = diff(self._audit_before, snapshot(self.object))
        if changes:
            log_event(request=self.request, action=AuditEvent.Action.UPDATE, obj=self.object, changes=changes)
        messages.success(self.request, 'Your profile was saved.')
        return response

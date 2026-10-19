from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from audit.models import AuditEvent
from audit.services import diff, log_event, snapshot
from facilities.models import StaffAssignment
from facilities.scope import facilities_for_user

from .forms import StaffAssignmentForm, StaffCreateForm, StaffUpdateForm
from .models import User
from .services import send_activation_email


def staff_in_scope(user):
    """Staff accounts the user may manage: those assigned within the user's facilities."""
    qs = User.objects.all()
    if user.is_superuser:
        return qs
    facilities = facilities_for_user(user)
    return qs.filter(Q(assignments__facility__in=facilities) | Q(pk=user.pk)).filter(is_superuser=False).distinct()


class StaffListView(PermissionRequiredMixin, ListView):
    permission_required = 'users.view_user'
    template_name = 'users/staff_list.html'
    paginate_by = 25

    def get_queryset(self):
        qs = staff_in_scope(self.request.user).prefetch_related('groups')
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
        status = self.request.GET.get('status')
        if status in ('active', 'inactive'):
            qs = qs.filter(is_active=status == 'active')
        return qs.order_by('username')


class StaffDetailView(PermissionRequiredMixin, DetailView):
    permission_required = 'users.view_user'
    template_name = 'users/staff_detail.html'
    context_object_name = 'staff'

    def get_queryset(self):
        return staff_in_scope(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['assignments'] = self.object.assignments.select_related('facility', 'district', 'region', 'zone')
        ctx['assignment_form'] = StaffAssignmentForm(user=self.request.user)
        return ctx


class StaffCreateView(PermissionRequiredMixin, CreateView):
    permission_required = 'users.add_user'
    form_class = StaffCreateForm
    template_name = 'generic_form.html'
    extra_context = {'title': 'Create staff account',
                     'subtitle': 'The staff member receives an email with a secure link to set their own password.'}

    def form_valid(self, form):
        user = form.save(commit=False)
        user.set_unusable_password()
        user.save()
        form.save_m2m()
        self.object = user
        log_event(request=self.request, action=AuditEvent.Action.CREATE, obj=user,
                  changes={'groups': [g.name for g in user.groups.all()]})
        send_activation_email(user)
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

    def get_queryset(self):
        return staff_in_scope(self.request.user)

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        self._before = snapshot(obj)
        self._groups_before = sorted(g.name for g in obj.groups.all())
        return obj

    def form_valid(self, form):
        response = super().form_valid(form)
        changes = diff(self._before, snapshot(self.object))
        groups_after = sorted(g.name for g in self.object.groups.all())
        if groups_after != self._groups_before:
            changes['groups'] = [self._groups_before, groups_after]
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
        staff = get_object_or_404(staff_in_scope(request.user), pk=pk)
        if staff == request.user:
            messages.error(request, 'You cannot deactivate your own account.')
            return redirect('users:detail', pk=pk)
        staff.is_active = not staff.is_active
        staff.save(update_fields=['is_active'])
        log_event(request=request, action=AuditEvent.Action.ACCOUNT, obj=staff,
                  changes={'is_active': [not staff.is_active, staff.is_active]}, reason=request.POST.get('reason', ''))
        messages.success(request, f'Account {"reactivated" if staff.is_active else "deactivated"}.')
        return redirect('users:detail', pk=pk)


class StaffResendActivationView(PermissionRequiredMixin, View):
    permission_required = 'users.change_user'

    def post(self, request, pk):
        staff = get_object_or_404(staff_in_scope(request.user), pk=pk)
        if not staff.email or not staff.is_active:
            messages.error(request, 'The account needs an email address and must be active.')
        else:
            send_activation_email(staff)
            log_event(request=request, action=AuditEvent.Action.ACCOUNT, obj=staff, reason='Activation link re-sent')
            messages.success(request, f'A new set-password link was sent to {staff.email}.')
        return redirect('users:detail', pk=pk)


class AssignmentCreateView(PermissionRequiredMixin, View):
    permission_required = 'facilities.add_staffassignment'

    def post(self, request, pk):
        staff = get_object_or_404(staff_in_scope(request.user), pk=pk)
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
        staff = get_object_or_404(staff_in_scope(request.user), pk=pk)
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

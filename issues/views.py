import calendar
import datetime

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from audit.models import AuditEvent
from audit.services import log_event
from encounters.models import Appointment, Encounter
from facilities.scope import facilities_for_user

from .detectors import MONTHLY_STAGES, visits_same_month
from .models import DataIssue


class IssueListView(PermissionRequiredMixin, ListView):
    permission_required = 'issues.view_dataissue'
    template_name = 'issues/issue_list.html'
    paginate_by = 50

    def base(self):
        return DataIssue.objects.in_scope(self.request.user)

    def get_queryset(self):
        qs = self.base().select_related('patient', 'facility', 'resolved_by')
        params = self.request.GET
        status = params.get('status', 'open')
        if status:
            qs = qs.filter(status=status)
        for key in ('category', 'code'):
            if params.get(key):
                qs = qs.filter(**{key: params[key]})
        if params.get('facility'):
            qs = qs.filter(Q(patient__current_facility_id=params['facility']) | Q(facility_id=params['facility']))
        if params.get('q'):
            q = params['q'].strip()
            qs = qs.filter(Q(patient__patient_number__icontains=q) | Q(title__icontains=q)
                           | (Q(patient__old_id=int(q)) if q.isdigit() else Q()))
        return qs

    def get_context_data(self, **kwargs):
        open_issues = self.base().open()
        by_category = dict(open_issues.values_list('category').annotate(n=Count('id')))
        categories = [(value, label, by_category.get(value, 0)) for value, label in DataIssue.Category.choices]
        codes = open_issues.values_list('code', flat=True).distinct().order_by('code')
        return super().get_context_data(categories=categories, codes=codes, statuses=DataIssue.Status.choices,
                                        current_status=self.request.GET.get('status', 'open'),
                                        facilities=facilities_for_user(self.request.user),
                                        open_total=open_issues.count(), **kwargs)


def month_records(issue):
    first = issue.related_date
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    encounters = (Encounter.objects.filter(patient=issue.patient, encounter_date__date__range=(first, last))
                  .exclude(status='cancelled').select_related('appointment', 'facility').order_by('encounter_date'))
    appointments = (Appointment.objects.filter(patient=issue.patient, scheduled_date__range=(first, last))
                    .order_by('scheduled_date'))
    return encounters, appointments


class IssueDetailView(PermissionRequiredMixin, View):
    permission_required = 'issues.view_dataissue'

    def get(self, request, pk):
        issue = get_object_or_404(DataIssue.objects.in_scope(request.user).select_related('patient'), pk=pk)
        context = {'issue': issue, 'patient': issue.patient}
        if issue.code == 'visits_same_month' and issue.patient_id and issue.related_date:
            context['encounters'], context['appointments'] = month_records(issue)
            context['monthly_stages'] = MONTHLY_STAGES
        if issue.code == 'location_not_in_national_list' and issue.related is not None:
            from facilities.locations import suggestions
            location = issue.related
            parent_field = {'District': 'region', 'Ward': 'district'}[type(location).__name__]
            siblings = type(location).objects.filter(**{parent_field: getattr(location, parent_field)}).exclude(pk=location.pk)
            context['merge_candidates'] = siblings.order_by('name')
            context['merge_suggestions'] = suggestions(location.name, list(siblings))
            context['merge_level'] = type(location).__name__.lower()
        return render(request, 'issues/issue_detail.html', context)


class IssueResolveView(PermissionRequiredMixin, View):
    permission_required = 'issues.change_dataissue'

    def post(self, request, pk):
        issue = get_object_or_404(DataIssue.objects.in_scope(request.user), pk=pk)
        decision = request.POST.get('decision')
        note = request.POST.get('note', '').strip()
        if decision not in ('resolved', 'dismissed', 'open'):
            raise PermissionDenied
        if decision != 'open' and not note:
            messages.error(request, 'Write a short note on what was done (or why no change is needed).')
            return redirect(issue.get_absolute_url())
        old = issue.status
        issue.status = decision
        issue.resolution_note = note if decision != 'open' else issue.resolution_note
        issue.resolved_by = request.user if decision != 'open' else None
        issue.resolved_at = timezone.now() if decision != 'open' else None
        issue.save()
        log_event(request=request, action=AuditEvent.Action.REVIEW, obj=issue, changes={'status': [old, decision]},
                  reason=note)
        messages.success(request, f'Issue marked {issue.get_status_display().lower()}.')
        return redirect(request.POST.get('next') or 'issues:list')


class VisitClassifyView(PermissionRequiredMixin, View):
    """Mark a visit as the scheduled monthly visit or as an unscheduled visit."""

    permission_required = ('issues.change_dataissue', 'encounters.change_encounter')

    def post(self, request, pk, encounter_pk):
        issue = get_object_or_404(DataIssue.objects.in_scope(request.user), pk=pk, code='visits_same_month')
        encounter = get_object_or_404(Encounter, pk=encounter_pk, patient=issue.patient)
        target = request.POST.get('as')
        old_stage = encounter.stage
        if target == 'unscheduled' and encounter.stage != 'unscheduled':
            appointment = encounter.appointment
            encounter.appointment, encounter.stage = None, 'unscheduled'
            encounter.save(update_fields=['appointment', 'stage', 'updated_at'])
            if appointment is not None and not appointment.encounters.exists():
                appointment.status = Appointment.Status.CANCELLED
                appointment.reason_for_change = 'Visit reclassified as unscheduled during data review'
                appointment.save(update_fields=['status', 'reason_for_change', 'updated_at'])
            log_event(request=request, action=AuditEvent.Action.UPDATE, obj=encounter,
                      changes={'stage': [old_stage, 'unscheduled'], 'scheduled': [appointment is not None, False]},
                      reason=f'Data issue {issue.pk}')
            messages.success(request, 'Visit marked as unscheduled.')
        elif target == 'scheduled' and not encounter.appointment_id:
            day = timezone.localtime(encounter.encounter_date).date()
            if encounter.stage == 'unscheduled':
                encounter.stage = 'follow_up'
            appointment = Appointment.objects.create(
                patient=encounter.patient, facility=encounter.facility, scheduled_date=day, stage=encounter.stage,
                location_type=encounter.location_type, status=Appointment.Status.ATTENDED,
                notes='Scheduled visit confirmed during data review', created_by=request.user)
            encounter.appointment = appointment
            encounter.save(update_fields=['appointment', 'stage', 'updated_at'])
            log_event(request=request, action=AuditEvent.Action.UPDATE, obj=encounter,
                      changes={'stage': [old_stage, encounter.stage], 'scheduled': [False, True]},
                      reason=f'Data issue {issue.pk}')
            messages.success(request, 'Visit marked as the scheduled monthly visit.')
        visits_same_month([issue.patient_id])
        issue.refresh_from_db()
        if issue.status == DataIssue.Status.RESOLVED and issue.resolved_by_id is None:
            issue.resolved_by = request.user
            issue.resolution_note = 'One follow-up visit kept for the month; the others marked Unscheduled.'
            issue.save(update_fields=['resolved_by', 'resolution_note', 'updated_at'])
            log_event(request=request, action=AuditEvent.Action.REVIEW, obj=issue,
                      changes={'status': ['open', 'resolved']}, reason=issue.resolution_note)
            messages.info(request, 'Only one follow-up visit is left in this month, so the issue is resolved.')
        return redirect(issue.get_absolute_url())


class LocationMergeView(PermissionRequiredMixin, View):
    """Merge a district/ward that is not in the national list into the correct one (all records move)."""

    permission_required = ('issues.change_dataissue', 'facilities.merge_locations')

    def post(self, request, pk):
        from facilities.locations import merge_location, usage
        issue = get_object_or_404(DataIssue.objects.in_scope(request.user), pk=pk, code='location_not_in_national_list',
                                  status='open')
        old = issue.related
        target = type(old).objects.filter(pk=request.POST.get('target')).first() if old else None
        if old is None or target is None:
            messages.error(request, 'Choose the location to merge into.')
            return redirect(issue.get_absolute_url())
        moved = usage(old)
        label = f'{type(old).__name__.lower()} "{old.name}" into "{target.name}"'
        merge_location(old, target)
        issue.status, issue.resolved_by, issue.resolved_at = 'resolved', request.user, timezone.now()
        issue.resolution_note = f'Merged {label}. Moved: ' + (', '.join(f'{n} {k}' for k, n in moved.items()) or 'nothing')
        issue.related = target
        issue.save()
        log_event(request=request, action=AuditEvent.Action.UPDATE, obj=target, changes={'merged': moved},
                  reason=issue.resolution_note)
        messages.success(request, issue.resolution_note)
        return redirect(issue.get_absolute_url())

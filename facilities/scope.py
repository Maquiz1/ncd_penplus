from django.db.models import Q
from django.utils import timezone


def effective_assignments(user):
    today = timezone.localdate()
    return user.assignments.filter(is_active=True, start_date__lte=today).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    )


LEVEL_ORDER = ['facility', 'district', 'region', 'zone', 'national']


def highest_level(user):
    """Widest level among the user's effective assignments ('national' for superusers, None without any)."""
    if user.is_superuser:
        return 'national'
    levels = set(effective_assignments(user).values_list('level', flat=True))
    return next((level for level in reversed(LEVEL_ORDER) if level in levels), None)


def facilities_for_user(user):
    """Facilities the user may access, computed from effective assignments.

    Superusers see everything; users without an assignment see nothing.
    """
    from .models import Facility

    if not user or not user.is_authenticated:
        return Facility.objects.none()
    if user.is_superuser:
        return Facility.objects.all()
    cache = getattr(user, '_facility_scope_cache', None)
    if cache is not None:
        return Facility.objects.filter(pk__in=cache)
    condition = Q()
    for assignment in effective_assignments(user):
        if assignment.level == 'national':
            condition = Q()
            ids = list(Facility.objects.values_list('pk', flat=True))
            user._facility_scope_cache = ids
            return Facility.objects.filter(pk__in=ids)
        condition |= {
            'facility': Q(pk=assignment.facility_id),
            'district': Q(district_id=assignment.district_id),
            'region': Q(district__region_id=assignment.region_id),
            'zone': Q(district__region__zone_id=assignment.zone_id),
        }[assignment.level]
    ids = list(Facility.objects.filter(condition).values_list('pk', flat=True)) if condition else []
    user._facility_scope_cache = ids
    return Facility.objects.filter(pk__in=ids)


def disease_ids_for_user(user):
    """Disease ids the user is assigned to, or None when the user works on all diseases."""
    if not user or not user.is_authenticated or user.is_superuser:
        return None
    cache = getattr(user, '_disease_scope_cache', False)
    if cache is False:
        cache = list(user.diseases.values_list('pk', flat=True)) or None
        user._disease_scope_cache = cache
    return cache


def patients_for_diseases(disease_ids):
    """Patients not yet enrolled in any programme, or enrolled in one of these diseases."""
    from django.db.models import Exists, OuterRef

    from clinical.models import ProgrammeEnrollment
    from patients.models import Patient

    enrollments = ProgrammeEnrollment.objects.filter(patient=OuterRef('pk'))
    return Patient.all_objects.filter(~Exists(enrollments) | Exists(enrollments.filter(programme__disease__in=disease_ids)))


def scope_queryset(queryset, user, field):
    """Restrict a queryset to rows whose `field` facility is within the user's scope.

    When `field` goes through the patient (e.g. 'patient__current_facility'), rows are also limited to the
    diseases the user is assigned to.
    """
    if user.is_superuser:
        return queryset
    queryset = queryset.filter(**{f'{field}__in': facilities_for_user(user)})
    disease_ids = disease_ids_for_user(user)
    if disease_ids and field.endswith('current_facility'):
        prefix = field[:-len('current_facility')]
        queryset = queryset.filter(**{f'{prefix}pk__in': patients_for_diseases(disease_ids).values('pk')})
    return queryset


def scope_description(user):
    if user.is_superuser:
        return 'National (system administrator)'
    parts = [str(a) for a in effective_assignments(user).select_related('facility', 'district', 'region', 'zone')]
    return '; '.join(parts) or 'No assigned scope'


def default_facility(user):
    assignment = effective_assignments(user).filter(level='facility').select_related('facility').first()
    if assignment:
        return assignment.facility
    return facilities_for_user(user).first()

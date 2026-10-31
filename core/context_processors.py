from django.conf import settings
from django.utils.functional import SimpleLazyObject


def site(request):
    user = getattr(request, 'user', None)
    return {
        'SITE_NAME': settings.SITE_NAME,
        'access_level_label': access_level_label(user),
        'can_view_identity': bool(user and user.is_authenticated and user.has_perm('patients.view_patient_identity')),
        'service_counts': SimpleLazyObject(lambda: service_counts(user)),
    }


def service_counts(user):
    """Work waiting in the Laboratory and Radiology menus, within the user's scope (counted only when shown)."""
    counts = {}
    if not (user and user.is_authenticated):
        return counts
    from facilities.scope import scope_queryset
    if user.has_perm('laboratory.view_laborder'):
        from laboratory.views import request_tab_counts
        counts.update({f'lab_{k}': v for k, v in request_tab_counts(user).items()})
    if user.has_perm('investigations.view_investigationorder'):
        from django.db.models import Count, Q

        from investigations.models import InvestigationOrder
        counts.update(scope_queryset(InvestigationOrder.objects.all(), user, 'patient__current_facility').aggregate(
            rad_pending=Count('pk', filter=Q(status__in=['requested', 'scheduled'])),
            rad_review=Count('pk', filter=Q(status='completed'))))
    return counts


def access_level_label(user):
    """Short label for the navbar, e.g. 'District' or 'Facility: Kondoa DH'."""
    if not (user and user.is_authenticated):
        return ''
    from facilities.scope import effective_assignments, highest_level
    level = highest_level(user)
    if level is None:
        return 'No access level'
    items = [a for a in effective_assignments(user).select_related('facility', 'district', 'region', 'zone') if a.level == level]
    return str(items[0]) if len(items) == 1 else f'{items[0].get_level_display()} ({len(items)})' if items else 'National'

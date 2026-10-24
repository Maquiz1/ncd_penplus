from django.conf import settings


def site(request):
    user = getattr(request, 'user', None)
    return {
        'SITE_NAME': settings.SITE_NAME,
        'access_level_label': access_level_label(user),
        'can_view_identity': bool(user and user.is_authenticated and user.has_perm('patients.view_patient_identity')),
    }


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

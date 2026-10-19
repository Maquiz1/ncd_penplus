from django.conf import settings


def site(request):
    user = getattr(request, 'user', None)
    return {
        'SITE_NAME': settings.SITE_NAME,
        'can_view_identity': bool(user and user.is_authenticated and user.has_perm('patients.view_patient_identity')),
    }

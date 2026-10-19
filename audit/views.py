from django.contrib.auth.mixins import PermissionRequiredMixin
from django.views.generic import ListView

from patients.models import IDENTITY_FIELDS

from .models import AuditEvent

MASK = '••• restricted •••'


def visible_changes(event, can_view_identity):
    hidden = set() if can_view_identity else IDENTITY_FIELDS.get(event.model, set())
    return {key: MASK if key in hidden else value for key, value in (event.changes or {}).items()}


class AuditEventListView(PermissionRequiredMixin, ListView):
    permission_required = 'audit.view_auditevent'
    model = AuditEvent
    paginate_by = 50
    template_name = 'audit/event_list.html'

    def get_queryset(self):
        qs = super().get_queryset().select_related('actor')
        params = self.request.GET
        if params.get('action'):
            qs = qs.filter(action=params['action'])
        if params.get('q'):
            qs = qs.filter(object_repr__icontains=params['q']) | qs.filter(actor__username__icontains=params['q'])
        if params.get('model'):
            qs = qs.filter(model__icontains=params['model'])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(actions=AuditEvent.Action.choices, **kwargs)
        can_view_identity = self.request.user.has_perm('patients.view_patient_identity')
        for event in ctx['object_list']:
            event.visible_changes = visible_changes(event, can_view_identity)
        return ctx

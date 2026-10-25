from django.contrib.auth.models import AbstractUser, Group
from django.db import models
from django.utils import timezone


class Position(models.Model):
    """Job position (what the person is). The role gives permissions and the assignment gives the level."""

    class Level(models.TextChoices):
        FACILITY = 'facility', 'Facility'
        DISTRICT = 'district', 'District'
        REGION = 'region', 'Region'
        ZONE = 'zone', 'Zone'
        NATIONAL = 'national', 'National'

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100, unique=True)
    default_role = models.ForeignKey(Group, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                     help_text='Role given to new staff with this position when no role is ticked.')
    typical_level = models.CharField(max_length=20, choices=Level.choices, default=Level.FACILITY,
                                     help_text='Usual access level; the actual level is set by the assignment.')
    description = models.CharField(max_length=255, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    """Staff account. Position, roles (groups) and access level (assignments) are kept separate."""

    class IdentityAccess(models.TextChoices):
        ROLE = 'role', 'As the role allows'
        GRANT = 'grant', 'Allowed (granted for this person)'
        REVOKE = 'revoke', 'Not allowed (removed for this person)'

    class Prefix(models.TextChoices):
        DR = 'Dr.', 'Dr.'
        PROF = 'Prof.', 'Prof.'
        MR = 'Mr.', 'Mr.'
        MRS = 'Mrs.', 'Mrs.'
        MS = 'Ms.', 'Ms.'
        SR = 'Sr.', 'Sr. (Sister)'

    prefix = models.CharField(max_length=10, choices=Prefix.choices, blank=True,
                              help_text='Shown before the name, e.g. in the welcome message.')
    phone = models.CharField(max_length=30, blank=True)
    phone_verified = models.BooleanField(default=False)
    position = models.ForeignKey(Position, on_delete=models.PROTECT, null=True, blank=True, related_name='staff')
    job_title = models.CharField(max_length=100, blank=True, help_text='Optional; shown when no position is set.')
    diseases = models.ManyToManyField('clinical.Disease', blank=True, related_name='staff',
                                      help_text='Diseases this person works on. Leave empty for all diseases.')
    identity_access = models.CharField(
        'Patient names and identifiers', max_length=10, choices=IdentityAccess.choices, default=IdentityAccess.ROLE)
    identity_access_reason = models.CharField(max_length=255, blank=True)
    identity_access_until = models.DateField(null=True, blank=True,
                                             help_text='A grant or removal ends on this date (blank = no end).')

    class Meta:
        ordering = ['username']
        permissions = [('manage_identity_access', 'Can grant or remove access to patient identity for a person')]

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def position_label(self):
        return self.position.name if self.position_id else self.job_title

    @property
    def display_name(self):
        """Name with prefix, e.g. 'Dr. Asha Mushi'."""
        return ' '.join(part for part in (self.prefix, self.get_full_name() or self.username) if part)

    @property
    def role_label(self):
        """Position if set, otherwise the roles (groups)."""
        return self.position_label or ', '.join(self.groups.values_list('name', flat=True))

    @property
    def disease_summary(self):
        names = [d.name for d in self.diseases.all()]
        return ', '.join(names) if names and not self.is_superuser else 'All diseases'

    @property
    def access_summary(self):
        """Effective assignments, widest first, e.g. 'District: Kondoa · Facility: Karatu DH'."""
        if self.is_superuser:
            return 'National (superuser)'
        from facilities.scope import LEVEL_ORDER, effective_assignments
        items = sorted(effective_assignments(self).select_related('facility', 'district', 'region', 'zone'),
                       key=lambda a: -LEVEL_ORDER.index(a.level))
        return ' · '.join(str(a) for a in items) or 'No access level'

    @property
    def identity_override(self):
        """'grant', 'revoke' or None while an override is in force."""
        if self.identity_access == self.IdentityAccess.ROLE:
            return None
        if self.identity_access_until and self.identity_access_until < timezone.localdate():
            return None
        return self.identity_access

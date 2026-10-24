from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class Zone(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Region(models.Model):
    zone = models.ForeignKey(Zone, on_delete=models.PROTECT, related_name='regions')
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class District(models.Model):
    region = models.ForeignKey(Region, on_delete=models.PROTECT, related_name='districts')
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['region', 'name'], name='unique_district_per_region')]

    def __str__(self):
        return f'{self.name} ({self.region})'


class Ward(models.Model):
    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name='wards')
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['district', 'name'], name='unique_ward_per_district')]

    def __str__(self):
        return f'{self.name} ({self.district.name})'


class Street(models.Model):
    """Street (mtaa) or village (kijiji): the lowest address level."""

    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, related_name='streets')
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ['name']
        permissions = [('merge_locations', 'Can merge duplicate locations into the national list')]
        verbose_name = 'street / village'
        verbose_name_plural = 'streets / villages'
        constraints = [models.UniqueConstraint(fields=['ward', 'name'], name='unique_street_per_ward')]

    def __str__(self):
        return self.name


class Facility(models.Model):
    class FacilityType(models.TextChoices):
        HOSPITAL = 'hospital', 'Hospital'
        HEALTH_CENTRE = 'health_centre', 'Health centre'
        DISPENSARY = 'dispensary', 'Dispensary'
        RESEARCH_SITE = 'research_site', 'Research site'
        OTHER = 'other', 'Other'

    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name='facilities')
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=30, unique=True)
    facility_type = models.CharField(max_length=20, choices=FacilityType.choices, default=FacilityType.HOSPITAL)
    is_research_site = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'facilities'

    def __str__(self):
        return self.name


class StaffAssignment(TimeStampedModel):
    """Effective-dated access scope for a staff member.

    The level decides which facilities the user can see: one facility, every
    facility in a district/region/zone, or national (all facilities).
    """

    class Level(models.TextChoices):
        FACILITY = 'facility', 'Facility'
        DISTRICT = 'district', 'District'
        REGION = 'region', 'Region'
        ZONE = 'zone', 'Zone'
        NATIONAL = 'national', 'National'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assignments')
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.FACILITY)
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, null=True, blank=True, related_name='staff_assignments')
    district = models.ForeignKey(District, on_delete=models.PROTECT, null=True, blank=True)
    region = models.ForeignKey(Region, on_delete=models.PROTECT, null=True, blank=True)
    zone = models.ForeignKey(Zone, on_delete=models.PROTECT, null=True, blank=True)
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_active', 'level']

    def __str__(self):
        return f'{self.get_level_display()}: {self.target or "All facilities"}'

    @property
    def target(self):
        return {'facility': self.facility, 'district': self.district, 'region': self.region,
                'zone': self.zone}.get(self.level)

    def clean(self):
        required = {'facility': 'facility', 'district': 'district', 'region': 'region', 'zone': 'zone'}
        field = required.get(self.level)
        if field and getattr(self, f'{field}_id') is None:
            raise ValidationError({field: f'Select the {field} for a {self.get_level_display().lower()}-level assignment.'})
        for other in required.values():
            if other != field:
                setattr(self, other, None)
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})

    @property
    def is_effective(self):
        today = timezone.localdate()
        return self.is_active and self.start_date <= today and (self.end_date is None or self.end_date >= today)

"""Filters shared by every report: area (zone / region / district / site), period and disease."""
import datetime
from urllib.parse import urlencode

from django.utils import timezone

from core.utils import add_months
from facilities.models import District, Region, Zone
from facilities.scope import disease_ids_for_user, facilities_for_user

PERIODS = [('this_month', 'This month'), ('last_month', 'Last month'), ('this_quarter', 'This quarter'),
           ('last_quarter', 'Last quarter'), ('this_year', 'This year'), ('last_12', 'Last 12 months'),
           ('all', 'All time'), ('custom', 'Date range…')]


def period_dates(period, today=None):
    today = today or timezone.localdate()
    month = today.replace(day=1)
    quarter = today.replace(month=3 * ((today.month - 1) // 3) + 1, day=1)
    if period == 'this_month':
        return month, today
    if period == 'last_month':
        return add_months(month, -1), month - datetime.timedelta(days=1)
    if period == 'this_quarter':
        return quarter, today
    if period == 'last_quarter':
        return add_months(quarter, -3), quarter - datetime.timedelta(days=1)
    if period == 'this_year':
        return today.replace(month=1, day=1), today
    if period == 'last_12':
        return add_months(month, -11), today
    if period == 'all':
        return datetime.date(2000, 1, 1), today
    return None, None


def parse_date(value):
    try:
        return datetime.date.fromisoformat(value) if value else None
    except ValueError:
        return None


class ReportFilters:
    """Read the filters from the query string, always within the user's own access scope."""

    def __init__(self, request, default_period='this_year', with_disease=True):
        get = request.GET
        self.user = request.user
        self.with_disease = with_disease
        self.scope = facilities_for_user(request.user)
        self.zones = Zone.objects.filter(regions__districts__facilities__in=self.scope).distinct()
        self.regions = Region.objects.filter(districts__facilities__in=self.scope).distinct()
        self.districts = District.objects.filter(facilities__in=self.scope).distinct()
        self.sites = self.scope.order_by('name')
        self.zone = self.zones.filter(pk=get.get('zone')).first() if get.get('zone') else None
        self.region = self.regions.filter(pk=get.get('region')).first() if get.get('region') else None
        self.district = self.districts.filter(pk=get.get('district')).first() if get.get('district') else None
        self.site = self.sites.filter(pk=get.get('site')).first() if get.get('site') else None
        facilities = self.scope
        if self.zone:
            facilities = facilities.filter(district__region__zone=self.zone)
        if self.region:
            facilities = facilities.filter(district__region=self.region)
        if self.district:
            facilities = facilities.filter(district=self.district)
        if self.site:
            facilities = facilities.filter(pk=self.site.pk)
        self.facilities = facilities
        self.period = get.get('period') or default_period
        if self.period not in dict(PERIODS):
            self.period = default_period
        start, end = period_dates(self.period)
        if self.period == 'custom':
            start = parse_date(get.get('start')) or timezone.localdate().replace(month=1, day=1)
            end = parse_date(get.get('end')) or timezone.localdate()
        self.start, self.end = start, end
        from clinical.models import Disease
        self.diseases = Disease.objects.filter(is_active=True)
        allowed = disease_ids_for_user(request.user)
        self.allowed_disease_ids = allowed
        if allowed:
            self.diseases = self.diseases.filter(pk__in=allowed)
        self.disease = self.diseases.filter(code=get.get('disease')).first() if with_disease and get.get('disease') else None
        if with_disease and allowed and self.disease is None and len(self.diseases) == 1:
            self.disease = self.diseases[0]
        self.periods = PERIODS

    @property
    def disease_ids(self):
        """Diseases a report must keep: the chosen one, else the user's own (None = all diseases)."""
        if self.disease is not None:
            return [self.disease.pk]
        return self.allowed_disease_ids

    @property
    def area_label(self):
        parts = [str(x) for x in (self.zone, self.region, self.district, self.site) if x]
        return ' · '.join(parts) or 'All sites in your scope'

    @property
    def period_label(self):
        if self.period == 'all':
            return 'All time'
        return f'{self.start:%d %b %Y} – {self.end:%d %b %Y}'

    @property
    def description(self):
        text = f'{self.area_label} · {self.period_label}'
        return f'{text} · {self.disease.name}' if self.disease else text

    def querystring(self, **extra):
        params = {k: v for k, v in {
            'zone': self.zone and self.zone.pk, 'region': self.region and self.region.pk,
            'district': self.district and self.district.pk, 'site': self.site and self.site.pk,
            'period': self.period, 'start': self.start if self.period == 'custom' else None,
            'end': self.end if self.period == 'custom' else None,
            'disease': self.disease and self.disease.code}.items() if v}
        params.update(extra)
        return urlencode(params)

from django import forms
from django.urls import reverse

from .models import District, Region, Street, Ward

LEVELS = ('region', 'district', 'ward', 'street')
MODELS = {'region': Region, 'district': District, 'ward': Ward, 'street': Street}
PARENT = {'district': 'region', 'ward': 'district', 'street': 'ward'}


class LocationFieldsMixin:
    """Linked region → district → ward → street/village pickers (Tom Select).

    `location_fields` maps level → form field name. Region is a helper field when the model stores only the
    district. Choices are limited to the chosen parent, so a ward always belongs to its district (as in herbal),
    and the browser loads the next level when a parent changes.
    """

    location_fields = {}
    location_labels = {'region': 'Region', 'district': 'District', 'ward': 'Ward', 'street': 'Street / village'}

    def setup_locations(self, default_district=None):
        fields = self.location_fields
        if 'region' in fields and fields['region'] not in self.fields:
            self.fields[fields['region']] = forms.ModelChoiceField(queryset=Region.objects.all(), required=False,
                                                                   label='Region')
        selected = {}
        for level in LEVELS:
            name = fields.get(level)
            if not name:
                continue
            raw = self.data.get(self.add_prefix(name)) if self.is_bound else None
            if raw in (None, '') and not self.is_bound:
                raw = getattr(self.instance, f'{name}_id', None) if hasattr(self.instance, f'{name}_id') else None
            selected[level] = str(raw) if raw not in (None, '') else ''
        if not self.is_bound and default_district and not selected.get('district'):
            selected['district'] = str(default_district.pk)
        if not selected.get('region') and selected.get('district'):
            selected['region'] = str(District.objects.filter(pk=selected['district']).values_list('region_id', flat=True).first() or '')
        for level in LEVELS:
            name = fields.get(level)
            if not name:
                continue
            field = self.fields[name]
            field.label = self.location_labels[level]
            parent = PARENT.get(level)
            if parent and fields.get(parent):
                parent_value = selected.get(parent)
                field.queryset = MODELS[level].objects.filter(**{f'{parent}_id': parent_value}) if parent_value \
                    else MODELS[level].objects.none()
            if not self.is_bound and selected.get(level):
                self.initial[name] = selected[level]
            field.widget.attrs.update({
                'data-location': level, 'data-url': reverse('facilities:location_options', args=[f'{level}s']),
                'data-placeholder': f'Choose {self.location_labels[level].lower()}…'})
            if parent and fields.get(parent):
                field.widget.attrs['data-parent'] = self.auto_id % self.add_prefix(fields[parent]) if self.auto_id else ''

    def clean(self):
        cleaned = super().clean()
        fields = self.location_fields
        for child, parent in (('district', 'region'), ('ward', 'district'), ('street', 'ward')):
            child_value, parent_value = cleaned.get(fields.get(child)), cleaned.get(fields.get(parent))
            if child_value is not None and parent_value is not None and getattr(child_value, f'{parent}_id') != parent_value.pk:
                self.add_error(fields[child], f'This {child} is not in the selected {parent}.')
            if child_value is not None and parent_value is None and fields.get(parent):
                cleaned[fields[parent]] = getattr(child_value, parent)
        return cleaned

    def order_location_fields(self, before):
        """Place the location fields together, just before `before`."""
        names = [self.location_fields[level] for level in LEVELS if level in self.location_fields]
        order = []
        for name in self.fields:
            if name == before:
                order.extend(names)
            if name not in names:
                order.append(name)
        self.order_fields(order)

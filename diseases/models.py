import re

from django.db import models

from core.models import TimeStampedModel


class Condition(TimeStampedModel):
    """A reporting condition within a disease (e.g. Type 1 diabetes, RHD), as used by the PEN-Plus indicators.

    Diagnoses are linked to a condition automatically from their description (keywords) and can be corrected.
    """

    disease = models.ForeignKey('clinical.Disease', on_delete=models.PROTECT, related_name='conditions')
    parent = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='children',
                               help_text='Leave empty for a main condition; choose one for a sub-type '
                                         '(e.g. Pure mitral stenosis under Rheumatic heart disease).')
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=150)
    indicator_code = models.CharField(max_length=20, blank=True,
                                      help_text='Row in the PEN-Plus reporting form, e.g. 1.1 for Type 1 diabetes.')
    keywords = models.TextField(blank=True, help_text='One per line. A diagnosis whose description contains any of '
                                                      'these words is linked to this condition.')
    is_default = models.BooleanField('Default for the disease', default=False,
                                     help_text='Used when no keyword matches (e.g. "Other cardiac condition").')
    excludes = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='+',
                                      help_text='Do not count a subject here if they also have one of these '
                                                '(e.g. Heart failure excludes RHD and congenital heart disease).')
    order = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['disease__order', 'order', 'name']

    def __str__(self):
        return self.name

    def keyword_list(self):
        return [k.strip().casefold() for k in self.keywords.splitlines() if k.strip()]

    @property
    def root(self):
        return self.parent.root if self.parent_id else self

    @property
    def label(self):
        return f'{self.parent.name} – {self.name}' if self.parent_id else self.name


def match_condition(description, disease, conditions=None):
    """The condition for a diagnosis description.

    First the main condition whose keywords appear (in order), then, within it, the most specific sub-type whose
    keywords appear. Without a match: the disease's default condition.
    """
    conditions = conditions if conditions is not None else list(
        Condition.objects.filter(disease=disease, is_active=True).select_related('parent'))
    conditions = sorted((c for c in conditions if c.disease_id == disease.pk), key=lambda c: (c.order, c.pk))
    text = re.sub(r'\s+', ' ', (description or '').casefold())
    for main in (c for c in conditions if c.parent_id is None):
        if any(k in text for k in main.keyword_list()):
            detail = text.split(' – ', 1)[1] if ' – ' in text else text
            for sub in (c for c in conditions if c.parent_id == main.pk):
                if any(k in detail for k in sub.keyword_list()):
                    return sub, True
            return main, True
    default = next((c for c in conditions if c.is_default and c.parent_id is None), None)
    return default, False

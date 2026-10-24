"""Tanzania address hierarchy: name cleaning, matching and merging of duplicate locations."""
import difflib
import re

from django.db import transaction

SMALL_WORDS = {'es', 'na', 'wa', 'ya', 'la', 'za', 'cha', 'kwa', 'mjini'}
UPPER_WORDS = {'CBD', 'DC', 'TC', 'MC', 'CC', 'JKT', 'TPDF', 'TAZARA'}
SPECIAL_NAMES = {'dar-es-salaam': 'Dar es Salaam', 'dar es salaam': 'Dar es Salaam'}

# Regions → zones (editable afterwards in admin).
REGION_ZONES = {
    'Northern Zone': ['Arusha', 'Kilimanjaro', 'Manyara', 'Tanga'],
    'Lake Zone': ['Mwanza', 'Mara', 'Kagera', 'Geita', 'Simiyu', 'Shinyanga'],
    'Central Zone': ['Dodoma', 'Singida'],
    'Eastern Zone': ['Dar es Salaam', 'Pwani', 'Morogoro'],
    'Southern Zone': ['Lindi', 'Mtwara'],
    'Southern Highlands Zone': ['Iringa', 'Njombe', 'Ruvuma'],
    'Western Zone': ['Kigoma', 'Tabora', 'Katavi'],
    'South-West Highlands Zone': ['Mbeya', 'Songwe', 'Rukwa'],
}


def clean_name(raw):
    """'MANG'OLA' → "Mang'ola", 'DODOMA\\nCBD' → 'Dodoma CBD', 'KONDOA MJINI' → 'Kondoa mjini'."""
    text = ' '.join((raw or '').replace('\n', ' ').split())
    if not text:
        return ''
    if text.casefold() in SPECIAL_NAMES:
        return SPECIAL_NAMES[text.casefold()]
    words = []
    for index, word in enumerate(text.split(' ')):
        if word.upper() in UPPER_WORDS:
            words.append(word.upper())
            continue
        word = '-'.join(part[:1].upper() + part[1:].lower() for part in word.split('-'))
        words.append(word.lower() if index and word.lower() in SMALL_WORDS else word)
    return ' '.join(words)


def name_key(name):
    """Comparison key: case, spaces and punctuation ignored ('Hondo Mairo' == 'HONDOMAIRO')."""
    return re.sub(r'[^a-z0-9]', '', (name or '').casefold())


def suggestions(name, candidates, limit=3):
    by_key = {name_key(c.name): c for c in candidates}
    close = difflib.get_close_matches(name_key(name), list(by_key), n=limit, cutoff=0.7)
    return [by_key[k] for k in close]


def incoming_links(model):
    """Every foreign key pointing at `model`, including hidden ones (related_name='+')."""
    return [f for f in model._meta.get_fields(include_hidden=True)
            if f.auto_created and not f.concrete and (f.one_to_many or f.one_to_one)]


def usage(obj):
    """How many records point at a location, per related model."""
    counts = {}
    for rel in incoming_links(type(obj)):
        n = rel.related_model._base_manager.filter(**{rel.field.name: obj}).count()
        if n:
            counts[str(rel.related_model._meta.verbose_name_plural)] = n
    return counts


@transaction.atomic
def merge_location(old, target):
    """Move everything that points at `old` to `target`, merging child locations with the same name, then
    delete `old`. Used to fix duplicates such as an EDCS ward spelled differently from the national list."""
    if type(old) is not type(target) or old.pk == target.pk:
        raise ValueError('Choose a different location of the same level.')
    for rel in incoming_links(type(old)):
        model, field = rel.related_model, rel.field.name
        is_child_location = model.__module__ == old.__module__ and hasattr(model, 'name') and model is not type(old) \
            and model.__name__ in ('District', 'Ward', 'Street')
        for child in list(model._base_manager.filter(**{field: old})):
            if is_child_location:
                twin = next((c for c in model._base_manager.filter(**{field: target}) if name_key(c.name) == name_key(child.name)), None)
                if twin is not None:
                    merge_location(child, twin)
                    continue
            setattr(child, field, target)
            child.save(update_fields=[field])
    old.delete()

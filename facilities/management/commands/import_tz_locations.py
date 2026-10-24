import csv
from collections import Counter, defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from facilities.locations import REGION_ZONES, clean_name, name_key, suggestions, usage
from facilities.models import District, Region, Street, Ward, Zone
from issues.detectors import raise_issue
from issues.models import DataIssue


class DryRun(Exception):
    pass


class Command(BaseCommand):
    help = ('Load the national list of regions, districts, wards and streets/villages (Tanzania mainland CSVs). '
            'Existing locations with the same name are kept and standardised; names that only nearly match are '
            'listed in Data issues for a data manager to merge. Safe to re-run.')

    def add_arguments(self, parser):
        parser.add_argument('--path', default=str(Path(settings.BASE_DIR) / 'data' / 'tz_locations'))
        parser.add_argument('--dry-run', action='store_true')

    def read(self, path):
        tree = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
        codes, ward_codes, skipped = {}, defaultdict(set), Counter()
        for file in sorted(Path(path).glob('*.csv')):
            with open(file, newline='', encoding='utf-8') as handle:
                for raw in csv.DictReader(handle):
                    row = {(k or '').strip().lower(): (v or '').strip() for k, v in raw.items()}
                    region, district, ward = (clean_name(row.get(c)) for c in ('region', 'district', 'ward'))
                    if not (region and district and ward):
                        skipped['rows without region/district/ward'] += 1
                        continue
                    codes.setdefault(('region', region), row.get('regioncode', ''))
                    codes.setdefault(('district', region, district), row.get('districtcode', ''))
                    ward_code = row.get('wardcode') or row.get('postcode')
                    if ward_code:
                        ward_codes[(region, district, ward)].add(ward_code)
                    street = clean_name(row.get('street'))
                    tree[region][district][ward]
                    if street:
                        tree[region][district][ward][street] = True
                    else:
                        skipped['rows without street (ward kept)'] += 1
        return tree, codes, ward_codes, skipped

    def handle(self, *args, path=None, dry_run=False, **options):
        tree, codes, ward_codes, skipped = self.read(path)
        counts = Counter(skipped)
        try:
            with transaction.atomic():
                self.load(tree, codes, ward_codes, counts)
                if dry_run:
                    raise DryRun
        except DryRun:
            self.stdout.write(self.style.WARNING('DRY RUN – nothing was saved.'))
        for key, value in sorted(counts.items()):
            self.stdout.write(f'  {key}: {value}')
        if not dry_run:
            self.stdout.write(self.style.SUCCESS('National location list loaded. Review "Locations & addresses" in Data issues.'))

    def load(self, tree, codes, ward_codes, counts):
        zone_of = {region: zone for zone, regions in REGION_ZONES.items() for region in regions}
        regions = {name_key(r.name): r for r in Region.objects.all()}
        for region_name, districts in tree.items():
            region = regions.get(name_key(region_name))
            if region is None:
                zone, _ = Zone.objects.get_or_create(name=zone_of.get(region_name, 'Unassigned zone'))
                region = Region.objects.create(name=region_name, zone=zone, code=codes.get(('region', region_name), ''))
                counts['regions added'] += 1
            elif not region.code and codes.get(('region', region_name)):
                region.code = codes[('region', region_name)]
                region.save(update_fields=['code'])
            existing_districts = {name_key(d.name): d for d in region.districts.all()}
            matched = set()
            for district_name, wards in districts.items():
                key = name_key(district_name)
                district = existing_districts.get(key)
                if district is None:
                    district = District.objects.create(region=region, name=district_name,
                                                       code=codes.get(('district', region_name, district_name), ''))
                    existing_districts[key] = district
                    counts['districts added'] += 1
                elif district.name != district_name:
                    district.name = district_name
                    district.save(update_fields=['name'])
                    counts['district names standardised'] += 1
                matched.add(district.pk)
                self.load_wards(region_name, district, wards, ward_codes, counts)
            for district in region.districts.exclude(pk__in=matched):
                self.unmatched(district, [d for d in region.districts.filter(pk__in=matched)], 'District',
                               f'{region.name} region', counts)

    def load_wards(self, region_name, district, wards, ward_codes, counts):
        existing = {name_key(w.name): w for w in district.wards.all()}
        matched = set()
        for ward_name, streets in wards.items():
            key = name_key(ward_name)
            code_set = ward_codes.get((region_name, district.name, ward_name), set())
            code = sorted(code_set)[0] if code_set else ''
            if len(code_set) > 1:
                _, new = raise_issue(f'loc:ward-codes:{district.pk}:{key}', 'location_code_conflict',
                                     DataIssue.Category.LOCATIONS, f'Ward {ward_name} has several codes',
                                     f'The national list gives {", ".join(sorted(code_set))} for {ward_name} '
                                     f'({district.name}). Check the official code; {code} was used.')
                counts['issues: ward code conflicts'] += new
            ward = existing.get(key)
            if ward is None:
                ward = Ward.objects.create(district=district, name=ward_name, code=code)
                existing[key] = ward
                counts['wards added'] += 1
            elif ward.name != ward_name or (code and not ward.code):
                ward.name, ward.code = ward_name, ward.code or code
                ward.save(update_fields=['name', 'code'])
                counts['ward names standardised'] += 1
            matched.add(ward.pk)
            have = set(name_key(n) for n in ward.streets.values_list('name', flat=True))
            new_streets = [Street(ward=ward, name=s) for s in streets if name_key(s) not in have]
            Street.objects.bulk_create(new_streets, batch_size=2000, ignore_conflicts=True)
            counts['streets / villages added'] += len(new_streets)
        for ward in district.wards.exclude(pk__in=matched):
            self.unmatched(ward, list(district.wards.filter(pk__in=matched)), 'Ward', f'{district.name} district', counts)

    def unmatched(self, obj, candidates, level, parent, counts):
        close = suggestions(obj.name, candidates)
        used = usage(obj)
        used_text = ', '.join(f'{n} {label}' for label, n in used.items()) or 'not used by any record yet'
        _, new = raise_issue(
            f'loc:{level.lower()}:{obj.pk}', 'location_not_in_national_list', DataIssue.Category.LOCATIONS,
            f'{level} "{obj.name}" ({parent}) is not in the national list',
            (f'Possibly the same as: {", ".join(c.name for c in close)}. ' if close else 'No similar name found. ')
            + f'Used by: {used_text}. Merge it into the correct {level.lower()} (all records move), or dismiss if '
              f'it is correct.', obj=obj)
        counts[f'issues: {level.lower()}s not in national list'] += new

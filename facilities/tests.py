import datetime
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.urls import reverse

from clinical.models import ClinicalProgramme, ProgrammeEnrollment
from core.tests import BaseTestCase
from issues.detectors import run_all
from issues.models import DataIssue
from patients.forms import PatientForm

from .locations import clean_name, merge_location, name_key
from .models import District, Region, Street, Ward

CSV = """region,regioncode,district,districtcode,ward,wardcode,street,places
DODOMA,41,KONDOA,411,BEREKO,41101,MNADANI,A
DODOMA,41,KONDOA,411,BEREKO,41101,MNADANI,B
DODOMA,41,KONDOA,411,HONDOMAIRO,,KISIWANI,
DODOMA,41,KONDOA,411,KONDOA MJINI,41105,,
DODOMA,41,"DODOMA
CBD",412,UHURU,41201,KIPANDE,
"""


class NameTests(BaseTestCase):
    def test_clean_and_key(self):
        self.assertEqual(clean_name("MANG'OLA"), "Mang'ola")
        self.assertEqual(clean_name('DODOMA\nCBD'), 'Dodoma CBD')
        self.assertEqual(clean_name('DAR-ES-SALAAM'), 'Dar es Salaam')
        self.assertEqual(clean_name('KONDOA MJINI'), 'Kondoa mjini')
        self.assertEqual(name_key('Hondo Mairo'), name_key('HONDOMAIRO'))


class ImportTests(BaseTestCase):
    def setUp(self):
        self.region, _ = Region.objects.get_or_create(name='Dodoma', defaults={'zone': self.ilala.district.region.zone})
        self.kondoa = District.objects.create(region=self.region, name='Kondoa')
        self.city = District.objects.create(region=self.region, name='Dodoma City')
        self.bereke = Ward.objects.create(district=self.kondoa, name='Bereke')        # EDCS spelling
        self.hondo = Ward.objects.create(district=self.kondoa, name='Hondo Mairo')    # same, other spacing
        self.patient = self.make_patient(residence_district=self.kondoa, residence_ward=self.bereke)
        self.dir = tempfile.mkdtemp()
        Path(self.dir, 'dodoma.csv').write_text(CSV, encoding='utf-8')

    def run_import(self):
        call_command('import_tz_locations', '--path', self.dir, stdout=open('/dev/null', 'w'))

    def test_import_merges_exact_and_reports_near_misses(self):
        self.run_import()
        self.hondo.refresh_from_db()
        self.assertEqual(self.hondo.name, 'Hondomairo')                         # standardised, same record
        self.assertTrue(Street.objects.filter(ward=self.hondo, name='Kisiwani').exists())
        self.assertEqual(Street.objects.filter(name='Mnadani').count(), 1)      # repeated "places" rows → one street
        self.assertEqual(Ward.objects.get(name='Bereko').code, '41101')
        issues = {i.title for i in DataIssue.objects.filter(code='location_not_in_national_list')}
        self.assertIn('Ward "Bereke" (Kondoa district) is not in the national list', issues)
        self.assertIn('District "Dodoma City" (Dodoma region) is not in the national list', issues)
        self.assertIn('Bereko', DataIssue.objects.get(title__startswith='Ward "Bereke"').description)
        before = Ward.objects.count(), Street.objects.count(), DataIssue.objects.count()
        self.run_import()
        self.assertEqual(before, (Ward.objects.count(), Street.objects.count(), DataIssue.objects.count()))

    def test_merge_from_data_issue_moves_patients(self):
        self.run_import()
        issue = DataIssue.objects.get(title__startswith='Ward "Bereke"')
        bereko = Ward.objects.get(name='Bereko')
        manager = self.make_user('dm_l', 'Data Manager', level='national')
        self.client.force_login(manager)
        page = self.client.get(issue.get_absolute_url())
        self.assertContains(page, 'Bereko (suggested)')
        self.client.post(reverse('issues:merge_location', args=[issue.pk]), {'target': bereko.pk})
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.residence_ward, bereko)
        self.assertFalse(Ward.objects.filter(pk=self.bereke.pk).exists())
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'resolved')

    def test_merge_district_merges_child_wards(self):
        other = District.objects.create(region=self.region, name='Kondoa DC')
        Ward.objects.create(district=other, name='BEREKE')
        unique = Ward.objects.create(district=other, name='Pahi')
        merge_location(other, self.kondoa)
        self.assertEqual(self.kondoa.wards.filter(name__iexact='bereke').count(), 1)
        unique.refresh_from_db()
        self.assertEqual(unique.district, self.kondoa)


class PickerTests(BaseTestCase):
    def setUp(self):
        self.region = self.ilala.district.region
        self.district = self.ilala.district
        self.ward = Ward.objects.create(district=self.district, name='Upanga')
        self.street = Street.objects.create(ward=self.ward, name='Mtaa wa Kwanza')
        other_district = District.objects.create(region=self.region, name='Other')
        self.other_ward = Ward.objects.create(district=other_district, name='Elsewhere')

    def test_endpoints_return_children_and_require_login(self):
        self.assertEqual(self.client.get(reverse('facilities:location_options', args=['wards'])).status_code, 302)
        self.client.force_login(self.clinician_ilala)
        data = self.client.get(reverse('facilities:location_options', args=['wards']), {'parent': self.district.pk}).json()
        self.assertEqual([w['text'] for w in data['results']], ['Upanga'])
        data = self.client.get(reverse('facilities:location_options', args=['streets']),
                               {'parent': self.ward.pk, 'q': 'kwanza'}).json()
        self.assertEqual(data['results'][0]['id'], self.street.pk)

    def base(self, **extra):
        data = {'first_name': 'A', 'last_name': 'B', 'sex': 'female', 'date_of_birth': '1990-01-01',
                'registration_facility': self.ilala.pk, 'registration_date': '2026-10-01', 'sms_consent': 'unknown'}
        data.update(extra)
        return data

    def test_form_rejects_ward_from_another_district(self):
        form = PatientForm(self.base(residence_region=self.region.pk, residence_district=self.district.pk,
                                     residence_ward=self.other_ward.pk), user=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('residence_ward', form.errors)

    def test_form_accepts_consistent_address_and_prefills_from_facility(self):
        form = PatientForm(self.base(residence_region=self.region.pk, residence_district=self.district.pk,
                                     residence_ward=self.ward.pk, residence_street=self.street.pk), user=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        clinician_form = PatientForm(user=self.clinician_ilala)
        self.assertEqual(str(clinician_form.initial.get('residence_district')), str(self.district.pk))
        self.assertIn('data-location="street"', str(clinician_form['residence_street']))

    def test_address_detectors(self):
        wrong = self.make_patient(residence_district=self.district, residence_ward=self.other_ward)
        no_ward = self.make_patient(first_name='Z')
        ProgrammeEnrollment.objects.create(patient=no_ward, facility=self.ilala,
                                           programme=ClinicalProgramme.objects.get(code='dm-care'))
        run_all()
        self.assertTrue(DataIssue.objects.filter(code='address_mismatch', patient=wrong).exists())
        self.assertTrue(DataIssue.objects.filter(code='address_incomplete', patient=no_ward).exists())

    def test_register_page_has_pickers(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse('patients:create'))
        self.assertContains(page, 'tom-select.complete.min.js')
        self.assertContains(page, 'Street / village')

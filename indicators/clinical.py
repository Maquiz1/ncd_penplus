"""PEN-Plus clinical indicators (NOP Tanzania 2024, Table 11) and sociodemographic indicators.

Each indicator gives a denominator (who is eligible) and numerator (who meets it) as patient sets, so numbers can be
opened as patient lists. Data comes from structured records (labs, investigations, medicines, admissions) and from
form variables with the EDCS names (e.g. "dka_number"), so new CRFs using the same variable names count too.
Indicators of inactive diseases are not shown.
"""
import datetime

from core.utils import add_months

from .engine import age_on, number

BETA_BLOCKERS = ('beta blocker', 'carvedilol', 'atenolol', 'bisoprolol', 'metoprolol', 'propranolol')
ACE_I = ('ace-i', 'ace inhibitor', 'enalapril', 'captopril', 'lisinopril', 'ramipril')
PENICILLIN = ('penicillin', 'pcn', 'pen v', 'benzathine', 'phenoxymethyl')
WARFARIN = ('warfarin', 'anticoagulation')


def positive(value):
    n = number(value)
    return n is not None and n > 0


class Indicator:
    def __init__(self, key, disease, label, numerator, denominator, note='', kind='pct', unit=''):
        self.key, self.disease, self.label, self.note, self.kind, self.unit = key, disease, label, note, kind, unit
        self._numerator, self._denominator = numerator, denominator

    def evaluate(self, cohort, date):
        """pct: (numerator set, denominator set); avg: (average, patients with a value)."""
        base = self._denominator(cohort, date)
        if self.kind == 'avg':
            values = {}
            for pid in base:
                value = self._numerator(cohort, pid, date)
                if value is not None:
                    values[pid] = value
            avg = round(sum(values.values()) / len(values), 1) if values else None
            return {'value': avg, 'n': len(values), 'denominator': set(values), 'numerator': set(values)}
        numerator = {pid for pid in base if self._numerator(cohort, pid, date)}
        return {'numerator': numerator, 'denominator': base,
                'pct': round(100 * len(numerator) / len(base), 1) if base else None}


def active_with(*codes):
    return lambda c, d: c.with_condition(c.active(d), set(codes), d)


def active_disease(code):
    return lambda c, d: c.with_disease(c.active(d), code, d)


def year_ago(d):
    return add_months(d, -12)


def latest_number(cohort, pid, keys, date, since=None):
    values = [number(cohort.latest_crf(pid, k, date, since)) for k in keys]
    values = [v for v in values if v is not None]
    return float(values[0]) if values else None


INDICATORS = [
    # ---------------------------------------------------------------- Diabetes
    Indicator('dm-basal-bolus', 'diabetes', '% people with T1D on basal bolus insulin (not pre-mixed insulin)',
              lambda c, p, d: (c.latest_crf(p, 'basal_insulin', d) in ('1', '2') and c.latest_crf(p, 'prandial_insulin', d) in ('1', '2'))
              or (c.on_any(p, d, ('lente', 'nph', 'basal', 'glargine')) and c.on_any(p, d, ('soluble', 'regular', 'actrapid', 'aspart', 'lispro'))
                  and not c.on_any(p, d, ('mixtard', 'premix', '30/70'))),
              active_with('t1d'), note='Basal and prandial insulin at the last visit.'),
    Indicator('dm-glucometer', 'diabetes', '% people with T1D with home glucometer',
              lambda c, p, d: positive(c.latest_crf(p, 'bg_measurement', d)) or c.on_any(p, d, ('glucometer', 'accucheck')),
              active_with('t1d'), note='Home blood glucose measurements recorded at the last visit (proxy).'),
    Indicator('dm-a1c-6m', 'diabetes', '% people with T1D with an A1C checked within the last 6 months',
              lambda c, p, d: bool(c.lab(p, 'hba1c', d, since=add_months(d, -6))), active_with('t1d')),
    Indicator('dm-a1c-below-8', 'diabetes', 'Proportion of patients with T1D with HbA1C < 8 on last measure',
              lambda c, p, d: (lambda r: bool(r) and r[-1][3] is not None and r[-1][3] < 8)(c.lab(p, 'hba1c', d)),
              lambda c, d: {p for p in active_with('t1d')(c, d) if c.lab(p, 'hba1c', d)},
              note='Denominator: T1D patients with at least one HbA1c result.'),
    Indicator('dm-dka-12m', 'diabetes', 'Proportion of patients with T1D who have had DKA in the past 12 months',
              lambda c, p, d: c.any_crf(p, 'dka_number', d, since=year_ago(d), test=positive), active_with('t1d')),
    # ---------------------------------------------------------------- Cardiac
    Indicator('cardiac-echo-12m', 'cardiac', 'Proportion of patients who have had an echo in the past year',
              lambda c, p, d: bool(c.investigations(p, 'echo', d, since=year_ago(d))), active_disease('cardiac')),
    Indicator('cardiac-rhd-prophylaxis', 'cardiac', 'Proportion of patients with RHD who are on secondary prophylaxis',
              lambda c, p, d: c.on_any(p, d, PENICILLIN), active_with('rhd'), note='Penicillin at the last visit.'),
    Indicator('cardiac-cm-bb-acei', 'cardiac', '% of patients with cardiomyopathy on beta blocker and ACE-I',
              lambda c, p, d: c.on_any(p, d, BETA_BLOCKERS) and c.on_any(p, d, ACE_I),
              active_with('cardiomyopathy')),
    Indicator('cardiac-warfarin-inr', 'cardiac', '% of patients on warfarin with an INR checked in the last 3 months',
              lambda c, p, d: bool(c.lab(p, 'inr', d, since=add_months(d, -3))),
              lambda c, d: {p for p in c.active(d) if c.on_any(p, d, WARFARIN)}),
    Indicator('cardiac-nyha-1-2', 'cardiac', 'Proportion of patients with none or mild limitation in activity (NYHA I and II) at last visit',
              lambda c, p, d: (c.latest_crf(p, 'nyha', d) or '').upper() in ('I', 'II', '1', '2'),
              lambda c, d: {p for p in active_disease('cardiac')(c, d) if c.latest_crf(p, 'nyha', d)},
              note='Needs NYHA on the cardiac assessment form (not recorded in the EDCS).'),
    Indicator('cardiac-nyha-3-4', 'cardiac', 'Proportion of patients with marked limitation in activity (NYHA III and IV) at last visit',
              lambda c, p, d: (c.latest_crf(p, 'nyha', d) or '').upper() in ('III', 'IV', '3', '4'),
              lambda c, d: {p for p in active_disease('cardiac')(c, d) if c.latest_crf(p, 'nyha', d)},
              note='Needs NYHA on the cardiac assessment form (not recorded in the EDCS).'),
    Indicator('cardiac-surgical-referral', 'cardiac', '% of patients with suspected congenital or RHD referred for surgical evaluation',
              lambda c, p, d: any(c.any_crf(p, k, d) for k in ('cardiology', 'awaiting_surgery', 'cardiac_surgery')),
              active_with('rhd', 'chd'), note='Cardiology referral, awaiting surgery or cardiac surgery recorded.'),
    # ---------------------------------------------------------------- Sickle cell
    Indicator('scd-hydroxyurea', 'scd', 'Proportion of patients with SCD on hydroxyurea at last visit',
              lambda c, p, d: c.on_any(p, d, ('hydroxyurea',)), active_disease('scd')),
    Indicator('scd-under5-prophylaxis', 'scd', 'Proportion of patients with SCD less than 5 years old who are on antibiotic prophylaxis',
              lambda c, p, d: c.on_any(p, d, PENICILLIN),
              lambda c, d: {p for p in active_disease('scd')(c, d) if age_on(c.patients[p].date_of_birth, d) < 5}),
    Indicator('scd-folic-acid', 'scd', 'Proportion of patients with SCD who are on folic acid',
              lambda c, p, d: c.on_any(p, d, ('folic',)), active_disease('scd')),
    Indicator('scd-transfusion-12m', 'scd', '% of patients with SCD needing transfusion within the last 12 months',
              lambda c, p, d: any(c.any_crf(p, k, d, since=year_ago(d), test=positive)
                                  for k in ('lab_transfusion_sickle', 'transfusion_12months', 'transfusion'))
              or c.any_crf(p, 'transfusion_needed', d, since=year_ago(d)), active_disease('scd')),
    Indicator('scd-hospitalized-12m', 'scd', 'Proportion of patients with SCD who had a hospitalization within the last 12 months',
              lambda c, p, d: c.admitted(p, year_ago(d), d) or c.any_crf(p, 'hospitalizations', d, since=year_ago(d))
              or c.any_crf(p, 'hospitalization_ncd', d, since=year_ago(d)), active_disease('scd')),
    # ---------------------------------------------------------------- Other diseases (shown only when the disease is active)
    *[Indicator(f'{disease}-{i}', disease, label, lambda c, p, d: False, lambda c, d: set(),
                note='No data collection form yet: add one in the form builder when this disease is activated.')
      for disease, labels in {
          'respiratory': ['% of patients with asthma who have a rescue beta-agonist inhaler', '% of patients with asthma on oral salbutamol',
                          '% of eligible patients on inhaled corticosteroid', '% of patients with nighttime awakening in last 4 weeks at last visit',
                          '% of patients with asthma who at last visit were well controlled (GINA)',
                          '% of patients with asthma who at last visit were partly controlled (GINA)'],
          'liver': ['% of patients with chronic liver disease with fibrosis evaluation by non-invasive testing (APRI)',
                    '% of patients tested for hepatitis B virus', '% of patients with cirrhosis on prophylaxis for esophageal varices'],
          'kidney': ['% of eligible patients on ACE-I', '% of patients with CKD with kidney function and electrolytes checked in last 6 months',
                     '% patients with normal BP at last visit'],
          'epilepsy': ['% of patients with epilepsy on anti-epilepsy medications', '% of patients with <2 seizures in last 6 months',
                       '% of patients reporting 0 seizures in the last month'],
      }.items() for i, label in enumerate(labels, start=1)],
]

SOCIODEMOGRAPHIC = [
    Indicator('socio-school-limited', 'socio', 'Proportion of patients for whom NCD has limited school attendance ever',
              lambda c, p, d: c.any_crf(p, 'school_attendance', d),
              lambda c, d: {p for p in c.active(d) if c.latest_crf(p, 'school_attendance', d) in ('1', '2')},
              note='Denominator: patients asked (answer Yes or No).'),
    Indicator('socio-missed-school', 'socio', 'Average days of missed school in the last month',
              lambda c, p, d: latest_number(c, p, ('missed_school', 'school_days'), d), lambda c, d: c.active(d),
              kind='avg', unit='days'),
    Indicator('socio-food-insecurity', 'socio', 'Patients facing food insecurity',
              lambda c, p, d: any(c.latest_crf(p, k, d) in ('2', '3') for k in ('no_food', 'sleep_hungry', 'day_hungry')),
              lambda c, d: {p for p in c.active(d) if c.latest_crf(p, 'no_food', d)},
              note='Sometimes or often without food, sleeping or a whole day hungry (latest socio-economic form).'),
    Indicator('socio-distance-km', 'socio', 'Average distance to clinic', lambda c, p, d: latest_number(c, p, ('distance_km',), d),
              lambda c, d: c.active(d), kind='avg', unit='km'),
    Indicator('socio-distance-min', 'socio', 'Average time to clinic', lambda c, p, d: latest_number(c, p, ('distance_minutes',), d),
              lambda c, d: c.active(d), kind='avg', unit='minutes'),
    Indicator('socio-transport-cost', 'socio', 'Average cost of transportation (round trip)',
              lambda c, p, d: latest_number(c, p, ('transportation_cost',), d), lambda c, d: c.active(d), kind='avg', unit='TSHS'),
    Indicator('socio-social-support', 'socio', 'Proportion of patients who are provided with social support in a quarter',
              lambda c, p, d: c.any_crf(p, 'social_support', d, since=add_months(d, -3)),
              lambda c, d: {p for p in c.active(d) if c.latest_crf(p, 'social_support', d, since=add_months(d, -3))},
              note='Denominator: active patients with a treatment plan in the last 3 months.'),
]


def visible(indicators, active_disease_codes):
    return [i for i in indicators if i.disease == 'socio' or i.disease in active_disease_codes]


def find(key):
    return next((i for i in INDICATORS + SOCIODEMOGRAPHIC if i.key == key), None)

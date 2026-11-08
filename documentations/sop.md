# SOP: using the NCD PEN-Plus system

**Purpose.** How a subject moves through the PEN-Plus clinic in this system, from the first contact to regular follow-up, and who records what at each step.

**Who uses it.** Everyone who records subject data at a PEN-Plus clinic: registration officers, nurses, clinicians, laboratory, imaging and pharmacy staff, and data clerks.

**Words used.** A **subject** is a person in the PEN-Plus study; the PEN-Plus clinic is the study, so everyone enrolled here is a study subject. Routine care outside PEN-Plus is recorded in GoTHOMIS. An **appointment** is a booking for a future date. A **visit** (an *encounter* in some menus) is a contact that happened. Everything recorded for a subject belongs to a visit.

## 1. The flow at a glance

```
New subject   Register → Screening → Investigations → Diagnosis → Prescribe → Enroll → Follow-up plan
              (all one BASELINE visit; it closes at Enroll)

Enrolled      Appointment day → Attend → FOLLOW-UP visit: Forms → Investigations → Prescribe → (Admission) → Close
              Between appointments → UNSCHEDULED visit: Forms → (Investigations) → (Prescribe) → (Admission) → Close
```

| Step | Who | Where in the system |
|---|---|---|
| Register | Registration officer, nurse, clinician, data clerk | Subjects → Subject register → **Register subject** |
| Screening | Nurse, clinician, data clerk | Subject page → **Baseline** → Screening |
| Request investigations | Nurse (laboratory), clinician (laboratory and imaging) | Baseline → Investigations, or inside a visit |
| Collect sample, enter result | Laboratory staff | Laboratory → To collect / Awaiting results |
| ECG, echo, imaging report | Imaging / ECG / echo staff | Radiology & imaging → Requests |
| Review results | Clinician | Laboratory → Doctor review; Radiology & imaging → Reports to review |
| Diagnosis | Clinician | Baseline → Diagnosis |
| Prescribe | Clinician | Baseline → Prescribe, or inside a visit |
| Dispense | Pharmacist / dispenser | Pharmacy → Dispensing |
| Enroll | Clinician | Baseline → Enroll |
| Follow-up plan | Clinician | Baseline → **Follow-up plan** tab |
| Vital signs and forms | Nurse, clinician, data clerk | Inside the visit → Forms |
| Admission and discharge | Clinician | Inside the visit → Admission / Discharge |
| Close the visit | Nurse, clinician, data clerk | Inside the visit → Close visit |

The subject page has three buttons at the top: **Baseline**, **Follow-up** and **Unscheduled**. Each shows its steps; a ticked step is done, a greyed step is not needed, and **Next step** links to whatever is still missing.

## 2. New subject: the baseline visit

Do not book an appointment for a subject seen today. Appointments are only for future dates. Steps 2 to 6 are one **baseline visit**: the system opens it at the first step, links every record to it, and closes it at enrollment.

**Vital signs and baseline forms** (demographics, history, symptoms and examination, socio-economic status …) are filled during the same visit, under Baseline → **Forms** tab, as soon as the subject has consented. Vital signs are the first form.

### Step 1. Register

1. Subjects → Subject register → **Register subject**.
2. Enter the name, sex, date of birth (or age), phone, residence and next of kin.
3. If the system shows **possible duplicates**, open them. Use the existing subject if it is the same person; register a new one only if it is not, and the reason is recorded.
4. Save. The subject gets an NCD number and their page opens.

### Step 2. Screening (once per subject)

1. On the subject page: **Baseline** → **Screening**.
2. Record **consent**. The **Outcome** follows it: "Continues in PEN-Plus" with consent, "Does not proceed at this time" without. Change it only to "Referred elsewhere".
   - **No consent: stop here.** Nothing else is recorded. The subject continues routine care in GoTHOMIS.
3. Record residence. If the subject already has a known NCD (known diabetic, referral, transfer-in), tick **Known NCD** and the disease(s).
4. **Investigations requested?**
   - **Yes:** tick under **Screen for** what the tests will look for (diabetes, cardiac, sickle cell, or several). A known disease is ticked for you. Saving opens the investigations page.
   - **No:** the Investigations step is greyed out and saving goes to **Diagnosis**.
5. The screening form is added once. Afterwards it can only be viewed, edited or deleted (Baseline → **Screening** tab).

### Step 3. Investigations

1. Request the tests on the investigations page: laboratory (HbA1c, glucose, Hb, creatinine, sickling test, Hb electrophoresis …) and ECG, echo or imaging.
2. **Laboratory staff** take the sample (Laboratory → **To collect**) and enter the results (Laboratory → **Awaiting results**). Results done in the hospital laboratory are taken from GoTHOMIS and entered here by hand.
3. **Imaging staff** enter the ECG, echo or imaging report (Radiology & imaging → **Requests**).
4. The **clinician** reviews them (Laboratory → **Doctor review**; Radiology & imaging → **Reports to review**).
5. While results are pending, the step shows **Waiting**. If the subject must come back for a test (for example an echo next week), book an appointment for that day and continue from step 4 then.

### Step 4. Diagnosis (clinician; once per subject)

1. **Baseline** → **Diagnosis**.
2. Tick each condition (and sub-type) found, and choose a **status** for each disease. Nothing is pre-selected:
   - **Confirmed**: the results support it.
   - **Suspected**: not yet supported, for example a known NCD with no result yet.
   - **Ruled out**: the disease was looked for and not found.
3. Save. This also gives the screening its result: confirmed counts as positive, ruled out as negative.
4. **No disease found:** press **Not diagnosed** on each screened disease (on the progress bar under Baseline) instead of recording a diagnosis. When every screened disease is not diagnosed, the baseline visit closes as *screening only*, and the subject continues routine care in GoTHOMIS.
5. Afterwards the diagnosis can only be viewed, edited or deleted (Baseline → **Diagnosis** tab). When a suspected diagnosis is later supported by results, edit it to Confirmed.

### Step 5. Prescribe (clinician; optional)

1. **Baseline** → **Prescribe**, if treatment starts today. Add one or more medicines on the same prescription.
2. The pharmacist dispenses it (Pharmacy → **Dispensing**).

### Step 6. Enroll (clinician)

1. **Baseline** → **Enroll**. The page lists the programme of every **confirmed** disease, ticked together.
2. Save. One save enrolls the subject in the programme(s) and in the PEN-Plus study; the screening consent is the study consent.
3. The baseline visit closes. Enrolling is what puts the subject into the indicators, the HbA1c due list and the lists of disease-assigned staff.

**Enroll the same day?** Yes, if the clinician confirms the diagnosis that day. No, if the subject is suspected and waiting for tests: book the review appointment and enroll then. A suspected diagnosis or a known NCD alone is not enough to enroll.

### Step 7. Follow-up plan (clinician; after enrollment)

1. **Baseline** → **Follow-up plan** tab (it opens only after enrollment).
2. The study is PEN-Plus, already chosen. Choose the **interval** (monthly, every 2, 3 or 6 months, or yearly), the start date (the enrollment date by default) and the **number of future visits** (1 to 12; it starts at 1).
3. Save. The system books the appointments. Subjects who consented to SMS get reminders.
4. A subject has **one** active schedule. A new or re-activated schedule stops the old one and cancels its future appointments. Extra one-off appointments are also booked here (**Book appointment**).

## 3. Follow-up visit (subject has an appointment)

1. Open the subject page → **Follow-up** → **Attend**. (Or Care → Appointments, or the clinic day page.)
   - A subject who comes a few days early or late still uses **Attend** on that appointment, so it counts as attended, not missed.
2. The visit opens, linked to the appointment. Its steps:
   1. **Forms**: vital signs first, then the forms listed for the visit (Required or Optional). Mark a form **N/A** with a reason if it does not apply. Watch for the HbA1c banner when a test is due.
   2. **Investigations**: request any tests; results follow the same path as at baseline.
   3. **Prescribe**: the medicines for this visit.
   4. **Admission**: only if the subject is admitted from this visit (section 5).
   5. **Close visit** when the work is done.
3. The appointment changes to **Attended**. The next appointment already exists from the follow-up plan. If the clinician wants a different date, **reschedule** it (a reason is required); do not book a second one.

**Subject does not come.** Leave the appointment. It becomes **Missed** when attendance for the clinic day is closed, or staff mark it Missed. Follow up by phone or home visit. Missed appointments feed the loss to follow-up (LTFU) count.

**Only one follow-up visit is expected per month.** A second one in the same month raises a data issue (section 7).

## 4. Unscheduled visit (no appointment)

For example: unwell, ran out of medicine, a complication, referred back between visits.

1. Subject page → **Unscheduled** → **Start unscheduled visit**. It is never linked to an appointment.
2. Same steps as a follow-up visit: **Forms** (vital signs first), **Investigations** and **Prescribe** (both optional here), **Admission** if needed, **Close visit**.
3. It does not attend or replace the next appointment. Keep the next appointment, or reschedule it if the clinician wants the subject back on another date.

If the subject comes **on the day of a booked appointment**, it is not unscheduled: use **Attend** (section 3).

A visit left open on an earlier day does not stop a new one; close it once its work is done.

## 5. Admission and discharge

1. Inside the visit: **Admission** → ward, date and reason. The subject's Admission tab and Care → Admissions list it.
2. While admitted, the same step shows **Discharge**: record the discharge date, outcome and summary.
3. A subject who dies in hospital: discharge with outcome **Died**, then **More actions → Record death** so the subject's status changes.

## 6. Outcomes and leaving

All from **More actions** on the subject page, at any time:

- **Transfer out** to another PEN-Plus facility.
- **Outcome** for a programme: LTFU, stopped, and later **Return to care**.
- **Record death**.
- **Withdraw / leave the study**: closes the care enrollment here too, and the subject continues routine care in GoTHOMIS. Closing one disease's programme closes the study only if no other programme is active.

## 7. Checks and corrections

- **Subjects → Stuck in the flow** lists subjects waiting between two steps: screened over 14 days with no decision, positive with no diagnosis, diagnosed but not enrolled, enrolled without a baseline visit, and enrolled with no follow-up plan or upcoming appointment. Work through it every week.
- **Suspected diagnoses**: if confirming results have not come within 60 days, a data issue says the diagnosis is still suspected; when results arrive, it says "results available: confirm the diagnosis".
- **Correcting**: edit the record. **Removing a duplicate entry**: delete it (a reason is asked). Only a superuser can restore a deleted record.
- **Screening for another disease later**: edit the screening form and tick it. **Screening again** after a negative or inconclusive result: Overview → Screening card → **Re-screen** on that disease, with the reason. The earlier record is kept.

### Two follow-up visits in the same month

When a subject has two or more baseline or follow-up visits in one month, the system assumes the extra ones were unscheduled and raises **"2 follow-up visits in <month>"** under Data quality → Data issues.

1. Open the issue; it lists every visit that month.
2. Keep the real monthly visit (the one linked to the appointment).
3. Click **Unscheduled** on each extra visit. It becomes Unscheduled and is unlinked from its appointment; an appointment left with no visit is cancelled ("Visit reclassified as unscheduled during data review").
4. The issue closes by itself when one follow-up visit is left.

If none of the visits has an appointment, click **Monthly visit** on the planned one; the system creates an attended appointment for it. If the visits are right as recorded, close the issue with **No change needed** and a note. Admissions and screening are not counted in this check.

## Reference: appointment and visit

| | Appointment | Visit |
|---|---|---|
| What it is | A plan: "come back on 15 Nov" | A fact: the subject was seen on this date |
| Created by | The follow-up plan, a clinic day, or staff booking it | Staff, on the day the subject is seen |
| Holds | Date, time, visit type, facility | Vital signs, forms, investigations, medicines, admission, notes |
| Statuses | Scheduled, confirmed, rescheduled, cancelled, attended, missed | Open, in progress, completed, incomplete, cancelled |

- An appointment linked to a visit becomes **Attended** automatically. One with no visit becomes **Missed**.
- An appointment with no visit linked (cancelled or never attended) can be deleted; one with a visit is cancelled or marked missed instead.
- Appointments count expected and missed visits, loss to follow-up and SMS reminders. Visits hold the clinical data.

## Reports

- **Follow-up & retention** shows, per month: visits due, attended, missed, attendance %, **unscheduled visits**, LTFU, returns and deaths.
- Attendance counts appointments only. Unscheduled visits are shown separately and do not raise or lower attendance.
- Visits migrated from EDCS as UV (unscheduled visit) are imported as **Unscheduled**.

## Roles that only read results

PIs and co-investigators, facility and geographic coordinators, statisticians and M&E officers get their own home page instead of the clinic dashboard. It shows reports, indicators, monitoring and subject summaries, view only, with subject IDs and no names. They don't see the subject register, forms or data entry. Statisticians also get data exports and the data dictionary.

## Diagnoses imported from EDCS

The old EDCS had a diagnosis form for each disease (diabetes, cardiac, sickle cell) and a "Patient category" form. They were imported into the one Diagnosis record: one diagnosis per diabetes or sickle cell form, one per cardiac condition ticked, and the patient category only where no disease form existed ("category recorded in EDCS"). The old forms are also kept as form records.

EDCS saved every diagnosis as final. Here an imported diagnosis stays **Confirmed** only when a supporting test was imported: echo for cardiac, HbA1c or fasting / random glucose for diabetes, a confirmatory test or genotype (Hb electrophoresis) for sickle cell. Without one it is **Suspected**. The test being done is what counts, not its value, because a treated subject can have normal results. Subjects with a suspected diagnosis stay enrolled; for those still active in care, a data issue asks the doctor to confirm the diagnosis.


## Forms from EDCS, and what replaced them

Each EDCS form either carries on as a form here, with the same variable names and answer codes, or its data now lives in a structured record. Every old entry is kept: forms that are no longer filled stay readable with their EDCS entries.

| EDCS form | Here |
|---|---|
| Demographic, Socioeconomic status, Patient history & family history | Forms at baseline, as before |
| History, symptom & exam | **Symptoms & examination** at every visit; its lab values (fasting / random glucose, HbA1c, Hb, WBC, Plt) are now requested under Investigations |
| Risk, Diagnosis complications & comorbidities, Hospitalization | Forms at every visit (Hospitalization at follow-up and unscheduled visits) |
| Treatment plan | **Treatment plan** at every visit, without the medicine rows and insulin doses: those are prescribed under Prescribe |
| Lab details | Lab values go to Investigations; its assessments (coping, family planning, eye and foot exams, DKA episodes, cardiac surgery, transfusions, transcranial Doppler) are the **Clinical assessments** form at every visit |
| Main diagnosis – diabetes | The type of diabetes is chosen in the Diagnosis step; presentation (DKA, ketosis…) and hypertension are the **Diabetes presentation** form at baseline |
| Main diagnosis – cardiac, sickle cell; Patient category | The Diagnosis step (conditions and sub-types, with status); the forms are no longer filled |
| Results at enrollment | ECG and echo under Investigations; sickle cell tests as lab results |
| Vitals | Vital signs, the first form of every visit |
| Hospitalization details | Admission, from inside a visit |
| Sickle cell family status | **SCD family history** tab (shown for subjects with sickle cell) |
| Next visit summary | Appointments and the follow-up plan; outcomes (LTFU, transfer, death) under More actions |
| Screening | The Screening form at baseline |

### Exporting with the old EDCS names

Exports use the new variable names by default. Choose **Variable names: Old EDCS names** on the Exports page, or **Codes, old EDCS names** in a form's Data menu on the Forms page, to get the EDCS column names instead (for example `dystolic`, `pr`, `clinic_date`, `vid`, `site_id`, `study_id`), so analysis scripts written for the EDCS data keep working. In that mode the subject's NCD number is `ncd_number`, the old EDCS client number is `patient_id` (`id` in the registration table), and lab results take their EDCS `lab_details` names (for example `cre`). A variable EDCS did not have keeps its new name. The data dictionary has an **EDCS table.column** column showing where each variable came from.

### Data dictionary versions

Exports → Codebook → **Data dictionary** downloads the versioned Excel data dictionary: study forms, datasets, every variable (label, type, unit, codes, required, in de-identified export, old EDCS name), lab tests, the open decisions (D1–D10, L1–L8) and the change log. Earlier versions, as they were issued, are listed under it. The data team raises the version when forms or variables change.

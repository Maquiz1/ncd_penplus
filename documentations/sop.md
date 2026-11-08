# SOP: using the NCD PEN-Plus system

## 1. Appointment and visit: the difference

An **appointment** is a booking for a future date. A **visit** (called an **encounter** in the system) is a contact that actually happened.

| | Appointment | Visit (encounter) |
|---|---|---|
| What it is | A plan: "come back on 15 Nov" | A fact: the subject was seen on this date |
| When it's created | Before the day, by a follow-up plan, a clinic day or staff booking it | On the day the subject is seen |
| What it holds | Date, time, visit type, facility | Vitals, complaints, notes, CRFs, lab orders, medicines, plan |
| Statuses | Scheduled, confirmed, rescheduled, cancelled, attended, missed | Open, in progress, completed, incomplete, cancelled |
| Needs the other? | No | No: a visit without an appointment is an unscheduled visit |

Visit types used for care:

- **Baseline**: the first visit, at enrolment.
- **Follow-up**: the planned monthly visit. Only **one** is expected per month.
- **Unscheduled**: any extra visit with no appointment. It is never linked to an appointment, and an appointment can't be booked as Unscheduled.

- When a visit is linked to an appointment, the appointment changes to **Attended** automatically.
- An appointment with no visit changes to **Missed**. This happens when attendance is closed for the clinic day, or when staff mark it Missed themselves.
- Appointments are used to count expected and missed visits and loss to follow-up, and to send SMS reminders. Visits hold the clinical data. Attendance compares the two.

## 2. New subject registration

You don't need to create an appointment first. Appointments are only for future visits; anything that happens today is recorded directly, even with no booking.

Order for a new subject seen today:

1. Register the patient.
2. Screening: one form with consent, residence and known NCD. Its **Outcome** is set from consent: "Continues in PEN-Plus" when the person consents, "Does not proceed at this time" when they don't. Change it only to "Referred elsewhere". For a known NCD (a known diabetic, a referral or a transfer-in), tick which disease or diseases. When **Investigations requested?** is Yes, tick under **Screen for** what the investigations will look for (diabetes, cardiac, sickle cell, or more than one). This is asked even for a known NCD, and a known disease is ticked there for you. No result is entered here. Saving opens the investigations page. When the answer is No, the Investigations step is greyed out and saving goes straight to **Diagnosis**.
3. Request the investigations under **Investigations** (HbA1c, echo, ECG and so on) and wait for the results.
4. Diagnosis: from the results, the doctor ticks each condition diagnosed and, for each disease, chooses a status. Nothing is chosen in advance: **Confirmed** when the results support it, **Suspected** when they don't yet (for example a known NCD with no result), or **Ruled out**. **Not diagnosed** on a screened disease does the same as Ruled out. This gives the screening its result: confirmed counts as positive, ruled out as negative. A subject with no investigations requested goes straight to the doctor.
5. Prescribe, if treatment starts now (optional). It comes after the doctor confirms and before enrolling, so the first prescription belongs to the baseline visit. A sick subject can still be prescribed earlier from the Medications tab.
6. Enroll the subject. The page lists the programme of every disease the doctor confirmed, ticked together. A suspected diagnosis or a known NCD alone isn't enough. Everyone here is in research, so one save also enrolls the subject in the PEN-Plus study; the screening consent is the research consent, and it must be Yes. Patients who are only in care are recorded in GoTHOMIS.
7. Create a follow-up plan. This generates the subject's future appointments. Enrolling does not do this by itself.

Steps 2 to 6 are one **baseline visit**. The system opens it at the first of these steps, links each record to it, and closes it at enrollment. If the doctor finds no disease, it closes as screening only and doesn't count as baseline. A community or school screening doesn't open it. Vitals and baseline forms are in that same visit.

The subject page has three buttons, each showing its steps with direct links: **Baseline**, **Follow-up** (attend the next appointment) and **Unscheduled** (no appointment; it doesn't change the next appointment). **Follow-up** shows its visits and appointments: a table of follow-up visits (date, appointment, forms missing, status, **View**), **Attend** (or **Start follow-up visit**), **Book appointment**, the follow-up schedule and the appointments list. **Unscheduled** shows a table of unscheduled visits and **Start unscheduled visit**. A visit left open on an earlier day doesn't stop a new one; close it once its work is done. **View** opens the visit, where the work is done and belongs to that visit: Forms, Investigations, Prescribe, Admission (or Discharge while admitted) and Close visit. In an unscheduled visit, investigations and prescribing are optional. The visit page also lists the investigations (with results) and medicines of that visit. Under Baseline, the steps are Screening, Investigations, Diagnosis, Prescribe and Enroll, and the tabs below are Overview, Visits (baseline visits only), Investigations, Medications, Admission, Family history, Forms and Follow-up plan (schedule and appointments). These tabs show only under Baseline. The work is done inside the visit page, so everything added, edited or deleted there belongs to that visit. Its steps are: forms (vital signs are the first form of every visit, baseline included), investigations, prescribe, close the visit. Below them are five tabs: **Overview**, **Visits & appointments**, **Investigations** (laboratory and radiology results), **Medications** and **Forms**. Screening, diagnoses and programmes, the PEN-Plus study record and facility history are done once, so they are cards on Overview that show only under Baseline. Outcomes are under **More actions** at any time: transfer out, admit, record death, a programme outcome (LTFU, stopped) or return to care, and withdrawing from the study.

The subject page shows these steps as a progress bar, one row per disease, with a **Next step** button for whatever is still missing. Screening shows as skipped for a subject who arrived diagnosed. "Not diagnosed" ends the path for that disease. The screening form is filled once per subject, and each disease is screened once. To screen for another disease later, edit the screening form and tick it. To correct a record, edit it; to remove one entered twice, delete it. To screen again after a negative or inconclusive result, press **Re-screen** on that disease in the Screening card on Overview and give the reason. This keeps the earlier record, shows the new one as "Re-screening", and starts again from investigations.

**Subjects → Stuck in the flow** lists the subjects waiting between two steps: screened more than 14 days ago with no decision from the doctor, positive but no diagnosis, diagnosed but never enrolled, enrolled without a baseline visit, and enrolled with no follow-up plan or upcoming appointment.

Pre-screening and disease screening do not need an encounter. In the screening form, the encounter field can stay at "Not linked to a visit".

### Should a same-day subject be enrolled the same day?

- **Yes**, if the doctor confirms the diagnosis that day. A suspected diagnosis or a known NCD alone isn't enough. If confirming results haven't come back within 60 days, the system flags a data issue saying the diagnosis is still suspected. When results do arrive, it flags "results available: confirm the diagnosis".
- **No**, if they were screened positive but only sent for tests and a review, for example echo next week. Book an appointment for that review and enroll them then.

Enrolling is what puts a subject into the indicators and the HbA1c due list, and it is how disease-assigned staff see them. A registered subject who is never enrolled doesn't count in any of these.

## 3. Subject comes for a follow-up (has an appointment)

1. Find the appointment, either in **Appointments** in the menu, on the clinic day page, or on the subject's page under **Visits**.
2. Click **Attend**. This opens a new encounter already linked to the appointment, with the visit type, facility and enrolment filled in.
3. Record the visit from inside the encounter: vitals, symptoms, CRFs, lab orders (check the HbA1c banner), results, medicines and the plan.
4. Save. The appointment changes to **Attended**.
5. The next appointment already exists if the subject has a follow-up plan. If the clinician wants a different date, reschedule the next appointment (a reason is required); don't create a second one.

If the subject comes a few days early or late for a booked appointment, still use **Attend** on that appointment, so it counts as attended and not as missed.

If the subject doesn't come: leave the appointment. It becomes **Missed** when attendance for the clinic day is closed, or staff can mark it Missed. Then follow up by phone or a home visit if needed.

## 4. Subject comes for an unscheduled visit (no appointment)

For example: feeling unwell, ran out of medicine, a complication, or referred back between visits.

1. Open the subject's page, go to **Visits** and click **Unscheduled visit**. The visit type is already set to **Unscheduled**.
2. Leave the **appointment field empty**. The system refuses an Unscheduled visit linked to an appointment.
3. Record the visit the same way as a follow-up: vitals, complaint, notes, tests, medicines, plan.
4. Save.
5. Check the subject's next appointment. Keep it if it's still right; reschedule it only if the clinician changes the date.

An unscheduled visit doesn't attend or replace the next booked appointment. If that appointment is far off and the clinician wants the subject back sooner, book an extra appointment with **Schedule appointment**.

If the subject comes on the day of a booked appointment, it is not unscheduled: use **Attend** on the appointment (section 3).

## 5. Two follow-up visits in the same month (data quality)

Only one Baseline or Follow-up visit is expected per subject per month. When a subject has two or more in one month, the system assumes the extra ones were unscheduled and raises a data issue, **"2 follow-up visits in <month>"**, under **Data quality → Visits & schedule**.

To resolve it:

1. Open the issue. It lists every visit that month, with its type and appointment.
2. Keep the real monthly visit as it is (the one linked to the appointment, if any).
3. Click **Unscheduled** on each extra visit. The visit type changes to Unscheduled and it is unlinked from its appointment. If that appointment then has no visit, it is cancelled with the reason "Visit reclassified as unscheduled during data review".
4. When only one follow-up visit is left in the month, the issue closes by itself and records who resolved it.

If none of the visits has an appointment, click **Monthly visit** on the one that was the planned visit; the system creates an attended appointment for it. If the visits are correct as recorded (for example, the clinician really booked two visits that month), close the issue with **No change needed** and a short note.

Editing the visit itself (type Unscheduled, appointment cleared) also closes the issue at the next data check.

Admissions, screening and other visit types are not counted in this check.

## 6. Reports

- **Follow-up & retention** shows, per month: visits due, attended, missed, attendance %, **unscheduled visits**, LTFU, returns and deaths.
- Attendance counts appointments only. Unscheduled visits are shown separately and do not raise or lower attendance.
- Visits migrated from EDCS as UV (unscheduled visit) are imported as **Unscheduled**.

## Roles that only read results

PIs and co-investigators, facility and geographic coordinators, statisticians and M&E officers get their own home page instead of the clinic dashboard. It shows reports, indicators, monitoring and subject summaries, view only, with subject IDs and no names. They don't see the subject register, forms or data entry. Statisticians also get data exports and the data dictionary.

## Leaving the study

Withdrawing consent, or closing the study enrollment, also closes the care enrollment here, and the subject continues care in GoTHOMIS. Closing care for one disease closes the study only if the subject has no other active programme.

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

The follow-up schedule and extra one-off appointments are set in one place: Baseline → **Follow-up plan**. An automatic schedule books 1 to 12 future visits; the number starts at 1. A subject follows one schedule at a time: a new or re-activated schedule stops the other and cancels its future visits not yet started. An appointment with no visit linked (cancelled or never attended) can be deleted; one with a visit is cancelled or marked missed instead.

### Exporting with the old EDCS names

Exports use the new variable names by default. Choose **Variable names: Old EDCS names** on the Exports page, or **Codes, old EDCS names** in a form's Data menu on the Forms page, to get the EDCS column names instead (for example `dystolic`, `pr`, `clinic_date`, `vid`, `site_id`, `study_id`), so analysis scripts written for the EDCS data keep working. In that mode the subject's NCD number is `ncd_number`, the old EDCS client number is `patient_id` (`id` in the registration table), and lab results take their EDCS `lab_details` names (for example `cre`). A variable EDCS did not have keeps its new name. The data dictionary has an **EDCS table.column** column showing where each variable came from.

### Data dictionary versions

Exports → Codebook → **Data dictionary** downloads the versioned Excel data dictionary: study forms, datasets, every variable (label, type, unit, codes, required, in de-identified export, old EDCS name), lab tests, the open decisions (D1–D10, L1–L8) and the change log. Earlier versions, as they were issued, are listed under it. The data team raises the version when forms or variables change.

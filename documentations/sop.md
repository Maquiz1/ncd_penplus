# SOP: using the NCD PEN-Plus system

## 1. Appointment and visit: the difference

An **appointment** is a booking for a future date. A **visit** (called an **encounter** in the system) is a contact that actually happened.

| | Appointment | Visit (encounter) |
|---|---|---|
| What it is | A plan: "come back on 15 Nov" | A fact: the patient was seen on this date |
| When it's created | Before the day, by a follow-up plan, a clinic day or staff booking it | On the day the patient is seen |
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

## 2. New patient registration

You don't need to create an appointment first. Appointments are only for future visits; anything that happens today is recorded directly, even with no booking.

Order for a new patient seen today:

1. Register the patient.
2. Pre-screening, then disease screening, both today. Skip these for a patient who arrives already diagnosed, such as a known diabetic, a referral or a transfer-in.
3. Order tests if needed (HbA1c, echo, ECG and so on).
4. Diagnosis. It can be provisional while results are pending.
5. Enroll the patient in the disease programme.
6. Record today's visit as an encounter with visit type **Baseline**. Leave the appointment field empty.
7. Create a follow-up plan. This generates the patient's future appointments. Enrolling does not do this by itself.

Pre-screening and disease screening do not need an encounter. In the screening form, the encounter field can stay at "Not linked to a visit".

### Should a same-day patient be enrolled the same day?

- **Yes**, if the clinician decides that day to take them into care, even with a provisional diagnosis. If confirming results haven't come back within 60 days, the system flags a data issue saying the diagnosis is still provisional. When results do arrive, it flags "results available: confirm the diagnosis".
- **No**, if they were screened positive but only sent for tests and a review, for example echo next week. Book an appointment for that review and enroll them then.

Enrolling is what puts a patient into the indicators and the HbA1c due list, and it is how disease-assigned staff see them. A registered patient who is never enrolled doesn't count in any of these.

## 3. Patient comes for a follow-up (has an appointment)

1. Find the appointment, either in **Appointments** in the menu, on the clinic day page, or on the patient's page under **Visits**.
2. Click **Attend**. This opens a new encounter already linked to the appointment, with the visit type, facility and enrolment filled in.
3. Record the visit from inside the encounter: vitals, symptoms, CRFs, lab orders (check the HbA1c banner), results, medicines and the plan.
4. Save. The appointment changes to **Attended**.
5. The next appointment already exists if the patient has a follow-up plan. If the clinician wants a different date, reschedule the next appointment (a reason is required); don't create a second one.

If the patient comes a few days early or late for a booked appointment, still use **Attend** on that appointment, so it counts as attended and not as missed.

If the patient doesn't come: leave the appointment. It becomes **Missed** when attendance for the clinic day is closed, or staff can mark it Missed. Then follow up by phone or a home visit if needed.

## 4. Patient comes for an unscheduled visit (no appointment)

For example: feeling unwell, ran out of medicine, a complication, or referred back between visits.

1. Open the patient's page, go to **Visits** and click **Unscheduled visit**. The visit type is already set to **Unscheduled**.
2. Leave the **appointment field empty**. The system refuses an Unscheduled visit linked to an appointment.
3. Record the visit the same way as a follow-up: vitals, complaint, notes, tests, medicines, plan.
4. Save.
5. Check the patient's next appointment. Keep it if it's still right; reschedule it only if the clinician changes the date.

An unscheduled visit doesn't attend or replace the next booked appointment. If that appointment is far off and the clinician wants the patient back sooner, book an extra appointment with **Schedule appointment**.

If the patient comes on the day of a booked appointment, it is not unscheduled: use **Attend** on the appointment (section 3).

## 5. Two follow-up visits in the same month (data quality)

Only one Baseline or Follow-up visit is expected per patient per month. When a patient has two or more in one month, the system assumes the extra ones were unscheduled and raises a data issue, **"2 follow-up visits in <month>"**, under **Data quality → Visits & schedule**.

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

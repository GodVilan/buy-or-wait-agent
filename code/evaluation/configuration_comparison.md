# Recurrence-Configuration Comparison

This document records the measured recurrence configurations evaluated in
`code/evaluation/compare_recurrence.py`. Running
`python3 code/evaluation/compare_recurrence.py` prints the eight documented CLI
variants A through H and reproduces the table below. Amount error is the mean
absolute difference between each variant's predicted `amount_safe_to_pay` and
the gold value on the 25 labelled `sample_requests.csv` rows. Event coverage is
the number of financial-event rows belonging to accepted groups.

## Variant rules and measured results

| Variant | Detection and projection rule | Groups accepted | Events covered | Four sample spending-change events detected | Mean absolute error |
|---|---|---:|---:|---:|---:|
| A | Original detector: at least 3 occurrences; median gap 25–35 days (monthly) or 6–8 days (weekly); reject any gap greater than 1.5 times the median. | 2,157 | 10,703 | 3/4 | 565,954.87 |
| B | Fixed GCD/median detector: at least 2 occurrences; accept weekly when the GCD is a multiple of 7 and at most 91, using cadence 7; otherwise accept monthly when the median gap is 25–35, using the rounded median. No maximum-gap rejection. | 4,969 | 19,913 | 3/4 | 849,105.04 |
| C | At least 2 occurrences grouped by the active event key; no cadence acceptance. Project the trailing total amount divided by the first-to-last span as a daily rate. | 6,109 | 23,449 | 4/4 | 1,161,544.22 |
| D | Variant B acceptance, with weekly series projected using the mean per-occurrence amount rather than the most recent amount. | 4,969 | 19,913 | 3/4 | 790,816.78 |
| E | Variant B acceptance plus request-date liveness: the latest occurrence must be no more than 1.5 times the cadence before the request date. Uses the current four-field event key and the E rolled-anchor projection behavior. | 4,969 | 19,913 | 3/4 | 258,041.26 |
| F | Group by `(category, direction)`; require at least 3 occurrences; accept GCD cadences from 5 through 45 days; apply request-date liveness; project the mean of the last three pre-request amounts. | 783 | 14,518 | 1/4 | 1,421,645.30 |
| G | Same as F, but group by `(event_type, category, direction)` so event types remain separate within a category. | 783 | 14,518 | 1/4 | 1,421,645.30 |
| H | Group by `(event_type, category, direction)`; require at least 3 occurrences; accept either a GCD cadence from 5 through 45 days or calendar-monthly gaps with median 28–31 and every gap 27–32; apply request-date liveness and use the mean of the last three pre-request amounts. | 2,546 | 23,652 | 4/4 | 963,764.38 |

The four sample-referenced spending-change events used for the detection check
are `event_476`, `event_989`, `event_1815`, and `event_1816`.

Variant A is now reimplemented standalone in `compare_recurrence.py`, so the
A figures above are reproduced from the current source.

## Selected configuration

Variant **E** was selected because it had the lowest measured mean absolute
error: **258,041.26** (approximately **258,041**), versus Variant A's
**565,954.87** (approximately **565,955**).

Variant H covered more events and detected all four sample events (`4/4`), but
its mean absolute error was worse at **963,764.38**.
Coverage and sample-event detection therefore did not compensate for its worse
labelled amount accuracy.

## Measured cadence distribution

The measured H distribution is split between fixed day multiples and
calendar-monthly series:

```text
day-multiple cadences:
  5 days: 43 groups
  7 days: 263 groups
  10 days: 129 groups
  14 days: 202 groups
  21 days: 144 groups
  30 days: 2 groups

calendar-monthly cadences:
  30-day rounded monthly cadence: 1,110 groups
  31-day rounded monthly cadence: 653 groups
```

Thus the measured fixed day-multiple cadence values in the dataset are only
**5, 7, 10, 14, 21, and 30 days**. The separate calendar-monthly acceptance
accounts for the 30- and 31-day month-length patterns.

## Scheduled-event ablation

This scheduled-event ablation was measured **before the message-delta layer was
wired into the forecast**. A separate measured ablation compared the normal
forecast with an in-memory forecast that excluded scheduled events. Across the
seven scored fields on the 25 labelled rows, **6 of 7 fields improved by exactly
one matching row** when scheduled events were excluded:

- `amount_safe_to_pay`: 2/25 → 3/25
- `affordability_status`: 15/25 → 16/25
- `recommended_payment_method`: 17/25 → 18/25
- `payment_plan`: 13/25 → 14/25
- `earliest_date_for_full_payment`: 10/25 → 11/25
- `spending_changes_needed`: 22/25 → 22/25 (no change)
- `decision_explanation`: 7/25 → 8/25

One row out of 25 is **4 percentage points**, so each one-row movement is
within the measured **4pp noise floor for n=25**. Scheduled events were kept in
the final forecast because the specification's explicit exclusion list does
not name scheduled events; scheduled debits are therefore reserved rather than
silently removed.

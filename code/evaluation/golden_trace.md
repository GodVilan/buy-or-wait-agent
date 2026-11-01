# Golden Trace: `request_21`

This trace follows the current deterministic pipeline for `request_21` using
`code/main.py`'s helpers and the restored original image cache. Monetary values
below are in the user's home currency, USD, unless noted otherwise. The forecast
window is `2026-04-03 <= occurrence < 2026-07-03`; the returned balance mapping
contains the request date through 90 days after it. `user_21` has no message
deltas, so this trace shows the pipeline without the message-delta layer.

## 1. Request and user profile

| Field | Value |
|---|---|
| `request_id` | `request_21` |
| `user_id` | `user_21` |
| `request_type` | `education` |
| `request_date` | `2026-04-03` |
| `requested_amount` | USD 1,574.40 |
| `desired_completion_date` | `2026-04-14` |
| `allows_partial_payment` | `False` |
| `home_currency` | USD |
| `current_available_balance` | USD 3,911.35 |
| `minimum_balance_to_keep` | USD 1,800 |
| `financial_priorities` | `retirement_investment\|emergency_savings` |
| `payment_methods_user_will_consider` | `full_payment` |
| protected categories | `rent\|utilities\|groceries` |
| categories user will reduce | `dining\|streaming\|shopping` |
| categories user will stop | `streaming\|cloud_storage` |
| `max_installment_months` | blank / no installment-month limit configured |

The request text describes a USD 1,574.40 course fee.

## 2. Detected recurring series

These are all eight live Variant E series detected for `user_21` at the request
date. `projected amount` is the signed amount added to the forecast on each
projected occurrence; negative values are debits and positive values are
credits. `occurrence count` is the number of projected occurrences in the
90-day half-open window.

| Description | Category | Direction | Cadence | Projected amount | Occurrence count | Projected dates |
|---|---|---:|---:|---:|---:|---|
| Municipal utilities | utilities | debit | 30 days | -124.08 | 3 | Apr 6, May 6, Jun 5 |
| Streaming subscription | streaming | debit | 30 days | -47.00 | 3 | Apr 9, May 9, Jun 8 |
| Monthly shopping spend | shopping | debit | 30 days | -126.38 | 3 | Apr 12, May 12, Jun 11 |
| Online backup subscription | cloud_storage | debit | 30 days | -11.00 | 3 | Apr 12, May 12, Jun 11 |
| Payroll credit | salary | credit | 30 days | +2,256.00 | 3 | Apr 15, May 15, Jun 14 |
| Fresh food shop | groceries | debit | 30 days | -77.75 | 3 | Apr 17, May 17, Jun 16 |
| Neighbourhood restaurant | dining | debit | 7 days | -69.31 | 13 | Apr 3 through Jun 26, weekly |
| Residential rent payment | rent | debit | 31 days | -718.80 | 2 | May 2, Jun 2 |

## 3. Unmodified forecast and capacity questions

The 91-day balance mapping starts at USD 3,911.35 on `2026-04-03`. Its trough
is:

```text
trough date:  2026-04-12
trough value: USD 3,480.58
minimum:      USD 1,800.00
```

The unmodified forecast has USD 1,680.58 of minimum-protected capacity:

```text
min(balance - minimum) = USD 1,680.58
```

Therefore the output safe amount, which is deliberately computed before
optional spending changes, is:

```text
amount_safe_to_pay = min(USD 1,574.40, USD 1,680.58) = USD 1,574.40
```

The separate earliest-full-payment capacity question tests one full payment at
each forecast date. The full request is already safe on the request date:

```text
earliest_date_for_full_payment = 2026-04-03
```

After a USD 1,574.40 payment on the request date, the trough would be USD
1,906.18, still above the USD 1,800 minimum.

## 4. Payment options and base candidate plans

The supplied options are:

| Option | Method | Schedule summary | Total payable | Eligibility result |
|---|---|---|---:|---|
| `payment_option_57` | `full_payment` | 2026-04-03: USD 1,574.40 | USD 1,574.40 | Not used as an option schedule; the direct full-payment candidate is safe |
| `payment_option_58` | `installments` | 21 × USD 88.47, first 2026-04-06, every 28 days | USD 1,857.87 | Filtered: user only considers `full_payment` |
| `payment_option_59` | `installments` | 6 × USD 283.39, first 2026-04-10, every 30 days | USD 1,700.34 | Filtered: user only considers `full_payment` |
| `payment_option_60` | `installments` | 24 × USD 80.03, first 2026-04-17, every 31 days | USD 1,920.72 | Filtered: user only considers `full_payment` |

`max_installment_months` is blank, so it does not impose an additional
installment-month limit. The installment methods are nevertheless ineligible
because `payment_methods_user_will_consider` is only `full_payment`.

The base candidate-plan enumeration produces exactly one candidate:

```text
method:      full_payment
payments:    2026-04-03:1574.4
total_paid:  USD 1,574.40
changes:     none
```

The other possible plan paths are filtered as follows:

- `installments`: the user does not accept this method;
- `partial_payment`: the user does not accept this method and the request does
  not allow partial payment;
- `wait`: not emitted because `earliest_date_for_full_payment` equals the
  request date rather than being later than it.

Because a base candidate exists, production `build_row` does not invoke the
spending-change search. This is why the final prediction has no spending
changes.

## 5. Spending-change search and event eligibility

Although the production path does not need the search, `_candidate_changes`
finds these eligible changes:

| Change | Series | Why it qualifies | Monthly saving |
|---|---|---|---:|
| `stop:event_1815` | Online backup subscription | debit; `cloud_storage` is not protected; event is `stoppable`; `cloud_storage` is in the user's stop categories | USD 11.00 |
| `reduce_to:event_1816:23.50` | Streaming subscription | debit; `streaming` is not protected; event is `reducible_or_stoppable`; `streaming` is in the user's reduce categories; USD 23.50 is below the USD 47.00 projected amount | USD 23.50 |
| `stop:event_1816` | Streaming subscription | same category/flexibility checks as above, using the stop permission | USD 47.00 |
| `reduce_to:event_1817:49.60` | Monthly shopping spend | debit; `shopping` is reducible; USD 49.60 is below the USD 126.38 projected amount | USD 76.78 |
| `reduce_to:event_1854:41.00` | Neighbourhood restaurant | debit; `dining` is reducible; USD 41.00 is below the USD 69.31 projected amount | USD 121.33 |

In particular, `event_1815` qualifies because it is a non-protected recurring
cloud-storage debit marked `stoppable`, and the user permits stopping
`cloud_storage`. `event_1816` qualifies because it is a non-protected recurring
streaming debit marked `reducible_or_stoppable`, the user permits reducing or
stopping `streaming`, and its configured minimum amount is USD 23.50.

If the search is forced in isolation, every one-change candidate makes the
full-payment plan feasible on `2026-04-03`. The search ranks first by fewest
changes, then by total monthly saving. Its one-change ranking is therefore:

1. `stop:event_1815` — USD 11.00 monthly saving;
2. `reduce_to:event_1816:23.50` — USD 23.50;
3. `stop:event_1816` — USD 47.00;
4. `reduce_to:event_1817:49.60` — USD 76.78;
5. `reduce_to:event_1854:41.00` — USD 121.33.

The isolated search would choose `stop:event_1815` and stop after the first
feasible change-count layer; it would not enumerate two-change combinations
once that layer has a winner. That isolated result is not the production winner
for this request, because the base full-payment candidate is already feasible.
The production combination chosen is therefore **none**.

## 6. Ranking and explanation

The production candidate list contains only the no-change full-payment plan.
Its ranking key is equivalent to:

```text
meets desired completion date: true
has changes:                   false
total paid:                    USD 1,574.40
start date:                    2026-04-03
number of payments:            1
payment option id:             empty
```

It wins without invoking the change-search ranking. `_status_for` maps it to
`affordable_now` because it is a full payment on the request date and the safe
amount equals the requested amount.

The explanation path fires the ordinary `full_payment_template` (not
`payment_with_changes_template`):

```text
Pay USD 1,574.40 today. This leaves at least USD 1,800 available over the next 90 days.
```

## 7. Final prediction and gold comparison

The final row below is the `build_row()` result for the public sample record in
`sample_requests.csv`. The root `output.csv` is generated from the separate
250-row evaluation `requests.csv`, so it does not contain this sample-only
`request_21` ID.

### Final output row produced by the current pipeline

```csv
request_21,1574.4,affordable_now,full_payment,2026-04-03:1574.40,2026-04-03,none,"Pay USD 1,574.40 today. This leaves at least USD 1,800 available over the next 90 days."
```

### Gold row from `sample_requests.csv`

```csv
request_21,1543.35,affordable_with_plan,full_payment,2026-04-03:1574.40,2026-04-15,stop:event_1815|reduce_to:event_1816:23.50,"Stop the online backup subscription and reduce the streaming subscription to USD 23.50, then pay USD 1,574.40 today. This leaves at least USD 1,800 available."
```

### Differences

| Field | Prediction | Gold | Difference |
|---|---|---|---|
| `amount_safe_to_pay` | 1,574.40 | 1,543.35 | Prediction is USD 31.05 higher |
| `affordability_status` | `affordable_now` | `affordable_with_plan` | Status differs |
| `recommended_payment_method` | `full_payment` | `full_payment` | Match |
| `payment_plan` | `2026-04-03:1574.40` | `2026-04-03:1574.40` | Match |
| `earliest_date_for_full_payment` | `2026-04-03` | `2026-04-15` | Prediction is 12 days earlier |
| `spending_changes_needed` | `none` | `stop:event_1815\|reduce_to:event_1816:23.50` | Gold requires two changes |
| `decision_explanation` | Plain full-payment template | Change-prefixed full-payment template | Wording differs because the selected plan has no changes |

The central trace discrepancy is the unmodified forecast: the current pipeline
finds a USD 3,480.58 trough and USD 1,680.58 of protected capacity, so the
request is safe in full on the request date. The gold row instead expects a
lower safe amount, a later earliest date, and the two spending changes, which
causes the status and explanation to differ even though the payment method and
payment plan agree.

# Buy or Wait? Agent

## 1. Running the agent

From the repository root, run:

```bash
python3 code/main.py
```

The command reads the participant-facing CSV files from `dataset/` and writes
the required root-level `output.csv` with one row per request.

The first run that needs image extraction requires the Anthropic key in the
process environment:

```bash
export ANTHROPIC_API_KEY="<your-key>"
python3 code/main.py
```

The key is read from `os.environ` only. It is never hardcoded, written to a
report, or printed. The vision layer stores validated image results in a
content-hash cache keyed by the SHA-256 digest of the image bytes plus the
model string. The shipped `.vision_cache` contains the 16 validated image
results, and the shipped `.message_delta_cache` contains the 176 validated
message-delta results. A cold run of either perception layer requires
`ANTHROPIC_API_KEY` in the process environment; if a needed result is not in
its cache and the variable is unset, the run fails with an error naming the
required variable. With both caches present, re-running
`python3 code/main.py` makes no model calls.

Useful validation commands are:

```bash
python3 code/verify_output.py
python3 code/evaluation/score_fields.py
python3 code/test_schema.py
python3 code/test_explanations.py
```

## 2. Architecture

The system is a deterministic financial simulator with a narrow LLM perception
layer. The model does not make financial decisions.

### What the model decides

The Anthropic model is used only for two narrow perception tasks. It does not
make financial decisions.

1. **Vision extraction:** for the 16 PNG images associated with blank `amount`
   cells in `financial_events.csv`, it reports only the visible monetary
   `amount` and three-letter `currency`.
2. **Message-delta extraction:** for the 176 messages with empty
   `related_event_id`, it returns a non-empty list of structured deltas, one per
   supported financial fact in the message.

The vision request requires strict JSON with exactly `amount` and `currency`.
The message request requires strict list-valued JSON with the delta schema
`kind`, `amount`, `currency`, `effective_date`, `percent_change`,
`target_category`, and `confidence`. Client code validates both responses,
rejects malformed or unsupported values, and treats message and image content
as untrusted evidence. The event CSV remains authoritative for event currency
and lifecycle facts; no embedded instruction overrides the challenge rules.

### What deterministic code enforces

All financial reasoning is performed by Python code, including:

- all non-image event amounts, event statuses, settlement dates, linked-record
  handling, and exact-date exchange-rate conversion;
- recurrence detection, liveness, projection dates, and balance forecasts;
- minimum-balance protection and the calculation of `amount_safe_to_pay`;
- full-payment, partial-payment, installment, wait, and not-recommended plan
  construction;
- payment-option eligibility and installment constraints;
- candidate ranking;
- bounded spending-change search and the allowed `stop`/`reduce_to` actions;
- the output schema, amount formatting, payment-plan grammar, and
  `spending_changes_needed` grammar; and
- deterministic decision-explanation rendering.

The model never selects a payment method, determines affordability, chooses a
payment plan, chooses spending changes, ranks plans, or writes `output.csv`.

### Modules

- `data_layer.py` loads and validates the CSV datasets into typed records,
  indexes users, events, options, messages, images, and exchange rates, and
  lazily resolves the 16 blank amounts through the vision extractor.
- `vision.py` sends PNGs as base64 image content, validates the strict model
  response, retries once on failure, records token/latency statistics, and
  caches validated results by image-content/model hash.
- `message_deltas.py` extracts list-valued deltas from the 176 unlinked
  messages, validates each object, retries once on failure, records usage, and
  caches validated results by message text/model/schema hash. It applies the
  deterministic employer-salary normalization before forecast consumers see
  the deltas.
- `recurrence.py` detects live recurring event series using the selected
  Variant E behavior and exposes series metadata to the forecaster.
- `forecast.py` projects day-by-day home-currency balances over the 90-day
  horizon, reserving applicable debits and counting credits according to their
  cash lifecycle.
- `main.py` computes safe amounts, earliest safe full-payment dates, candidate
  plans, spending-change combinations, deterministic ranking, output fields,
  and the final 250-row `output.csv`.
- `explanations.py` renders the deterministic decision-explanation templates.
- `schema.py` defines the exact eight output columns, allowed values, grammars,
  and the distinct CSV/plan/display amount formatters.
- `verify_output.py` checks structure, allowed values, payment plans,
  cross-field constraints, spending changes, payment-option matching, and
  sample-derived conventions.
- `evaluation/score_fields.py` compares the seven scored output fields plus
  `amount_safe_to_pay` against the 25 public labelled rows.
- `evaluation/score_amount.py` is a development diagnostic from the forecast
  build that compared no-spending-change safe amounts against gold.
- `evaluation/trace_amount.py` is a development diagnostic from the forecast
  build that printed per-day balance traces and contributing events for
  individual requests.
- `evaluation/score_amount.py` and `evaluation/trace_amount.py` are superseded
  by `evaluation/score_fields.py`; they are retained for reference and are not
  used by the pipeline.
- `evaluation/compare_recurrence.py` contains the measured recurrence
  configuration comparisons documented in
  `evaluation/configuration_comparison.md`.

## 3. Design decisions

### Variant E recurrence detection

Variant E was selected from the measured recurrence experiments because it had
the lowest mean absolute error on the 25 labelled amount rows: **258,041.26**
(approximately 258,041), versus Variant A's recorded original-rule value of
**565,954.87** (approximately 565,955).

E uses the fixed GCD/median cadence acceptance, request-date liveness, the
current four-field event key, and the E rolled-anchor projection behavior. It
keeps a series live only when its latest occurrence is within 1.5 times the
cadence of the request date. Variant H covered more events and detected all
four measured sample recurrence events, but its labelled amount error was
worse, so coverage was not preferred over amount accuracy.

### Vision prompt selection

The extraction prompt asks the model for the **monetary amount shown in the
image**. An alternative prompt that asked for the outstanding balance was tested
and rejected. It returned zero for three receipts marked `PAID`—`event_3231`,
`event_7307`, and `event_1545`—where the balance genuinely is zero but the
forecast needs the transaction amount. It also misread `event_1442`'s Indian
lakh notation as **1,000,000** instead of **200,000**.

Three of the sixteen extractions were manually verified against their source
PNGs: `image_02` (rent receipt), `image_07` (restaurant invoice), and `image_12`
(taxi receipt). All three matched. A client-side validation rule rejects a zero
extracted amount and names the event, because the specification forbids treating
a blank amount as zero.

### Spending-change ranking

The bounded search considers permitted combinations of up to three changes.
The implementation ranks feasible change combinations first by **fewest
changes**, then by **smallest total monthly saving**, followed by the remaining
plan tie-breakers in code. The specification ranks plans by total amount paid;
using monthly saving as the second change-combination criterion is our
interpretation of that preference for the least disruptive plan, not a claim
that the specification literally names monthly saving.

For a `reduce_to` action, the implementation always targets the event's
`minimum_allowed_amount`. It does not invent an intermediate reduction amount.
The event must also be flexible and in a category the profile permits reducing.

### Amount rounding and formatting

The 25 rows in `sample_requests.csv` show three distinct amount formats:

1. **CSV safe amounts:** plain ungrouped decimal text with no unnecessary
   trailing zeroes, such as `17229139.2`, `603.3`, and `28820`.
2. **Payment-plan amounts:** ungrouped text; fractional values are rounded to
   exactly two decimal places, while integral values have no decimal point,
   such as `620.40`, `996.60`, `15952906.67`, and `25256`.
3. **Explanation display amounts:** thousands separators are included;
   fractional values use exactly two decimal places and integral values have no
   decimal point, such as `25,256` and `15,952,906.67`.

The code uses `Decimal` arithmetic and half-up rounding when a two-decimal
representation is required. Formatting is separate from the underlying
financial calculations.

### Scheduled events

Scheduled events are included in the forecast. They are not in the
specification's explicit exclusion list, so scheduled debits are reserved on
their settlement dates rather than silently removed. In the measured
scheduled-event ablation, excluding them improved six of seven fields by one
row on the 25 labelled rows, which is one row or 4 percentage points and within
the measured noise floor for `n=25`. The final configuration therefore keeps
scheduled events.

### Conflict resolution

The specification defines a four-level precedence for conflicting financial
records:

1. explicit cancellation, settlement, or amendment;
2. a newer record from the same source;
3. a settled record over an estimate; and
4. the financially safer interpretation.

The implementation realises this precedence through the event status filter and
linked-chain handling rather than through a separate procedural precedence
ladder. This interpretation was verified by measurement across the supplied
linked records and request windows.

`financial_events.csv` contains 58 linked chains with these parent-to-child
status transitions:

```text
cancelled -> settled:    8
failed -> scheduled:      7
settled -> pending:      14
settled -> settled:      19
settled -> unrealized:   10
```

The cancelled and failed parents are excluded by the status filter, satisfying
rule 1. All 19 settled-to-settled pairs fall outside every request's 90-day
window, so rule 2 has nothing to act on.

Of the 33 settled-to-pending and settled-to-settled chains, 13 have a record
inside a request window. All 13 are settled-to-pending, with the parent settled
before the request date. The 7 pending credits are excluded by the
specification's pending-credit rule, satisfying rule 3. The 6 pending debits
are bank-disputed card charges whose linked messages confirm that no reversal
has posted; they are retained as the financially safer interpretation,
satisfying rule 4. No chain has both records inside the same request window.

## 4. Decision explanations

`explanations.py` contains deterministic templates for:

- full payment today;
- installments;
- waiting for a later full payment;
- a two-payment partial-payment plan;
- not recommended by the requested deadline;
- not recommended because the full request cannot be completed safely; and
- a payment plan preceded by one or more spending changes.

The templates were derived by inspecting all 25 rows in
`sample_requests.csv`, including their amount punctuation, date wording,
minimum-balance wording, and change-clause grammar. The gold-input unit test
`code/test_explanations.py` reproduces **24 of 25** gold explanation strings
exactly. The remaining known mismatch is `request_09`'s wording variant using
“keeps the minimum available” instead of the implementation's “leaves at
least ... available” wording.

## 5. Message deltas

`message_deltas.py` processes the **176 messages with an empty
`related_event_id`**. It returns a non-empty list so one message can contribute
multiple independent facts. Each delta has exactly these fields:

```text
kind, amount, currency, effective_date, percent_change, target_category, confidence
```

The extractor recognizes salary amount/date changes, ended income, expense
amount changes, confirmed income, new recurring expenses, pending exclusions,
internal transfers, and `none` evidence classifications. Message text is
untrusted evidence: the extractor classifies supplied facts, while code owns
validation, normalization, date handling, recurrence, affordability, and plan
selection.

Before forecast use, code deterministically normalizes any
`confirmed_income` delta whose source message has `source_type=employer` and
`target_category=salary` to `salary_amount_change`. This corrects the unstable
classification boundary observed across extraction runs without re-extracting
or trusting a message instruction.

Five delta kinds are currently wired into `forecast.py`:

- `salary_amount_change`: changes detected salary-credit projections;
- `income_ended`: removes salary-credit projections from its effective date;
- `expense_amount_change`: scales matching category debit recurrences by the
  extracted percentage;
- `salary_date_change`: shifts a detected salary series to the stated
  day-of-month from its effective date; and
- `confirmed_income`: for a non-employer message with a complete amount,
  currency, and effective date, adds one non-recurring credit inside the
  90-day window.

Only two extracted delta kinds remain deliberately **unwired** to financial
forecasts:

- `new_recurring_expense`; and
- `internal_transfer`.

The `exclude_pending` and `none` outputs are retained as evidence/control
classifications rather than forecast amendment kinds. Only deltas belonging to
the request's user can be considered, and embedded instructions never become
directives.

The measured wiring result is narrow. Wiring `salary_amount_change` and
`income_ended` changed **2 of 25** public sample rows and improved four scored
fields by one row each: `affordability_status`,
`recommended_payment_method`, `payment_plan`, and
`decision_explanation`. `request_02` moved from `not_recommended` to the gold's
exact three-installment plan because `message_01` supplied a salary rise that
was absent from `financial_events`. Wiring `expense_amount_change` changed
**0 of 25** sample rows and remains unvalidated.

Wiring `salary_date_change` and `confirmed_income` changed **0 of 25** public
sample rows. `request_07`'s salary-date delta was ignored because that user has
no detected salary series, and `request_23`'s confirmed-income delta was
skipped because it was incomplete. Both behaviors are specification-compliant
but remain unvalidated against the labelled sample.

The final configuration with all five delta kinds wired scores **2/25** on
`amount_safe_to_pay`, **16/25** on `affordability_status`, **18/25** on
`recommended_payment_method`, **14/25** on `payment_plan`, **10/25** on
`earliest_date_for_full_payment`, **22/25** on `spending_changes_needed`, and
**8/25** on `decision_explanation` on the labelled sample.

All **39 event-linked messages** only corroborate existing event status or
lifecycle fields. That is why those messages are not used as amendment inputs:
they add no measured rule-changing information under the current evidence
rules. Messages remain available through `data_layer.py` for audit and
inspection, but their text does not override structured challenge rules.

## 6. Known limitations

These are measured or observed limitations, not guarantees about hidden data:

- The current configuration's `amount_safe_to_pay` exact match is **2 of 25**
  labelled sample rows. The **3 of 25** result was measured before the vision
  and message-delta layers were wired;
  the difference is `request_16`, where resolving the **INR 200,000**
  image-backed scheduled rent event lowers the forecast trough below the
  user's minimum balance. Manual inspection of `image_02` confirms that the
  extracted **INR 200,000** matches the receipt's stated total, but the same
  receipt shows **INR 1,00,000.00 already received** and **INR 1,00,000.00
  balance due**. The forward obligation is therefore arguably **INR 100,000**;
  the extractor reads the headline total rather than the outstanding balance.
- A known rolled-anchor bug can drop one monthly cycle when a series' next due
  date falls just before the request date. The affected edge case is retained
  as a documented limitation of the measured projection behavior.
- **5 of 25** sample users have no detected income series. The observed cause
  is description-based grouping splitting their salary events into separate
  groups, leaving no accepted income series for those users.
- `request_09`'s “keeps the minimum available” explanation variant has no
  identified deterministic trigger in the current template selector.
- Two extracted message-delta kinds remain unapplied to forecasts:
  `new_recurring_expense` and `internal_transfer`.
- The five sample-derived output conventions checked by `verify_output.py` are
  inferences from the 25 public rows, not specification rules:
  1. `wait` uses one full-amount payment on `earliest_date_for_full_payment`;
  2. `not_recommended` uses `payment_plan=none` and an empty earliest date;
  3. `affordable_now` uses `full_payment` and pays the full requested amount;
  4. `affordable_later` uses `wait`; and
  5. `not_affordable` uses `not_recommended`.

The submitted output satisfies all five conventions. They remain labelled
sample-derived because they are inferences from 25 public rows rather than
requirements in the specification.

"""verify_output.py

Most checks below are derived from explicit sentences in problem_statement.md. Five
checks are explicitly labelled sample-derived conventions; they are WARN-only
inferences rather than contract requirements. This validates the submission contract,
not the quality of the decisions.

    python code/verify_output.py

Exit 0 only when there are zero FAILs. WARNs are things to go look at; they don't gate.

Column names for the supporting CSVs are declared in COLUMNS below. They were written
from the problem statement's prose, not from the files, so **check them against the real
headers in the first 10 minutes** and fix any that differ. Any check whose columns can't
be resolved is SKIPPED loudly rather than silently passing — a skipped check is not a
passed check.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output.csv"
DATASET = ROOT / "dataset"

# ── Contract from problem_statement.md ────────────────────────────────────────

OUTPUT_COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)

AFFORDABILITY_STATUS = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}

PAYMENT_METHOD = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}

NONE_TOKEN = "none"
PLAN_SEP = "|"
CHANGE_SEP = "|"
MAX_SPENDING_CHANGES = 3
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PAYMENT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):(-?[\d.,]+)$")
STOP_RE = re.compile(r"^stop:([A-Za-z0-9_\-]+)$")
REDUCE_RE = re.compile(r"^reduce_to:([A-Za-z0-9_\-]+):(-?[\d.,]+)$")

# ── VERIFY THESE AGAINST THE REAL HEADERS ────────────────────────────────────

COLUMNS = {
    "requests": {
        "id": "request_id",
        "user": "user_id",
        "request_date": "request_date",
        "requested_amount": "requested_amount",
        "desired_completion_date": "desired_completion_date",
        "allows_partial_payment": "allows_partial_payment",
    },
    "profiles": {
        "user": "user_id",
        "methods": "payment_methods_user_will_consider",
        "max_installment_months": "max_installment_months",
        "protect": "expense_categories_to_protect",
        "reduce": "expense_categories_user_is_willing_to_reduce",
        "stop": "expense_categories_user_is_willing_to_stop",
    },
    "events": {
        "id": "event_id",
        "user": "user_id",
        "category": "category",
        "amount": "amount",
        "flexibility": "flexibility",
        "minimum_allowed_amount": "minimum_allowed_amount",
    },
    "payment_options": {
        "id": "payment_option_id",
        "request": "request_id",
        "method": "payment_method",
        "first_payment_date": "first_payment_date",
        "num_payments": "number_of_payments",
        "days_between": "payment_frequency_days",
        "installment_amount": "payment_amount",
        "total_payable": "total_payable_amount",
    },
}

# flexibility -> which spending changes that event permits
STOPPABLE = {"stoppable", "reducible_or_stoppable"}
REDUCIBLE = {"reducible", "reducible_or_stoppable"}

TRUTHY = {"1", "true", "yes", "y", "t"}

# ── Reporting ─────────────────────────────────────────────────────────────────

fails: list[str] = []
warns: list[str] = []
skips: list[str] = []


def fail(label: str, detail: str = "") -> None:
    fails.append(label)
    print(f"  FAIL  {label}{f' — {detail}' if detail else ''}")


def warn(label: str, detail: str = "") -> None:
    warns.append(label)
    print(f"  WARN  {label}{f' — {detail}' if detail else ''}")


def ok(label: str, detail: str = "") -> None:
    print(f"  PASS  {label}{f' — {detail}' if detail else ''}")


def skip(label: str, why: str) -> None:
    skips.append(label)
    print(f"  SKIP  {label} — {why}  (a skipped check is NOT a passed check)")


def check(label: str, condition: bool, detail: str = "") -> None:
    (ok if condition else fail)(label, detail)


def head(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


# ── Helpers ───────────────────────────────────────────────────────────────────


def read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def has_cols(rows: list[dict], wanted: dict, *keys: str) -> bool:
    if not rows:
        return False
    header = set(rows[0])
    return all(wanted[k] in header for k in keys)


def dec(raw: str) -> Decimal | None:
    try:
        return Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def parse_date(raw: str) -> date | None:
    if not raw or not DATE_RE.match(raw.strip()):
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_plan(raw: str) -> list[tuple[date, Decimal]] | None:
    """None means malformed. Empty list means the literal token `none`."""
    raw = (raw or "").strip()
    if raw == NONE_TOKEN:
        return []
    if not raw:
        return None
    out: list[tuple[date, Decimal]] = []
    for part in raw.split(PLAN_SEP):
        match = PAYMENT_RE.match(part.strip())
        if not match:
            return None
        when, amount = parse_date(match.group(1)), dec(match.group(2))
        if when is None or amount is None:
            return None
        out.append((when, amount))
    return out


def parse_changes(raw: str) -> list[tuple[str, str, Decimal | None]] | None:
    raw = (raw or "").strip()
    if raw == NONE_TOKEN:
        return []
    if not raw:
        return None
    out: list[tuple[str, str, Decimal | None]] = []
    for part in raw.split(CHANGE_SEP):
        part = part.strip()
        stop = STOP_RE.match(part)
        if stop:
            out.append(("stop", stop.group(1), None))
            continue
        reduce_to = REDUCE_RE.match(part)
        if reduce_to:
            amount = dec(reduce_to.group(2))
            if amount is None:
                return None
            out.append(("reduce_to", reduce_to.group(1), amount))
            continue
        return None
    return out


def option_schedule(option: dict) -> list[tuple[date, Decimal]] | None:
    """Reconstruct a payment option's schedule so an installment plan can be matched."""
    cols = COLUMNS["payment_options"]
    start = parse_date(option.get(cols["first_payment_date"], ""))
    count = dec(option.get(cols["num_payments"], ""))
    gap = dec(option.get(cols["days_between"], ""))
    amount = dec(option.get(cols["installment_amount"], ""))
    if start is None or count is None or gap is None or amount is None:
        return None
    return [
        (start + timedelta(days=int(gap) * i), amount) for i in range(int(count))
    ]


# ── Checks ────────────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 78)
    print(f"VERIFY {OUTPUT}")
    print("=" * 78)

    if not OUTPUT.exists():
        print(f"\nFAIL: {OUTPUT} does not exist")
        return 1

    with OUTPUT.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames
        rows = list(reader)

    requests = read(DATASET / "requests.csv")
    rc = COLUMNS["requests"]
    by_request = {r[rc["id"]]: r for r in requests}

    def optional(name: str) -> list[dict]:
        path = DATASET / name
        return read(path) if path.exists() else []

    profiles = optional("financial_profiles.csv")
    events = optional("financial_events.csv")
    options = optional("request_payment_options.csv")

    # ── structure ─────────────────────────────────────────────────────────────
    head("STRUCTURE")
    check(
        "header equals the required columns in order",
        header == list(OUTPUT_COLUMNS),
        f"got {header}",
    )
    check(
        f"row count == requests.csv ({len(requests)})",
        len(rows) == len(requests),
        f"got {len(rows)}",
    )
    ids = [r.get("request_id", "") for r in rows]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    check("no duplicate request_id", not dupes, f"{dupes[:5]}")
    missing = sorted(set(by_request) - set(ids))
    extra = sorted(set(ids) - set(by_request))
    check(
        "request_id set matches requests.csv exactly",
        not missing and not extra,
        f"missing {missing[:5]} extra {extra[:5]}",
    )

    # ── field values ──────────────────────────────────────────────────────────
    head("FIELD VALUES")
    bad_status = sorted({r["affordability_status"] for r in rows
                         if r["affordability_status"] not in AFFORDABILITY_STATUS})
    check("affordability_status in allowed set", not bad_status, f"{bad_status}")

    bad_method = sorted({r["recommended_payment_method"] for r in rows
                         if r["recommended_payment_method"] not in PAYMENT_METHOD})
    check("recommended_payment_method in allowed set", not bad_method, f"{bad_method}")

    bad_amount, bad_decimal_places, bad_bounds = [], [], []
    for row in rows:
        req = by_request.get(row["request_id"])
        amount = dec(row["amount_safe_to_pay"])
        if amount is None:
            bad_amount.append(row["request_id"])
            continue
        if max(0, -amount.as_tuple().exponent) > 2:
            bad_decimal_places.append(row["request_id"])
        if req is None:
            continue
        requested = dec(req[rc["requested_amount"]])
        if requested is None:
            continue
        if not (Decimal(0) <= amount <= requested):
            bad_bounds.append(f"{row['request_id']}: {amount} vs {requested}")
    check("amount_safe_to_pay parses as a number", not bad_amount, f"{bad_amount[:5]}")
    check(
        "amount_safe_to_pay has at most two decimal places",
        not bad_decimal_places,
        f"{bad_decimal_places[:5]}",
    )
    check(
        "0 <= amount_safe_to_pay <= requested_amount",
        not bad_bounds,
        f"{bad_bounds[:5]}",
    )

    bad_expl = [r["request_id"] for r in rows if not (r["decision_explanation"] or "").strip()]
    check("decision_explanation is non-empty", not bad_expl, f"{bad_expl[:5]}")

    # ── plan format ───────────────────────────────────────────────────────────
    head("PAYMENT PLAN")
    malformed, unsorted_plan = [], []
    plans: dict[str, list[tuple[date, Decimal]]] = {}
    for row in rows:
        plan = parse_plan(row["payment_plan"])
        if plan is None:
            malformed.append(row["request_id"])
            continue
        plans[row["request_id"]] = plan
        if [p[0] for p in plan] != sorted(p[0] for p in plan):
            unsorted_plan.append(row["request_id"])
    check("payment_plan is 'none' or well-formed date:amount pairs",
          not malformed, f"{malformed[:5]}")
    check("payment_plan entries are chronological", not unsorted_plan, f"{unsorted_plan[:5]}")

    bad_date = []
    for row in rows:
        raw = (row["earliest_date_for_full_payment"] or "").strip()
        if raw and parse_date(raw) is None:
            bad_date.append(f"{row['request_id']}={raw!r}")
    check("earliest_date_for_full_payment is empty or YYYY-MM-DD",
          not bad_date, f"{bad_date[:5]}")

    # ── spec-stated cross-field rules ─────────────────────────────────────────
    head("CROSS-FIELD RULES (each from an explicit line in the spec)")

    violations = []
    for row in rows:
        req = by_request.get(row["request_id"])
        if req is None or row["affordability_status"] != "affordable_now":
            continue
        if (row["earliest_date_for_full_payment"] or "").strip() != req[rc["request_date"]]:
            violations.append(row["request_id"])
    check(
        "affordable_now => earliest_date_for_full_payment == request_date",
        not violations,
        f"{violations[:5]}",
    )

    status_mismatch, precond, shape = [], [], []
    for row in rows:
        if row["recommended_payment_method"] != "partial_payment":
            continue
        rid = row["request_id"]
        req = by_request.get(rid)
        if row["affordability_status"] != "affordable_with_plan":
            status_mismatch.append(rid)
        if req is None:
            continue
        requested = dec(req[rc["requested_amount"]])
        amount = dec(row["amount_safe_to_pay"])
        earliest = parse_date(row["earliest_date_for_full_payment"])
        completion = parse_date(req[rc["desired_completion_date"]])
        allows = str(req.get(rc["allows_partial_payment"], "")).strip().lower() in TRUTHY

        if not allows:
            precond.append(f"{rid}: request does not allow partial payment")
        if amount is None or requested is None or not (Decimal(0) < amount < requested):
            precond.append(f"{rid}: needs 0 < amount_safe_to_pay < requested_amount")
        if earliest is None or completion is None or earliest > completion:
            precond.append(f"{rid}: earliest_date must be on or before desired_completion")

        plan = plans.get(rid)
        if plan is None or len(plan) != 2:
            shape.append(f"{rid}: needs exactly two payments")
            continue
        first, second = plan
        request_date = parse_date(req[rc["request_date"]])
        if first[0] != request_date or (amount is not None and first[1] != amount):
            shape.append(f"{rid}: payment 1 must be amount_safe_to_pay on request_date")
        if earliest is not None and second[0] != earliest:
            shape.append(f"{rid}: payment 2 must fall on earliest_date_for_full_payment")
        if requested is not None and first[1] + second[1] != requested:
            shape.append(f"{rid}: payments must sum to requested_amount")

    check("partial_payment => affordability_status is affordable_with_plan",
          not status_mismatch, f"{status_mismatch[:5]}")
    check("partial_payment preconditions hold", not precond, f"{precond[:5]}")
    check("partial_payment plan has the exact two-payment shape", not shape, f"{shape[:5]}")

    # ── sample-derived conventions ────────────────────────────────────────────
    head("SAMPLE-DERIVED CONVENTIONS (WARN only — inferred from 25 rows, NOT in the spec)")
    print("  Promote any of these to FAIL once you have confirmed it is a real rule.\n")

    wait_bad, nr_bad, now_bad, later_bad, na_bad = [], [], [], [], []
    for row in rows:
        rid = row["request_id"]
        req = by_request.get(rid)
        method = row["recommended_payment_method"]
        status = row["affordability_status"]
        plan = plans.get(rid)
        earliest = parse_date(row["earliest_date_for_full_payment"])
        requested = dec(req[rc["requested_amount"]]) if req else None
        amount = dec(row["amount_safe_to_pay"])

        # every sample `wait` row carries ONE payment for the full amount on the
        # earliest safe date — it is not `none`
        if method == "wait":
            if not plan or len(plan) != 1:
                wait_bad.append(f"{rid}: wait needs exactly one payment")
            else:
                when, value = plan[0]
                if earliest is not None and when != earliest:
                    wait_bad.append(f"{rid}: wait payment not on earliest_date")
                if requested is not None and value != requested:
                    wait_bad.append(f"{rid}: wait payment != requested_amount")

        # every sample `not_recommended` row has plan=none and an empty earliest date
        if method == "not_recommended":
            if plan != []:
                nr_bad.append(f"{rid}: expected payment_plan=none")
            if (row["earliest_date_for_full_payment"] or "").strip():
                nr_bad.append(f"{rid}: expected empty earliest_date")

        # affordable_now appeared only with full_payment and amount == requested
        if status == "affordable_now":
            if method != "full_payment":
                now_bad.append(f"{rid}: affordable_now with {method}")
            if requested is not None and amount is not None and amount != requested:
                now_bad.append(f"{rid}: affordable_now but amount_safe_to_pay != requested")

        if status == "affordable_later" and method != "wait":
            later_bad.append(f"{rid}: affordable_later with {method}")
        if status == "not_affordable" and method != "not_recommended":
            na_bad.append(f"{rid}: not_affordable with {method}")

    for label, offenders in (
        ("wait => one payment of the full amount on earliest_date", wait_bad),
        ("not_recommended => payment_plan 'none' and empty earliest_date", nr_bad),
        ("affordable_now => full_payment and amount_safe_to_pay == requested", now_bad),
        ("affordable_later => wait", later_bad),
        ("not_affordable => not_recommended", na_bad),
    ):
        (ok if not offenders else warn)(label, f"{offenders[:5]}")

    # ── installments match a supplied option ──────────────────────────────────
    head("INSTALLMENTS MATCH A SUPPLIED PAYMENT OPTION")
    pc = COLUMNS["payment_options"]
    if not options:
        skip("installment plans match a supplied option", "request_payment_options.csv not found")
    elif not has_cols(options, pc, "request", "first_payment_date", "num_payments",
                      "days_between", "installment_amount"):
        skip("installment plans match a supplied option",
             f"expected columns not in header: {sorted(options[0])}")
    else:
        by_req_options = defaultdict(list)
        for option in options:
            by_req_options[option[pc["request"]]].append(option)
        unmatched, total_mismatch = [], []
        for row in rows:
            if row["recommended_payment_method"] != "installments":
                continue
            rid = row["request_id"]
            plan = plans.get(rid)
            if not plan:
                unmatched.append(f"{rid}: no plan")
                continue
            matched_option = None
            for option in by_req_options.get(rid, []):
                sched = option_schedule(option)
                if sched is not None and sched == plan:
                    matched_option = option
                    break
            if matched_option is None:
                unmatched.append(rid)
                continue
            payable = dec(matched_option.get(pc["total_payable"], ""))
            if payable is not None and sum(a for _, a in plan) != payable:
                total_mismatch.append(
                    f"{rid}: plan sums to {sum(a for _, a in plan)} but option says {payable}")
        check("every installment plan exactly matches a supplied option",
              not unmatched, f"{unmatched[:5]}")

        check("installment plan total equals the option's total_payable_amount",
              not total_mismatch, f"{total_mismatch[:5]}")

        pfc3 = COLUMNS["profiles"]
        if profiles and has_cols(profiles, pfc3, "user", "max_installment_months"):
            months_by_user = {p[pfc3["user"]]: dec(p.get(pfc3["max_installment_months"], ""))
                              for p in profiles}
            too_long = []
            for row in rows:
                if row["recommended_payment_method"] != "installments":
                    continue
                req = by_request.get(row["request_id"])
                plan = plans.get(row["request_id"]) or []
                if req is None or not plan:
                    continue
                cap = months_by_user.get(req[rc["user"]])
                if cap is None:
                    continue
                span_months = Decimal((plan[-1][0] - plan[0][0]).days) / Decimal(30) + 1
                if span_months > cap:
                    too_long.append(f"{row['request_id']}: ~{span_months:.1f}mo > cap {cap}")
            check("installment plan fits max_installment_months",
                  not too_long, f"{too_long[:5]}")
        else:
            skip("installment plan fits max_installment_months", "column missing")

    # ── spending changes ──────────────────────────────────────────────────────
    head("SPENDING CHANGES")
    ec = COLUMNS["events"]
    malformed_changes, too_many, both_ways = [], [], []
    referenced: list[tuple[str, str]] = []
    for row in rows:
        changes = parse_changes(row["spending_changes_needed"])
        if changes is None:
            malformed_changes.append(row["request_id"])
            continue
        if len(changes) > MAX_SPENDING_CHANGES:
            too_many.append(f"{row['request_id']}={len(changes)}")
        seen = defaultdict(set)
        for kind, event_id, _ in changes:
            seen[event_id].add(kind)
            referenced.append((row["request_id"], event_id))
        for event_id, kinds in seen.items():
            if len(kinds) > 1:
                both_ways.append(f"{row['request_id']}:{event_id}")

    check("spending_changes_needed is 'none' or well-formed",
          not malformed_changes, f"{malformed_changes[:5]}")
    check(f"at most {MAX_SPENDING_CHANGES} changes per row", not too_many, f"{too_many[:5]}")
    check("no event is both stopped and reduced in the same row",
          not both_ways, f"{both_ways[:5]}")

    if not events:
        skip("spending changes target flexible recurring events", "financial_events.csv not found")
    elif not has_cols(events, ec, "id"):
        skip("spending changes target flexible recurring events",
             f"expected columns not in header: {sorted(events[0])}")
    else:
        known = {e[ec["id"]] for e in events}
        unknown = sorted({f"{r}:{e}" for r, e in referenced if e not in known})
        check("every referenced event_id exists", not unknown, f"{unknown[:5]}")

        by_event = {e[ec["id"]]: e for e in events}

        if has_cols(events, ec, "flexibility"):
            wrong_kind = []
            for row in rows:
                changes = parse_changes(row["spending_changes_needed"]) or []
                for kind, event_id, new_amount in changes:
                    event = by_event.get(event_id)
                    if event is None:
                        continue
                    flex = (event.get(ec["flexibility"], "") or "").strip().lower()
                    if kind == "stop" and flex not in STOPPABLE:
                        wrong_kind.append(f"{row['request_id']}:{event_id} stop but flexibility={flex}")
                    if kind == "reduce_to" and flex not in REDUCIBLE:
                        wrong_kind.append(f"{row['request_id']}:{event_id} reduce but flexibility={flex}")
            check("stop/reduce matches the event's flexibility value",
                  not wrong_kind, f"{wrong_kind[:5]}")
        else:
            skip("stop/reduce matches the event's flexibility value",
                 f"no '{ec['flexibility']}' column in financial_events.csv")

        if has_cols(events, ec, "minimum_allowed_amount", "amount"):
            floor_violations = []
            for row in rows:
                changes = parse_changes(row["spending_changes_needed"]) or []
                for kind, event_id, new_amount in changes:
                    if kind != "reduce_to" or new_amount is None:
                        continue
                    event = by_event.get(event_id)
                    if event is None:
                        continue
                    floor = dec(event.get(ec["minimum_allowed_amount"], ""))
                    current = dec(event.get(ec["amount"], ""))
                    if floor is not None and new_amount < floor:
                        floor_violations.append(
                            f"{row['request_id']}:{event_id} {new_amount} < minimum {floor}")
                    if current is not None and new_amount >= current:
                        floor_violations.append(
                            f"{row['request_id']}:{event_id} reduce_to {new_amount} not below current {current}")
            check("reduce_to respects minimum_allowed_amount and lowers the amount",
                  not floor_violations, f"{floor_violations[:5]}")
        else:
            skip("reduce_to respects minimum_allowed_amount", "columns missing")

        # The profile gates changes by CATEGORY, independently of the event's flexibility.
        pfc2 = COLUMNS["profiles"]
        if profiles and has_cols(profiles, pfc2, "user", "protect", "reduce", "stop") \
                and has_cols(events, ec, "category"):
            prof_by_user = {p[pfc2["user"]]: p for p in profiles}

            def cats(profile, key):
                return {c.strip() for c in re.split(r"[|,;]", profile.get(pfc2[key], "") or "") if c.strip()}

            category_violations = []
            for row in rows:
                req = by_request.get(row["request_id"])
                if req is None:
                    continue
                profile = prof_by_user.get(req[rc["user"]])
                if profile is None:
                    continue
                protected = cats(profile, "protect")
                willing_reduce = cats(profile, "reduce")
                willing_stop = cats(profile, "stop")
                for kind, event_id, _ in parse_changes(row["spending_changes_needed"]) or []:
                    event = by_event.get(event_id)
                    if event is None:
                        continue
                    category = (event.get(ec["category"], "") or "").strip()
                    if category in protected:
                        category_violations.append(
                            f"{row['request_id']}:{event_id} category {category} is protected")
                    elif kind == "stop" and category not in willing_stop:
                        category_violations.append(
                            f"{row['request_id']}:{event_id} user will not stop {category}")
                    elif kind == "reduce_to" and category not in willing_reduce:
                        category_violations.append(
                            f"{row['request_id']}:{event_id} user will not reduce {category}")
            check("spending changes respect the user's protect/reduce/stop categories",
                  not category_violations, f"{category_violations[:5]}")
        else:
            skip("spending changes respect the user's category preferences", "columns missing")

    # ── eligibility ───────────────────────────────────────────────────────────
    head("PAYMENT-METHOD ELIGIBILITY")
    pfc = COLUMNS["profiles"]
    if not profiles or not has_cols(profiles, pfc, "user", "methods"):
        skip("recommended method is one the user will consider",
             "financial_profiles.csv missing or column name differs")
    else:
        accepted = {
            p[pfc["user"]]: {m.strip() for m in re.split(r"[|,;]", p[pfc["methods"]] or "")}
            for p in profiles
        }
        offenders = []
        for row in rows:
            method = row["recommended_payment_method"]
            if method in {"not_recommended"}:
                continue
            req = by_request.get(row["request_id"])
            if req is None:
                continue
            allowed = accepted.get(req[rc["user"]], set())
            needed = "full_payment" if method == "wait" else method
            if allowed and needed not in allowed:
                offenders.append(f"{row['request_id']}: {method} but user accepts {sorted(allowed)}")
        check("recommended method is eligible for this user", not offenders, f"{offenders[:5]}")

    # ── distributions ─────────────────────────────────────────────────────────
    head("DISTRIBUTIONS  (eyeball against sample_requests.csv)")
    total = len(rows) or 1
    for column in ("affordability_status", "recommended_payment_method"):
        counts = Counter(r[column] for r in rows)
        print(f"\n  {column}")
        for key, n in counts.most_common():
            print(f"    {key:<24}{n:>5}{n / total:>8.1%}")

    none_plans = sum(1 for r in rows if (r["payment_plan"] or "").strip() == NONE_TOKEN)
    none_changes = sum(1 for r in rows
                       if (r["spending_changes_needed"] or "").strip() == NONE_TOKEN)
    empty_dates = sum(1 for r in rows
                      if not (r["earliest_date_for_full_payment"] or "").strip())
    print(f"\n  payment_plan == none            {none_plans:>5}{none_plans / total:>8.1%}")
    print(f"  spending_changes == none        {none_changes:>5}{none_changes / total:>8.1%}")
    print(f"  earliest_date empty             {empty_dates:>5}{empty_dates / total:>8.1%}")

    explanations = Counter(r["decision_explanation"] for r in rows)
    top, top_n = explanations.most_common(1)[0]
    print(f"\n  distinct explanations           {len(explanations):>5}")
    print(f"  most common covers              {top_n:>5}{top_n / total:>8.1%}")
    if top_n / total > 0.20:
        warn("one explanation covers over 20% of the file",
             "August's judge called out exactly this pattern")

    spans = defaultdict(set)
    for row in rows:
        spans[row["decision_explanation"]].add(row["recommended_payment_method"])
    straddling = {e for e, methods in spans.items() if len(methods) > 1}
    if straddling:
        warn(f"{len(straddling)} explanation string(s) appear under more than one method",
             "an explanation can't be the operative reason for both")

    # ── summary ───────────────────────────────────────────────────────────────
    head("SUMMARY")
    print(f"  FAIL {len(fails)} · WARN {len(warns)} · SKIP {len(skips)}")
    for label in fails:
        print(f"    FAIL  {label}")
    for label in skips:
        print(f"    SKIP  {label}")
    print()
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
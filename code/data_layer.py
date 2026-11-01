"""Typed access to the supplied CSV datasets and lazy image resolution.

This module loads and indexes source data, and ``resolve_blank_amounts`` may
call the configured vision extractor/model lazily. It does not forecast
balances or make payment decisions.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional


DEFAULT_DATASET_DIR = Path(__file__).resolve().parents[1] / "dataset"


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True)
class SampleRequestRecord:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: Optional[date]
    spending_changes_needed: str
    decision_explanation: str


@dataclass(frozen=True)
class FinancialProfileRecord:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: str
    expense_categories_to_protect: str
    expense_categories_user_is_willing_to_reduce: str
    expense_categories_user_is_willing_to_stop: str
    payment_methods_user_will_consider: str
    max_installment_months: Optional[int]


@dataclass(frozen=True)
class FinancialEventRecord:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Optional[Decimal]
    currency: str
    event_date: Optional[date]
    settlement_date: Optional[date]
    status: str
    linked_event_id: Optional[str]
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]


@dataclass(frozen=True)
class PaymentOptionRecord:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: Optional[date]
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass(frozen=True)
class ExchangeRateRecord:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass(frozen=True)
class MessageRecord:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: datetime
    source_type: str
    message_text: str


@dataclass(frozen=True)
class ImageRecord:
    image_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]


def _read_csv(dataset_dir: Path, filename: str) -> list[dict[str, str]]:
    path = dataset_dir / filename
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _text(row: dict[str, str], field: str) -> str:
    return row[field].strip()


def _optional_text(row: dict[str, str], field: str) -> Optional[str]:
    value = _text(row, field)
    return value or None


def _decimal(row: dict[str, str], field: str, *, optional: bool = False) -> Optional[Decimal]:
    value = _text(row, field)
    if not value and optional:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid Decimal in {field}: {value!r}") from exc


def _date(row: dict[str, str], field: str, *, optional: bool = False) -> Optional[date]:
    value = _text(row, field)
    if not value and optional:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date in {field}: {value!r}") from exc


def _datetime(row: dict[str, str], field: str) -> datetime:
    value = _text(row, field)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid datetime in {field}: {value!r}") from exc


def _bool(row: dict[str, str], field: str) -> bool:
    value = _text(row, field).lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"Invalid boolean in {field}: {value!r}")


def _int(row: dict[str, str], field: str, *, optional: bool = False) -> Optional[int]:
    value = _text(row, field)
    if not value and optional:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid integer in {field}: {value!r}") from exc


def _request(row: dict[str, str]) -> RequestRecord:
    return RequestRecord(
        request_id=_text(row, "request_id"),
        user_id=_text(row, "user_id"),
        request_date=_date(row, "request_date"),
        request_type=_text(row, "request_type"),
        requested_amount=_decimal(row, "requested_amount"),
        desired_completion_date=_date(row, "desired_completion_date"),
        allows_partial_payment=_bool(row, "allows_partial_payment"),
        request_text=_text(row, "request_text"),
    )


def _sample_request(row: dict[str, str]) -> SampleRequestRecord:
    return SampleRequestRecord(
        request_id=_text(row, "request_id"),
        user_id=_text(row, "user_id"),
        request_date=_date(row, "request_date"),
        request_type=_text(row, "request_type"),
        requested_amount=_decimal(row, "requested_amount"),
        desired_completion_date=_date(row, "desired_completion_date"),
        allows_partial_payment=_bool(row, "allows_partial_payment"),
        request_text=_text(row, "request_text"),
        amount_safe_to_pay=_decimal(row, "amount_safe_to_pay"),
        affordability_status=_text(row, "affordability_status"),
        recommended_payment_method=_text(row, "recommended_payment_method"),
        payment_plan=_text(row, "payment_plan"),
        earliest_date_for_full_payment=_date(
            row, "earliest_date_for_full_payment", optional=True
        ),
        spending_changes_needed=_text(row, "spending_changes_needed"),
        decision_explanation=_text(row, "decision_explanation"),
    )


def _profile(row: dict[str, str]) -> FinancialProfileRecord:
    return FinancialProfileRecord(
        user_id=_text(row, "user_id"),
        home_currency=_text(row, "home_currency"),
        current_available_balance=_decimal(row, "current_available_balance"),
        minimum_balance_to_keep=_decimal(row, "minimum_balance_to_keep"),
        financial_priorities=_text(row, "financial_priorities"),
        expense_categories_to_protect=_text(row, "expense_categories_to_protect"),
        expense_categories_user_is_willing_to_reduce=_text(
            row, "expense_categories_user_is_willing_to_reduce"
        ),
        expense_categories_user_is_willing_to_stop=_text(
            row, "expense_categories_user_is_willing_to_stop"
        ),
        payment_methods_user_will_consider=_text(
            row, "payment_methods_user_will_consider"
        ),
        max_installment_months=_int(row, "max_installment_months", optional=True),
    )


def _event(row: dict[str, str]) -> FinancialEventRecord:
    return FinancialEventRecord(
        event_id=_text(row, "event_id"),
        user_id=_text(row, "user_id"),
        event_type=_text(row, "event_type"),
        description=_text(row, "description"),
        category=_text(row, "category"),
        direction=_text(row, "direction"),
        amount=_decimal(row, "amount", optional=True),
        currency=_text(row, "currency"),
        event_date=_date(row, "event_date", optional=True),
        settlement_date=_date(row, "settlement_date", optional=True),
        status=_text(row, "status"),
        linked_event_id=_optional_text(row, "linked_event_id"),
        flexibility=_text(row, "flexibility"),
        minimum_allowed_amount=_decimal(
            row, "minimum_allowed_amount", optional=True
        ),
    )


def _option(row: dict[str, str]) -> PaymentOptionRecord:
    return PaymentOptionRecord(
        payment_option_id=_text(row, "payment_option_id"),
        request_id=_text(row, "request_id"),
        payment_method=_text(row, "payment_method"),
        payment_amount=_decimal(row, "payment_amount"),
        number_of_payments=_int(row, "number_of_payments"),
        first_payment_date=_date(row, "first_payment_date", optional=True),
        payment_frequency_days=_int(row, "payment_frequency_days", optional=True),
        financing_fee=_decimal(row, "financing_fee"),
        total_payable_amount=_decimal(row, "total_payable_amount"),
    )


def _rate(row: dict[str, str]) -> ExchangeRateRecord:
    return ExchangeRateRecord(
        rate_date=_date(row, "rate_date"),
        from_currency=_text(row, "from_currency"),
        to_currency=_text(row, "to_currency"),
        rate=_decimal(row, "rate"),
    )


def _message(row: dict[str, str]) -> MessageRecord:
    return MessageRecord(
        message_id=_text(row, "message_id"),
        user_id=_text(row, "user_id"),
        request_id=_optional_text(row, "request_id"),
        related_event_id=_optional_text(row, "related_event_id"),
        sent_at=_datetime(row, "sent_at"),
        source_type=_text(row, "source_type"),
        message_text=_text(row, "message_text"),
    )


def _image(row: dict[str, str]) -> ImageRecord:
    return ImageRecord(
        image_id=_text(row, "image_id"),
        user_id=_text(row, "user_id"),
        request_id=_optional_text(row, "request_id"),
        related_event_id=_optional_text(row, "related_event_id"),
    )


class DataLayer:
    """Load and index all participant-facing CSV records."""

    def __init__(self, dataset_dir: Path | str = DEFAULT_DATASET_DIR) -> None:
        self.dataset_dir = Path(dataset_dir)

        self.requests: tuple[RequestRecord, ...] = tuple(
            _request(row) for row in _read_csv(self.dataset_dir, "requests.csv")
        )
        self.sample_requests: tuple[SampleRequestRecord, ...] = tuple(
            _sample_request(row)
            for row in _read_csv(self.dataset_dir, "sample_requests.csv")
        )
        self.profiles: tuple[FinancialProfileRecord, ...] = tuple(
            _profile(row)
            for row in _read_csv(self.dataset_dir, "financial_profiles.csv")
        )
        self.events: tuple[FinancialEventRecord, ...] = tuple(
            _event(row)
            for row in _read_csv(self.dataset_dir, "financial_events.csv")
        )
        self.payment_options: tuple[PaymentOptionRecord, ...] = tuple(
            _option(row)
            for row in _read_csv(self.dataset_dir, "request_payment_options.csv")
        )
        self.exchange_rates: tuple[ExchangeRateRecord, ...] = tuple(
            _rate(row)
            for row in _read_csv(self.dataset_dir, "exchange_rates.csv")
        )
        self.messages: tuple[MessageRecord, ...] = tuple(
            _message(row) for row in _read_csv(self.dataset_dir, "messages.csv")
        )
        self.images: tuple[ImageRecord, ...] = tuple(
            _image(row) for row in _read_csv(self.dataset_dir, "images.csv")
        )

        self._profiles_by_user = {record.user_id: record for record in self.profiles}
        self._events_by_user: dict[str, list[FinancialEventRecord]] = {}
        for record in self.events:
            self._events_by_user.setdefault(record.user_id, []).append(record)
        self._blank_amounts_resolved = False
        self.vision_extracted_currencies: dict[str, str] = {}
        self.vision_currency_disagreements: dict[str, tuple[str, str]] = {}

        self._options_by_request: dict[str, list[PaymentOptionRecord]] = {}
        for record in self.payment_options:
            self._options_by_request.setdefault(record.request_id, []).append(record)

        self._messages_by_user: dict[str, list[MessageRecord]] = {}
        self._messages_by_event: dict[str, list[MessageRecord]] = {}
        for record in self.messages:
            self._messages_by_user.setdefault(record.user_id, []).append(record)
            if record.related_event_id is not None:
                self._messages_by_event.setdefault(record.related_event_id, []).append(record)

        self._images_by_event: dict[str, list[ImageRecord]] = {}
        for record in self.images:
            if record.related_event_id is not None:
                self._images_by_event.setdefault(record.related_event_id, []).append(record)

        self._rates_by_key = {
            (record.rate_date, record.from_currency, record.to_currency): record.rate
            for record in self.exchange_rates
        }

    def profile_for(self, user_id: str) -> FinancialProfileRecord:
        """Return the profile for ``user_id`` or raise ``KeyError``."""

        try:
            return self._profiles_by_user[user_id]
        except KeyError as exc:
            raise KeyError(f"No financial profile for user_id={user_id!r}") from exc

    def events_for(self, user_id: str) -> tuple[FinancialEventRecord, ...]:
        """Return all financial events for ``user_id`` in source-file order."""

        return tuple(self._events_by_user.get(user_id, ()))

    def resolve_blank_amounts(self, extractor=None) -> None:
        """Fill blank event amounts from their single matching image.

        Resolution is lazy so importing or loading the data layer never makes
        an API call. Once resolved, replacement records are indexed exactly as
        ordinary event records and all downstream forecast code sees the
        extracted amount and currency.
        """

        if self._blank_amounts_resolved:
            return
        blank_events = [event for event in self.events if event.amount is None]
        if not blank_events:
            self._blank_amounts_resolved = True
            return
        if extractor is None:
            try:
                from .vision import VisionAmountExtractor
            except ImportError:  # pragma: no cover - direct script execution
                from vision import VisionAmountExtractor
            extractor = VisionAmountExtractor(self.dataset_dir)
        resolved: list[FinancialEventRecord] = []
        for event in self.events:
            if event.amount is not None:
                resolved.append(event)
                continue
            matching_images = self.images_for(event.event_id)
            if len(matching_images) != 1:
                raise RuntimeError(
                    f"{event.event_id}: expected exactly one matching image, "
                    f"found {len(matching_images)}"
                )
            if hasattr(extractor, "resolve_event"):
                resolved_event = extractor.resolve_event(event, self)
                resolved.append(resolved_event)
            else:
                # Keep compatibility with simple test doubles while retaining
                # the event's own currency as the forecast authority.
                extracted = extractor.extract(event.event_id, matching_images[0].image_id)
                resolved.append(replace(event, amount=extracted.amount))
        self.events = tuple(resolved)
        if hasattr(extractor, "extracted_currencies"):
            self.vision_extracted_currencies.update(extractor.extracted_currencies)
        if hasattr(extractor, "currency_disagreements"):
            self.vision_currency_disagreements.update(extractor.currency_disagreements)
        self._events_by_user = {}
        for record in self.events:
            self._events_by_user.setdefault(record.user_id, []).append(record)
        self._blank_amounts_resolved = True

    def options_for(self, request_id: str) -> tuple[PaymentOptionRecord, ...]:
        """Return all payment options for ``request_id`` in source-file order."""

        return tuple(self._options_by_request.get(request_id, ()))

    def messages_for(
        self, user_id: str, request_id: Optional[str]
    ) -> tuple[MessageRecord, ...]:
        """Return user-level and matching request-level messages.

        A blank request ID in ``messages.csv`` denotes a user-level message and
        is included alongside messages explicitly tied to ``request_id``.
        """

        return tuple(
            message
            for message in self._messages_by_user.get(user_id, ())
            if message.request_id is None
            or request_id is None
            or message.request_id == request_id
        )

    def messages_for_event(self, event_id: str) -> tuple[MessageRecord, ...]:
        """Return messages whose ``related_event_id`` is ``event_id``."""

        return tuple(self._messages_by_event.get(event_id, ()))

    def images_for(self, event_id: str) -> tuple[ImageRecord, ...]:
        """Return images whose ``related_event_id`` is ``event_id``."""

        return tuple(self._images_by_event.get(event_id, ()))

    def rate(self, from_ccy: str, to_ccy: str, on_date: date) -> Decimal:
        # Try exact direction, then the exact-date inverse; never use a nearest date.
        """Return an exact-date rate, using an exact-date inverse if necessary.

        Same-currency conversion uses identity. If neither exact direction nor
        its exact-date inverse exists, this raises ``KeyError``.
        """

        if from_ccy == to_ccy:
            return Decimal("1")

        direct_key = (on_date, from_ccy, to_ccy)
        direct_rate = self._rates_by_key.get(direct_key)
        if direct_rate is not None:
            return direct_rate

        reverse_key = (on_date, to_ccy, from_ccy)
        reverse_rate = self._rates_by_key.get(reverse_key)
        if reverse_rate is not None:
            if reverse_rate == 0:
                raise ValueError(
                    f"Cannot invert zero exchange rate for {to_ccy}->{from_ccy} "
                    f"on {on_date.isoformat()}"
                )
            return Decimal("1") / reverse_rate

        raise KeyError(
            f"No exchange rate for {from_ccy}->{to_ccy} or inverse on "
            f"{on_date.isoformat()}"
        )


_DEFAULT_DATA_LAYER = DataLayer()


def profile_for(user_id: str) -> FinancialProfileRecord:
    return _DEFAULT_DATA_LAYER.profile_for(user_id)


def events_for(user_id: str) -> tuple[FinancialEventRecord, ...]:
    return _DEFAULT_DATA_LAYER.events_for(user_id)


def options_for(request_id: str) -> tuple[PaymentOptionRecord, ...]:
    return _DEFAULT_DATA_LAYER.options_for(request_id)


def messages_for(
    user_id: str, request_id: Optional[str]
) -> tuple[MessageRecord, ...]:
    return _DEFAULT_DATA_LAYER.messages_for(user_id, request_id)


def messages_for_event(event_id: str) -> tuple[MessageRecord, ...]:
    return _DEFAULT_DATA_LAYER.messages_for_event(event_id)


def images_for(event_id: str) -> tuple[ImageRecord, ...]:
    return _DEFAULT_DATA_LAYER.images_for(event_id)


def rate(from_ccy: str, to_ccy: str, on_date: date) -> Decimal:
    return _DEFAULT_DATA_LAYER.rate(from_ccy, to_ccy, on_date)


__all__ = [
    "DataLayer",
    "RequestRecord",
    "SampleRequestRecord",
    "FinancialProfileRecord",
    "FinancialEventRecord",
    "PaymentOptionRecord",
    "ExchangeRateRecord",
    "MessageRecord",
    "ImageRecord",
    "profile_for",
    "events_for",
    "options_for",
    "messages_for",
    "messages_for_event",
    "images_for",
    "rate",
]

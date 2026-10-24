"""Core data models, enums, and constants for the Bike Lane Obstruction Filter.

This module defines the foundational types used throughout the pipeline. The
classification logic, I/O shell, and entry point are layered on top of these
models in later tasks; Task 1 establishes only the structure, data models, and
module-level constants.

Design reference: ``.kiro/specs/bike-lane-obstruction-filter/design.md``
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Iterator, Optional, Union


# ---------------------------------------------------------------------------
# Coordinate bounds (Requirement 5.2)
#
# Boston-area geographic bounds, inclusive on both ends. A record's coordinates
# are valid only when longitude falls within [LONGITUDE_MIN, LONGITUDE_MAX] and
# latitude falls within [LATITUDE_MIN, LATITUDE_MAX].
# ---------------------------------------------------------------------------
LONGITUDE_MIN: float = -71.3
LONGITUDE_MAX: float = -70.9
LATITUDE_MIN: float = 42.2
LATITUDE_MAX: float = 42.5


# ---------------------------------------------------------------------------
# Output projection field order (Requirements 6.3, 6.4, 6.5)
#
# Exactly these fields, in this order, are written to the Output_Dataset. There
# is deliberately no severity or weighting column (R6.5): every verified
# obstruction is an equal-weight safety signal.
# ---------------------------------------------------------------------------
OUTPUT_FIELDS: tuple[str, ...] = (
    "case_id",
    "case_topic",
    "service_name",
    "closure_comments",
    "longitude",
    "latitude",
)


class Schema(Enum):
    """The detected Source_Schema of a Source_File (Requirements 1.2, 1.6).

    Boston publishes 311 data as one export per year and the column layout has
    drifted over time, so the pipeline supports two schemas:

    * ``LEGACY`` — the original export (for example ``tmp7o3lwr4m.csv``) with a
      human-written ``closure_comments`` narrative. Narrative_Text is drawn from
      ``closure_comments``.
    * ``CURRENT`` — the 2025+ open-data schema (for example
      ``lagan_311_open_data_2025_rvsd3.csv``) which renames the identity/topic/
      service columns and omits ``closure_comments`` entirely; its surviving
      resolution narrative lives in ``closure_reason``, so Narrative_Text is
      drawn from there.

    The ``SchemaAdapter`` (Task 14.2) detects which schema a file uses and maps
    its columns onto the common :class:`SourceRecord` so every downstream gate
    is schema-agnostic.
    """

    LEGACY = "legacy"
    CURRENT = "current"


# ---------------------------------------------------------------------------
# Per-schema column maps (Requirements 1.2, 1.6)
#
# Each map translates an internal ``SourceRecord`` field name onto the source
# column name that supplies it under a given schema. The ``SchemaAdapter``
# (Task 14.2) consumes these to map a raw row onto the common record, so the
# schema-specific column vocabulary lives in named module-level constants that
# can be reviewed and extended without touching control flow (design
# "Maintainability").
#
# ``narrative_text`` is the schema-selected Narrative_Text the verification gate
# reads: it comes from ``closure_comments`` under the legacy schema and from
# ``closure_reason`` under the current open-data schema. ``closure_reason`` is
# also carried through independently in both schemas as a secondary signal.
# ---------------------------------------------------------------------------

# Legacy export (for example ``tmp7o3lwr4m.csv``): the internal field names and
# the source column names coincide. Narrative_Text is the human-written
# ``closure_comments``. ``closure_comments`` is retained so the output
# projection's ``closure_comments`` column stays populated for legacy rows.
LEGACY_COLUMN_MAP: dict[str, str] = {
    "case_id": "case_id",
    "case_topic": "case_topic",
    "service_name": "service_name",
    "narrative_text": "closure_comments",
    "closure_comments": "closure_comments",
    "closure_reason": "closure_reason",
    "case_status": "case_status",
    "longitude": "longitude",
    "latitude": "latitude",
}

# Current open-data schema (2025+, for example
# ``lagan_311_open_data_2025_rvsd3.csv``): identity/topic/service columns are
# renamed and there is NO ``closure_comments`` column. The surviving resolution
# narrative lives in ``closure_reason``, so Narrative_Text is drawn from it.
CURRENT_COLUMN_MAP: dict[str, str] = {
    "case_id": "case_enquiry_id",
    "case_topic": "case_title",
    "service_name": "type",
    "narrative_text": "closure_reason",
    "closure_reason": "closure_reason",
    "case_status": "case_status",
    "longitude": "longitude",
    "latitude": "latitude",
}

# Lookup from detected :class:`Schema` to its column map, so the adapter can
# select the right mapping by schema without a branch per call site.
COLUMN_MAPS: dict[Schema, dict[str, str]] = {
    Schema.LEGACY: LEGACY_COLUMN_MAP,
    Schema.CURRENT: CURRENT_COLUMN_MAP,
}


class Verification(Enum):
    """Result of the verification gate (Requirement 4).

    ``CONFIRMED`` means the closure comment affirmatively asserts that a
    physical bike-lane obstruction occurred, was ticketed, or was addressed.
    ``UNVERIFIED`` covers informational, negative, and ambiguous comments, and
    is the conservative default for a safety-first downstream consumer (R4.4).
    """

    CONFIRMED = "confirmed"
    UNVERIFIED = "unverified"


class ExclusionReason(Enum):
    """The single reason a record was dropped from the Output_Dataset.

    These are exactly the reason buckets reported in the end-of-run summary
    (Requirement 7.3), plus ``parse_error`` for rows that could not be mapped
    to the expected fields (R1.4). Each excluded record carries exactly one of
    these reasons, corresponding to the first gate it failed.
    """

    PARSE_ERROR = "parse_error"
    NO_BIKE_LANE_CONTEXT = "no_bike_lane_context"
    UNRELATED_RECORD = "unrelated_record"
    UNVERIFIED_RECORD = "unverified_record"
    INVALID_COORDINATES = "invalid_coordinates"


@dataclass(frozen=True)
class SourceRecord:
    """A single parsed row from the Source_Dataset (``tmp7o3lwr4m.csv``).

    Only the fields the pipeline uses are modeled here; other source columns
    are read but ignored for classification. Coordinate fields are kept as raw
    strings (as read from the CSV) so the coordinate gate can decide whether
    they parse as numeric values (R5.1); ``None`` represents an absent field.

    ``row_number`` provides a fallback identity for logging when ``case_id`` is
    missing or unreadable (R1.4).

    ``narrative_text`` is the common Narrative_Text the classification gates
    read, so they stay schema-agnostic (design "Narrative_Text"). Under the
    legacy schema it equals ``closure_comments``; under the current open-data
    schema the ``SchemaAdapter`` populates it from ``closure_reason`` (Task
    14.2). Both ``narrative_text`` and ``source_file`` default so existing
    positional/keyword construction keeps working: when ``narrative_text`` is
    not supplied, :meth:`__post_init__` falls it back to ``closure_comments``,
    giving every legacy record (which only sets ``closure_comments``) a
    populated Narrative_Text without any caller change.

    ``source_file`` names the originating Source_File for per-file logging and
    reporting (R1.5, R7.4); it is ``None`` until a multi-file reader attaches it.
    """

    case_id: Optional[str]
    case_topic: Optional[str]
    service_name: Optional[str]
    closure_comments: Optional[str]
    closure_reason: Optional[str]
    case_status: Optional[str]
    longitude: Optional[str]
    latitude: Optional[str]
    row_number: int
    narrative_text: Optional[str] = None
    source_file: Optional[str] = None

    def __post_init__(self) -> None:
        """Fall ``narrative_text`` back to ``closure_comments`` when unset.

        The classification gates read a single ``narrative_text`` field so they
        are identical across schemas. Legacy callers (including every existing
        test) construct a record with only ``closure_comments`` set and leave
        ``narrative_text`` at its ``None`` default; this fallback makes their
        Narrative_Text equal the legacy comment automatically. The dataclass is
        frozen, so the assignment goes through ``object.__setattr__``.

        An explicitly supplied ``narrative_text`` (what the current-schema
        adapter passes from ``closure_reason``) is left untouched.
        """
        if self.narrative_text is None:
            object.__setattr__(self, "narrative_text", self.closure_comments)


# ===========================================================================
# Schema detection and adaptation (Requirements 1.2, 1.3, 1.6)
#
# Boston publishes 311 data as one export per year and the column layout has
# drifted, so each Source_File must be recognized as one of the supported
# schemas and its raw columns mapped onto the common :class:`SourceRecord`
# before classification. The ``SchemaAdapter`` is the only component aware of
# raw column names; everything downstream reads the common record and is
# therefore schema-agnostic (design "SchemaAdapter").
# ===========================================================================


class UnknownSchemaError(Exception):
    """Raised when a Source_File's header matches no supported Source_Schema.

    Carries the offending ``source_file`` and the file's ``columns`` so
    ``run_filter`` (and the CLI) can surface a descriptive message that names
    exactly which file was unrecognized and what columns it actually had before
    skipping that file and continuing with the rest (R1.3).

    ``source_file`` may be ``None`` when the header is inspected without a known
    originating file name; the message degrades gracefully in that case.
    """

    def __init__(
        self,
        source_file: Optional[str],
        columns: Iterable[str],
    ) -> None:
        self.source_file = source_file
        self.columns = tuple(columns)
        file_label = source_file if source_file is not None else "<unknown file>"
        columns_label = ", ".join(self.columns) if self.columns else "(none)"
        super().__init__(
            f"Unrecognized schema in {file_label}: header columns do not match "
            f"any supported schema. Found columns: {columns_label}"
        )


# The identifying columns that mark a header as a given schema. A header is
# recognized as that schema only when it contains every column in the set, so a
# file that happens to share one column with a schema (but not the identifying
# pair) is not misclassified.
_LEGACY_IDENTIFYING_COLUMNS: frozenset[str] = frozenset(
    {"case_id", "closure_comments"}
)
_CURRENT_IDENTIFYING_COLUMNS: frozenset[str] = frozenset(
    {"case_enquiry_id", "case_title", "type"}
)


class SchemaAdapter:
    """Detects a Source_File's schema and maps its rows onto the common record.

    ``detect`` inspects a header row and returns the matching :class:`Schema`,
    raising :class:`UnknownSchemaError` when it recognizes neither (R1.3).
    ``to_source_record`` maps a raw ``csv.DictReader`` row onto a common
    :class:`SourceRecord` using :data:`COLUMN_MAPS` for the detected schema,
    selecting Narrative_Text from ``closure_comments`` (legacy) or
    ``closure_reason`` (current) and attaching the originating ``source_file``
    and ``row_number`` for logging (R1.2, R1.6).

    The methods are stateless, so the class is equally usable as a namespace of
    static methods or as an instance; no per-file state is held.
    """

    @staticmethod
    def detect(header: Iterable[str], source_file: Optional[str] = None) -> Schema:
        """Return the :class:`Schema` a header row uses.

        A header is :attr:`Schema.LEGACY` when it contains the legacy
        identifying columns (``case_id`` and ``closure_comments``), and
        :attr:`Schema.CURRENT` when it contains the current open-data
        identifying columns (``case_enquiry_id`` and ``case_title``/``type``).
        The legacy check is applied first because the legacy export also carries
        a ``type``-free layout; the two identifying sets are disjoint in
        practice.

        Args:
            header: The CSV header row (column names) to inspect.
            source_file: Optional originating file name, used only to make the
                :class:`UnknownSchemaError` message name the offending file.

        Returns:
            The detected :class:`Schema`.

        Raises:
            UnknownSchemaError: When the header matches neither supported schema
                (R1.3); carries ``source_file`` and the header's columns.
        """
        columns = tuple(header)
        column_set = set(columns)

        if _LEGACY_IDENTIFYING_COLUMNS <= column_set:
            return Schema.LEGACY
        if _CURRENT_IDENTIFYING_COLUMNS <= column_set:
            return Schema.CURRENT

        raise UnknownSchemaError(source_file, columns)

    @staticmethod
    def to_source_record(
        row: dict,
        schema: Schema,
        source_file: str,
        row_number: int,
    ) -> SourceRecord:
        """Map a raw row onto the common :class:`SourceRecord` for ``schema``.

        Pulls each internal field from the source column named in
        ``COLUMN_MAPS[schema]`` (R1.2, R1.6). Narrative_Text is taken from the
        schema's narrative column — ``closure_comments`` for legacy,
        ``closure_reason`` for current — and passed explicitly so the record's
        ``narrative_text`` is populated regardless of schema. ``closure_comments``
        is populated only under the legacy schema; the current open-data schema
        has no such column, so it is left ``None``. A field whose source column
        is absent from ``row`` maps to ``None``.

        Args:
            row: The raw ``csv.DictReader`` row (source-column name to value).
            schema: The detected :class:`Schema` selecting the column map.
            source_file: Originating file name, attached for logging (R1.5).
            row_number: 1-based source row number, attached as the fallback
                identity for logging (R1.5).

        Returns:
            The common :class:`SourceRecord` for the row.
        """
        column_map = COLUMN_MAPS[schema]

        def pull(internal_field: str) -> Optional[str]:
            source_column = column_map.get(internal_field)
            if source_column is None:
                return None
            return row.get(source_column)

        return SourceRecord(
            case_id=pull("case_id"),
            case_topic=pull("case_topic"),
            service_name=pull("service_name"),
            closure_comments=pull("closure_comments"),
            closure_reason=pull("closure_reason"),
            case_status=pull("case_status"),
            longitude=pull("longitude"),
            latitude=pull("latitude"),
            row_number=row_number,
            narrative_text=pull("narrative_text"),
            source_file=source_file,
        )


@dataclass(frozen=True)
class OutputRecord:
    """The projection written to the Output_Dataset (R6.3).

    Exactly the fields in ``OUTPUT_FIELDS`` and nothing more — in particular no
    severity or weighting field (R6.5).
    """

    case_id: Optional[str]
    case_topic: Optional[str]
    service_name: Optional[str]
    closure_comments: Optional[str]
    longitude: Optional[str]
    latitude: Optional[str]

    @classmethod
    def from_source(cls, record: SourceRecord) -> "OutputRecord":
        """Project a ``SourceRecord`` onto the output field set.

        Per R6.3 the output's ``closure_comments`` column carries the record's
        Narrative_Text — the schema-selected resolution narrative. Under the
        current open-data schema there is no source ``closure_comments`` column,
        so the narrative lives in ``narrative_text`` (drawn from
        ``closure_reason``); populating the column from ``record.closure_comments``
        would drop that narrative for 2025+ rows. We therefore source the column
        from ``narrative_text``, falling back to ``closure_comments`` only if the
        narrative is unset. For legacy rows ``narrative_text == closure_comments``
        (SourceRecord.__post_init__ guarantees it), so legacy output is
        unchanged, and the column name/order is untouched so Property 7 holds.
        """
        narrative = (
            record.narrative_text
            if record.narrative_text is not None
            else record.closure_comments
        )
        return cls(
            case_id=record.case_id,
            case_topic=record.case_topic,
            service_name=record.service_name,
            closure_comments=narrative,
            longitude=record.longitude,
            latitude=record.latitude,
        )

    def as_row(self) -> tuple[Optional[str], ...]:
        """Return the field values in ``OUTPUT_FIELDS`` order for CSV writing."""
        return (
            self.case_id,
            self.case_topic,
            self.service_name,
            self.closure_comments,
            self.longitude,
            self.latitude,
        )


@dataclass(frozen=True)
class Classification:
    """The single outcome assigned to a ``SourceRecord``.

    ``outcome`` is either ``"verified"`` (the record is a
    Verified_Obstruction_Record and is written to the output) or ``"excluded"``
    (the record failed a gate). When excluded, ``reason`` names the first gate
    that rejected the record; when verified, ``reason`` is ``None``.

    The single-outcome invariant — every record maps to exactly one
    ``Classification`` — is what makes the reporting counts partition the input
    (design Property 4).
    """

    outcome: str  # "verified" | "excluded"
    reason: Optional[ExclusionReason] = None

    @classmethod
    def verified(cls) -> "Classification":
        """Construct the verified outcome."""
        return cls(outcome="verified", reason=None)

    @classmethod
    def excluded(cls, reason: ExclusionReason) -> "Classification":
        """Construct an excluded outcome with its single reason."""
        return cls(outcome="excluded", reason=reason)

    @property
    def is_verified(self) -> bool:
        return self.outcome == "verified"

    @property
    def is_excluded(self) -> bool:
        return self.outcome == "excluded"

    def __post_init__(self) -> None:
        if self.outcome not in ("verified", "excluded"):
            raise ValueError(
                f"outcome must be 'verified' or 'excluded', got {self.outcome!r}"
            )
        if self.outcome == "verified" and self.reason is not None:
            raise ValueError("verified classification must not carry a reason")
        if self.outcome == "excluded" and self.reason is None:
            raise ValueError("excluded classification must carry a reason")


# ---------------------------------------------------------------------------
# Gate 4 — Valid_Coordinates (Requirement 5)
# ---------------------------------------------------------------------------
def has_valid_coordinates(record: SourceRecord) -> bool:
    """Return ``True`` when the record has Valid_Coordinates (Requirement 5).

    A record has valid coordinates when both ``longitude`` and ``latitude`` are
    present, parse as numeric floats (R5.1), and fall within the inclusive
    Boston-area bounds (R5.2): longitude in ``[LONGITUDE_MIN, LONGITUDE_MAX]``
    and latitude in ``[LATITUDE_MIN, LATITUDE_MAX]``.

    Missing, empty, non-numeric, or out-of-range coordinates make a record
    invalid (R5.4). This is a normal classification result, never an error:
    the function returns ``False`` rather than raising so a confirmed
    obstruction with bad coordinates is dropped and logged cleanly.
    """
    longitude = _parse_coordinate(record.longitude)
    if longitude is None:
        return False
    latitude = _parse_coordinate(record.latitude)
    if latitude is None:
        return False

    return (
        LONGITUDE_MIN <= longitude <= LONGITUDE_MAX
        and LATITUDE_MIN <= latitude <= LATITUDE_MAX
    )


def _parse_coordinate(raw: Optional[str]) -> Optional[float]:
    """Parse a raw coordinate string to ``float``, or ``None`` if not numeric.

    Treats ``None``, empty/whitespace-only strings, and any value that does not
    parse as a finite float (including ``NaN`` and infinities) as absent/
    non-numeric, so the coordinate gate can reject them without raising.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        # Reject NaN (self-inequality) and infinities as non-mappable.
        return None
    return value


# ===========================================================================
# Gate 1 — Bike_Lane_Context detection (Requirement 2)
#
# The source 311 feed has no "bike lane obstruction" category, so candidate
# records are recognized from free text. Gate 1 is the cheapest, broadest
# filter: a record is a candidate only when a bike-lane phrase appears as a
# whole word sequence in one of its text fields.
# ===========================================================================

# ---------------------------------------------------------------------------
# Bike-lane context phrases (Requirement 2.1)
#
# Whole-phrase, case-insensitive matches of these sequences in any of the
# evaluated text fields mark a record as a bike-lane-obstruction candidate. The
# vocabulary lives in a named module-level constant so it can be reviewed and
# extended without touching the matching control flow.
# ---------------------------------------------------------------------------
BIKE_LANE_CONTEXT_PHRASES: tuple[str, ...] = (
    "bike lane",
    "bike lanes",
    "bike line",
)

# Text fields scanned for bike-lane context, in evaluation order (R2.1, R2.3).
# ``narrative_text`` is the schema-selected Narrative_Text (``closure_comments``
# under the legacy schema, ``closure_reason`` under the current open-data
# schema), so Gate 1 scans the correct narrative regardless of schema.
BIKE_LANE_CONTEXT_FIELDS: tuple[str, ...] = (
    "case_topic",
    "service_name",
    "narrative_text",
)

# ---------------------------------------------------------------------------
# Whole-phrase matcher (Requirement 2.1)
#
# After normalization the field text is lowercase with single-space-separated
# tokens, so each phrase is a run of words separated by single spaces. Word
# boundaries (\b) around the phrase ensure a match on whole word sequences
# regardless of surrounding punctuation or whitespace (which normalization has
# already collapsed), while preventing false positives from larger tokens
# (e.g. "bike laned" would not match "bike lane"). Longer phrases are listed
# first in the alternation so, e.g., "bike lanes" is preferred over "bike lane";
# this does not affect the boolean result but keeps the intent clear.
# ---------------------------------------------------------------------------
_BIKE_LANE_CONTEXT_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(phrase)
        for phrase in sorted(BIKE_LANE_CONTEXT_PHRASES, key=len, reverse=True)
    )
    + r")\b"
)


def normalize_text(value: Optional[str]) -> str:
    """Normalize a free-text field for case-insensitive whole-phrase matching.

    Lowercases the text and collapses every run of whitespace (spaces, tabs,
    newlines) to a single space, then strips leading/trailing whitespace. This
    makes matching robust to the varied casing and spacing seen in the real
    ``closure_comments`` text (R2.1).

    Absent fields are represented by ``None`` and normalize to the empty string
    so callers can treat missing, empty, and whitespace-only fields uniformly as
    "no match" (R2.2).
    """
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()


def _field_has_bike_lane_context(value: Optional[str]) -> bool:
    """Return True when a single field's text contains bike-lane context.

    Absent/empty/whitespace-only fields normalize to the empty string and never
    match (R2.2).
    """
    normalized = normalize_text(value)
    if not normalized:
        return False
    return _BIKE_LANE_CONTEXT_PATTERN.search(normalized) is not None


def has_bike_lane_context(record: SourceRecord) -> bool:
    """Gate 1 — does the record reference a bike lane? (Requirement 2)

    Performs a case-insensitive, whole-phrase match of the
    ``BIKE_LANE_CONTEXT_PHRASES`` against the record's ``case_topic``,
    ``service_name``, and ``narrative_text`` fields (R2.1). A match in **any**
    field makes the record a candidate (R2.3). ``narrative_text`` is the
    schema-selected narrative, so Gate 1 is schema-agnostic.

    Absent, empty, or null fields contribute no match; evaluation continues with
    the remaining fields (R2.2). When no field matches, the record has no
    Bike_Lane_Context and will be excluded as ``no_bike_lane_context`` (R2.4).
    """
    for field_name in BIKE_LANE_CONTEXT_FIELDS:
        if _field_has_bike_lane_context(getattr(record, field_name)):
            return True
    return False


# ---------------------------------------------------------------------------
# Shared text normalization
#
# Normalization lowercases the field and collapses runs of whitespace to a
# single space so that casing, punctuation-adjacent spacing, and irregular
# whitespace do not defeat phrase matching (design "Keyword/Phrase
# Classification Strategy"). Absent fields normalize to the empty string.
#
# This is the shared helper reused by every text gate (Gate 1 context, Gate 2
# unrelated, Gate 3 verification). If a parallel task has already defined an
# equivalent ``normalize`` in this module, the two are interchangeable.
# ---------------------------------------------------------------------------
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize(text: Optional[str]) -> str:
    """Lowercase ``text`` and collapse whitespace runs to single spaces.

    ``None`` and empty/whitespace-only inputs normalize to the empty string so
    callers can treat missing fields as "no match" without special-casing.
    """
    if not text:
        return ""
    return _WHITESPACE_RUN.sub(" ", text).strip().lower()


# ---------------------------------------------------------------------------
# Gate 2 — Unrelated_Record phrase set (Requirement 3.1)
#
# Candidates that reached this gate already carry Bike_Lane_Context (Gate 1),
# yet some of them are really about a bicycle *object* — a stolen or abandoned
# bike, or a bike rack — rather than an obstruction of the lane itself. These
# phrases, matched (after normalization) against the topic/service/comment
# text, flag such records so they are excluded as ``unrelated_record``.
#
# Kept as a named module-level constant so the vocabulary can be reviewed and
# extended without touching control flow.
# ---------------------------------------------------------------------------
UNRELATED_PHRASES: tuple[str, ...] = (
    "stolen bike",
    "abandoned bike",
    "abandoned bicycle",
    "bike rack",
    "bicycle rack",
)


def is_unrelated(record: SourceRecord) -> bool:
    """Return ``True`` when the record is an Unrelated_Record (R3.1).

    A record is unrelated when its subject is a bicycle object — a stolen or
    abandoned bicycle, or a bike rack — rather than an obstruction of a bike
    lane. The check normalizes ``case_topic``, ``service_name``, and
    ``narrative_text`` with the shared :func:`normalize` helper and looks for
    any phrase in :data:`UNRELATED_PHRASES`. ``narrative_text`` is the
    schema-selected narrative, so the gate is schema-agnostic. Absent/empty
    fields contribute no match. A hit in any one field marks the whole record
    unrelated.
    """
    haystack = " ".join(
        normalize(field)
        for field in (
            record.case_topic,
            record.service_name,
            record.narrative_text,
        )
    )
    return any(phrase in haystack for phrase in UNRELATED_PHRASES)


# ---------------------------------------------------------------------------
# Gate 3 — Verification signal sets (Requirement 4)
#
# These phrase sets drive the verification gate. They live at module level so
# the vocabulary can be reviewed and extended without touching control flow.
# All phrases are written in normalized form (lowercase, single-spaced) so they
# can be matched directly against ``normalize(...)`` output.
# ---------------------------------------------------------------------------

# Negative / no-obstruction signals (R4.1, R4.2). When any of these appears in
# the closure comment (or secondarily the closure reason), the record asserts
# that no standing bike-lane obstruction existed, so the outcome is UNVERIFIED
# regardless of any positive signal that may also be present.
NEGATIVE_VERIFICATION_SIGNALS: tuple[str, ...] = (
    "no violation found",
    "no violation",
    "unable to locate",
    "no issue found",
    "no issue was found",
    "no bike was found",
    "no bike found",
    "bike lane is clear",
    "bike lane clear",
    "bike is lift up",
    "bike is lifted up",
    "lifted away",
    "for information on bike lanes",
    "for information on bike lane",
    "remains unimpeded",
    "no further pruning needed",
    "no work needed",
    "will be serviced",
    "going to be serviced",
)

# Positive / confirming signals (R4.3). Each asserts that a physical bike-lane
# obstruction occurred, was ticketed, or was addressed/removed. These are only
# honored when no negative signal overrides them.
POSITIVE_VERIFICATION_SIGNALS: tuple[str, ...] = (
    "removed barrels from bike lane",
    "removed from bike lane",
    "removed from the bike lane",
    "removed the bike lane",
    "glass removed from bike lane",
    "mattress was removed from the bike lane",
    "blocking the bike lane",
    "blocking bike lane",
    "bike lane removed",
    "bike lane blocked",
    "blocked the bike lane",
    "ticketed for bike lane",
    "ticket for bike lane",
    "pothole in bike lane",
    "pothole in the bike lane",
    "bad pothole in bike lane",
    "cleared from",
    "picked up out of bike lane",
    "picked up out of the bike lane",
    "swept",
    "mechanically swept",
    "pruned back",
)


def verification_outcome(record: SourceRecord) -> Verification:
    """Classify a candidate's verification outcome (Requirement 4).

    Applies the curated signal sets primarily to ``narrative_text`` (the
    schema-selected narrative) and secondarily to ``closure_reason``. Returns
    ``Verification.CONFIRMED`` only
    when a positive/confirming signal is present and no negative/no-obstruction
    signal overrides it. Negative signals take precedence (R4.1, R4.2), and any
    comment that matches neither set defaults to ``Verification.UNVERIFIED``
    (R4.4) — the conservative choice for a safety-first consumer.

    Args:
        record: The candidate source record to evaluate.

    Returns:
        ``Verification.CONFIRMED`` or ``Verification.UNVERIFIED``.
    """
    comment = normalize(record.narrative_text)
    reason = normalize(record.closure_reason)
    combined = f"{comment} {reason}".strip()

    # Negative signals take precedence: if any no-obstruction signal is present,
    # the record is unverified even when a positive phrase also appears (R4.2).
    for signal in NEGATIVE_VERIFICATION_SIGNALS:
        if signal in combined:
            return Verification.UNVERIFIED

    # With no overriding negative signal, a positive confirming signal yields
    # CONFIRMED (R4.3).
    for signal in POSITIVE_VERIFICATION_SIGNALS:
        if signal in combined:
            return Verification.CONFIRMED

    # Neither positive nor negative signal matched: default to UNVERIFIED (R4.4).
    return Verification.UNVERIFIED


# ===========================================================================
# Gate orchestrator — classify (Requirements 2.4, 3.1, 4.1–4.4, 5.3, 5.4)
#
# ``classify`` runs the four gates in requirement order (R2 -> R3 -> R4 -> R5)
# and collapses them into a single :class:`Classification`. The ordering is the
# one established in the design's "Gate Ordering Rationale": each gate assumes
# the previous one passed, the first gate to reject owns the outcome, and a
# record that clears every gate is ``verified``.
# ===========================================================================
def classify(record: SourceRecord) -> Classification:
    """Assign a ``SourceRecord`` its single :class:`Classification`.

    Runs the four classification gates in requirement order and returns the
    outcome of the first gate that rejects the record, or ``verified`` when the
    record clears them all:

    1. **Bike_Lane_Context (R2)** — no bike-lane reference →
       ``NO_BIKE_LANE_CONTEXT`` (R2.4).
    2. **Unrelated (R3)** — a bicycle-object record (stolen/abandoned bike, bike
       rack) rather than a lane obstruction → ``UNRELATED_RECORD`` (R3.1).
    3. **Verification (R4)** — closure comment does not confirm an obstruction
       (negative, informational, or ambiguous) → ``UNVERIFIED_RECORD``
       (R4.1, R4.2, R4.4). A confirmed obstruction (R4.3) passes.
    4. **Valid_Coordinates (R5)** — missing, non-numeric, or out-of-bounds
       coordinates → ``INVALID_COORDINATES`` (R5.4).

    A record that clears all four gates is a Verified_Obstruction_Record and is
    returned as ``verified`` (R5.3).

    The gates run in this fixed order because each one assumes the previous
    passed, and the **first** gate to reject owns the record's single reason
    (design "Gate Ordering Rationale"). This guarantees the single-outcome
    invariant: every record maps to exactly one :class:`Classification`, which
    is what makes the reporting counts partition the input (design Property 4).

    Args:
        record: The source record to classify.

    Returns:
        A :class:`Classification` that is either ``verified`` or ``excluded``
        with exactly one :class:`ExclusionReason`.
    """
    # Gate 1 — Bike_Lane_Context (R2): the cheapest, broadest filter, run first.
    if not has_bike_lane_context(record):
        return Classification.excluded(ExclusionReason.NO_BIKE_LANE_CONTEXT)

    # Gate 2 — Unrelated_Record (R3): drop bicycle-object records before the
    # verification keyword logic can be misled by their comments.
    if is_unrelated(record):
        return Classification.excluded(ExclusionReason.UNRELATED_RECORD)

    # Gate 3 — Verification (R4): keep only comments that confirm an obstruction;
    # negative/informational/ambiguous comments default to unverified.
    if verification_outcome(record) != Verification.CONFIRMED:
        return Classification.excluded(ExclusionReason.UNVERIFIED_RECORD)

    # Gate 4 — Valid_Coordinates (R5): run last so a confirmed obstruction with
    # bad coordinates is attributed here rather than hidden behind an earlier
    # gate.
    if not has_valid_coordinates(record):
        return Classification.excluded(ExclusionReason.INVALID_COORDINATES)

    # Cleared every gate: a Verified_Obstruction_Record (R5.3).
    return Classification.verified()


@dataclass(frozen=True)
class ExclusionEntry:
    """A single per-record exclusion recorded during a run.

    ``identity`` is the record's ``case_id`` when present, falling back to a
    ``row <n>`` label derived from ``row_number`` when ``case_id`` is missing or
    blank (R1.4). ``reason`` is the single :class:`ExclusionReason` for the
    first gate the record failed. ``row_number`` is retained so the log can
    always point back at the exact source row even when identities collide or
    are absent.

    ``source_file`` names the originating Source_File so an exclusion can be
    attributed to the right file in a multi-file run (R1.5). It defaults to
    ``None`` for single-file / legacy callers that never attach a file name.

    The source file is carried as an attribute only: :meth:`format_line` keeps
    its original three-column ``row <n>`` / ``identity`` / ``reason`` shape
    unchanged so the ``.log`` mirror stays byte-stable for every caller
    (existing tests parse exactly those three tab-separated columns). Per-file
    attribution is surfaced through the attribute for reporting, not by widening
    the log line.
    """

    identity: str
    reason: ExclusionReason
    row_number: int
    source_file: Optional[str] = None

    def format_line(self) -> str:
        """Render a stable one-line representation for the ``.log`` mirror.

        Deliberately emits only the original three tab-separated columns —
        ``row <n>`` / ``identity`` / ``reason`` — regardless of whether a
        ``source_file`` is attached, so the mirror's format is identical across
        single-file and multi-file runs. Per-file attribution lives on the
        :attr:`source_file` attribute (R1.5), which the reporter reads directly.
        """
        return f"row {self.row_number}\t{self.identity}\t{self.reason.value}"


class ProcessingLog:
    """Accumulates per-record exclusion entries within a single run.

    Satisfies the logging obligations of R1.4 (parse errors), R3.2 (unrelated
    records), R4.5 (unverified records), and R5.4 (invalid coordinates): every
    excluded record is recorded with its ``case_id`` (or row-number fallback)
    and the single :class:`ExclusionReason` for the gate it failed.

    Entries are kept in an in-memory list in the order they were recorded. When
    constructed with a ``log_path`` the log also mirrors each entry to that
    ``.log`` file as it is recorded, so a crash mid-run still leaves a partial
    record on disk. Mirroring is optional; with no path the log is purely
    in-memory.
    """

    def __init__(self, log_path: Optional[str] = None) -> None:
        self._entries: list[ExclusionEntry] = []
        self._log_path = log_path
        self._file = None
        if log_path is not None:
            # Open for the lifetime of the run so entries stream out as they are
            # recorded rather than only at the end. Newline-normalized UTF-8 so
            # the mirror is portable off the Windows host.
            self._file = open(log_path, "w", encoding="utf-8", newline="\n")

    def record(self, record: "SourceRecord", reason: ExclusionReason) -> ExclusionEntry:
        """Record ``record``'s exclusion under ``reason`` and return the entry.

        Uses ``case_id`` as the identity when it carries a non-blank value,
        otherwise falls back to a ``row <n>`` label built from the record's
        ``row_number`` (R1.4). The entry is appended to the in-memory list and,
        when a ``.log`` mirror is configured, flushed to disk immediately.
        """
        identity = self._identity_for(record)
        entry = ExclusionEntry(
            identity=identity,
            reason=reason,
            row_number=record.row_number,
            # Attribute the exclusion to its originating file for multi-file
            # reporting (R1.5); None for single-file / legacy records.
            source_file=record.source_file,
        )
        self._entries.append(entry)
        if self._file is not None:
            self._file.write(entry.format_line() + "\n")
            self._file.flush()
        return entry

    @staticmethod
    def _identity_for(record: "SourceRecord") -> str:
        """Return ``case_id`` if present and non-blank, else a row-number label."""
        case_id = record.case_id
        if case_id is not None and case_id.strip():
            return case_id
        return f"row {record.row_number}"

    @property
    def entries(self) -> tuple[ExclusionEntry, ...]:
        """An immutable snapshot of the recorded entries, in record order."""
        return tuple(self._entries)

    def counts_by_reason(self) -> dict[ExclusionReason, int]:
        """Return the number of recorded entries for each seen reason."""
        counts: dict[ExclusionReason, int] = {}
        for entry in self._entries:
            counts[entry.reason] = counts.get(entry.reason, 0) + 1
        return counts

    def __len__(self) -> int:
        return len(self._entries)

    def close(self) -> None:
        """Close the ``.log`` mirror if one was opened. Safe to call repeatedly."""
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "ProcessingLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

# ===========================================================================
# Output writing (Requirement 6)
#
# The OutputWriter serializes Verified_Obstruction_Records to the single clean
# Output_Dataset CSV consumed by the Routing_Decision_Layer. It uses the
# standard-library ``csv`` module so embedded commas, quotes, and newlines in
# ``closure_comments`` are quoted correctly on the way out.
# ===========================================================================

import csv


class OutputWriter:
    """Writes Verified_Obstruction_Records to the Output_Dataset CSV (R6).

    The output has exactly one header row naming the :data:`OUTPUT_FIELDS`
    (R6.4) followed by one data row per verified record, each carrying only the
    ``case_id``, ``case_topic``, ``service_name``, ``closure_comments``,
    ``longitude``, and ``latitude`` fields in that order (R6.3). There is
    deliberately no severity or weighting column (R6.5): every verified
    obstruction is an equal-weight safety signal.
    """

    def write(self, records: "Iterable[SourceRecord]", path: str) -> int:
        """Write ``records`` to the CSV at ``path`` and return the row count.

        Writes a single header row naming :data:`OUTPUT_FIELDS` (R6.4), then one
        row per record projected onto the output field set via
        :meth:`OutputRecord.from_source` (R6.3). ``None`` values are written as
        empty fields. The Boston 311 ``closure_comments`` text frequently
        contains commas and embedded newlines, so the ``csv.writer`` handles
        quoting; ``newline=""`` on the file keeps the writer in charge of line
        endings and avoids blank lines on Windows.

        Args:
            records: The Verified_Obstruction_Records to serialize. The caller
                is responsible for passing only verified records (R6.2); this
                writer does not re-classify.
            path: Destination path for the single Output_Dataset file (R6.1).

        Returns:
            The number of data rows written (excluding the header row).
        """
        rows_written = 0
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(OUTPUT_FIELDS)
            for record in records:
                output_record = OutputRecord.from_source(record)
                writer.writerow(
                    "" if value is None else value
                    for value in output_record.as_row()
                )
                rows_written += 1
        return rows_written


# ===========================================================================
# Reporter — end-of-run summary (Requirement 7)
#
# After the pipeline finishes, the Reporter produces the human-readable summary
# the routing-app developer uses to confirm the filter behaved correctly:
#   - the total count of Source_Records read (R7.1),
#   - the count of Verified_Obstruction_Records written to the output (R7.2),
#   - the counts of excluded records grouped by exclusion reason (R7.3),
#     covering no_bike_lane_context, unrelated_record, unverified_record,
#     invalid_coordinates, and parse_error.
#
# The Reporter is a pure summarizer: it reads totals and the ProcessingLog's
# per-reason counts and formats them. It performs no classification and no file
# I/O of its own, which keeps it trivially testable and keeps the single-outcome
# invariant (every record is either verified or excluded under exactly one
# reason) the sole source of truth for the arithmetic.
# ===========================================================================

# The exclusion reasons reported in the summary, in a stable display order.
# Every bucket is always shown — even at zero — so the report shape is constant
# across runs and a reason with no hits reads as an explicit "0" rather than a
# silent omission (R7.3).
REPORT_REASON_ORDER: tuple[ExclusionReason, ...] = (
    ExclusionReason.NO_BIKE_LANE_CONTEXT,
    ExclusionReason.UNRELATED_RECORD,
    ExclusionReason.UNVERIFIED_RECORD,
    ExclusionReason.INVALID_COORDINATES,
    ExclusionReason.PARSE_ERROR,
)


@dataclass(frozen=True)
class RunSummary:
    """The immutable, structured result of a filtering run (Requirement 7).

    ``total_read`` is the number of Source_Records read from the input (R7.1).
    ``total_written`` is the number of Verified_Obstruction_Records written to
    the Output_Dataset (R7.2). ``excluded_by_reason`` maps every reporting
    reason to its count (R7.3); the mapping always contains all buckets from
    :data:`REPORT_REASON_ORDER`, defaulting absent reasons to zero.

    The single-outcome invariant means the counts partition the input: the
    verified count plus the sum of all exclusion counts equals ``total_read``.
    :meth:`is_balanced` checks exactly that, and :attr:`total_excluded` exposes
    the excluded subtotal.

    ``verified_by_file`` maps each originating Source_File to the number of
    Verified_Obstruction_Records it contributed (R7.4). It is only meaningful
    when more than one Source_File is processed; for a single-file run it is an
    empty (or single-entry) mapping and the per-file section of the report is
    suppressed, keeping the report shape identical to earlier single-file runs.
    """

    total_read: int
    total_written: int
    excluded_by_reason: dict[ExclusionReason, int]
    verified_by_file: dict[str, int] = field(default_factory=dict)

    @property
    def total_excluded(self) -> int:
        """Sum of all per-reason exclusion counts."""
        return sum(self.excluded_by_reason.values())

    def is_balanced(self) -> bool:
        """Return ``True`` when written + excluded equals total read (Property 4)."""
        return self.total_written + self.total_excluded == self.total_read

    def format_report(self) -> str:
        """Render a stable, human-readable multi-line summary.

        Lists the total read, the verified total written, then one line per
        reporting reason in :data:`REPORT_REASON_ORDER`, every bucket shown even
        when zero (R7.3).
        """
        lines = [
            "Bike Lane Obstruction Filter — run summary",
            f"  Total records read:    {self.total_read}",
            f"  Verified records written: {self.total_written}",
            "  Excluded by reason:",
        ]
        for reason in REPORT_REASON_ORDER:
            count = self.excluded_by_reason.get(reason, 0)
            lines.append(f"    {reason.value}: {count}")
        # Per-file verified breakdown (R7.4): only shown when more than one
        # Source_File contributed, so single-file runs keep their exact prior
        # report shape. Files are listed in a stable (sorted) order so the
        # section is deterministic across runs.
        if len(self.verified_by_file) > 1:
            lines.append("  Verified records by source file:")
            for source_file in sorted(self.verified_by_file):
                lines.append(
                    f"    {source_file}: {self.verified_by_file[source_file]}"
                )
        return "\n".join(lines)


class Reporter:
    """Produces the end-of-run summary from run totals and the ProcessingLog.

    The Reporter does not classify records or touch the filesystem; it reads the
    totals the pipeline accumulated and the per-reason counts the
    :class:`ProcessingLog` recorded, and assembles them into a
    :class:`RunSummary` (and, via that, a printable report) satisfying
    Requirement 7:

    * total Source_Records read (R7.1),
    * Verified_Obstruction_Records written (R7.2),
    * excluded counts grouped by reason, every bucket present (R7.3).
    """

    def summarize(
        self,
        total_read: int,
        total_written: int,
        log: "ProcessingLog",
        verified_by_file: Optional[dict[str, int]] = None,
    ) -> RunSummary:
        """Build a :class:`RunSummary` from run totals and the processing log.

        Args:
            total_read: Number of Source_Records read from the input (R7.1).
            total_written: Number of Verified_Obstruction_Records written to the
                Output_Dataset (R7.2).
            log: The run's :class:`ProcessingLog`, whose per-reason counts supply
                the excluded totals (R7.3).
            verified_by_file: Optional mapping of each originating Source_File to
                the number of Verified_Obstruction_Records it contributed (R7.4).
                ``None`` (the backward-compatible default used by existing
                single-file / positional call sites) is treated as an empty
                mapping, which suppresses the per-file report section.

        Returns:
            A :class:`RunSummary` whose ``excluded_by_reason`` contains every
            bucket in :data:`REPORT_REASON_ORDER` (absent reasons default to 0)
            and whose ``verified_by_file`` carries the per-file verified counts
            (empty when not supplied).
        """
        logged = log.counts_by_reason()
        excluded_by_reason = {
            reason: logged.get(reason, 0) for reason in REPORT_REASON_ORDER
        }
        return RunSummary(
            total_read=total_read,
            total_written=total_written,
            excluded_by_reason=excluded_by_reason,
            verified_by_file=dict(verified_by_file or {}),
        )

    def report(
        self,
        total_read: int,
        total_written: int,
        log: "ProcessingLog",
    ) -> str:
        """Return the formatted summary string for the given run totals."""
        return self.summarize(total_read, total_written, log).format_report()


# ===========================================================================
# Input reading (Requirement 1)
# ===========================================================================


class SourceFileNotFoundError(FileNotFoundError):
    """Raised when the Source_Dataset is not found at the expected path (R1.3).

    Carries the attempted **absolute** path so ``run_filter`` can surface a
    descriptive, actionable message that names exactly where the Filter looked
    before stopping. Subclasses the built-in :class:`FileNotFoundError` so
    callers that already handle missing files keep working, while callers that
    want the richer behavior can catch this type specifically and read
    :attr:`path`.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Source dataset not found at: {path}")


# The source columns the pipeline maps onto ``SourceRecord``. Located by name in
# the header row (R1.2); any other columns in the file are read but ignored.
#
# These are the LEGACY schema's source columns; kept for backward compatibility
# with the original single-file reader and its tests. Multi-file reading now
# routes every file — legacy or current — through the :class:`SchemaAdapter`,
# so the "required columns" a row is validated against are the detected
# schema's, taken from :data:`SCHEMA_REQUIRED_COLUMNS` below rather than this
# fixed tuple.
_SOURCE_FIELDS: tuple[str, ...] = (
    "case_id",
    "case_topic",
    "service_name",
    "closure_comments",
    "closure_reason",
    "case_status",
    "longitude",
    "latitude",
)


# The source columns that must be present (and non-``None``) on a data row for
# it to map cleanly onto the common :class:`SourceRecord` under each schema
# (R1.5). A row missing any of its schema's required columns — or carrying
# overflow under the ``None`` key — is surfaced as a :class:`ParseError` rather
# than a partially-populated record, exactly as the original legacy reader did.
#
# Legacy requires the full legacy column set (unchanged from the original
# ``_SOURCE_FIELDS`` behavior). Current requires the renamed identity/topic/
# service columns plus the shared narrative/status/coordinate columns; it has
# no ``closure_comments`` column, so that is deliberately absent here.
SCHEMA_REQUIRED_COLUMNS: dict[Schema, tuple[str, ...]] = {
    Schema.LEGACY: (
        "case_id",
        "case_topic",
        "service_name",
        "closure_comments",
        "closure_reason",
        "case_status",
        "longitude",
        "latitude",
    ),
    Schema.CURRENT: (
        "case_enquiry_id",
        "case_title",
        "type",
        "closure_reason",
        "case_status",
        "longitude",
        "latitude",
    ),
}


@dataclass(frozen=True)
class ParseError:
    """A marker yielded for a source row that cannot be mapped to the fields.

    ``CsvReader`` yields one of these instead of a :class:`SourceRecord` when a
    data row does not conform to the header (for example a row with the wrong
    number of fields, so ``csv`` cannot align values to column names). The
    pipeline logs it under ``parse_error`` and excludes it, then continues with
    the next row — one bad row never aborts the run (R1.4).

    ``identity`` is the row's ``case_id`` when it could be recovered, otherwise
    a ``row <n>`` label derived from ``row_number`` (R1.4). ``row_number`` is
    always retained so the log can point back at the exact source row even when
    ``case_id`` is unreadable. ``source_file`` names the originating Source_File
    so a bad row can be attributed to the right file across a multi-file run
    (R1.5); it defaults to ``None`` for the single-file / legacy callers that do
    not set it.
    """

    row_number: int
    identity: str
    detail: Optional[str] = None
    source_file: Optional[str] = None


# A reader yields either a parsed record or a parse-error marker, one per row.
ReadResult = Union[SourceRecord, ParseError]


@dataclass(frozen=True)
class SkippedFile:
    """Record of a Source_File skipped because its schema was unrecognized (R1.3).

    When :meth:`CsvReader.read_many` encounters a file whose header matches no
    supported schema, it does **not** abort the whole run: it skips that one
    file and keeps reading the rest. The skip is surfaced two ways — the
    underlying :class:`UnknownSchemaError` is passed to the reader's
    ``on_file_error`` callback as it happens, and a :class:`SkippedFile` is
    appended to :attr:`CsvReader.skipped_files` so a caller (``run_filter`` /
    the CLI) can report every skipped file afterward (R1.3).

    ``source_file`` is the originating path and ``error`` is the captured
    :class:`UnknownSchemaError`, whose message already names the file and its
    unrecognized columns.
    """

    source_file: str
    error: UnknownSchemaError


class CsvReader:
    """Reads and parses one or more Source_Files, streaming one result per row.

    Each file is opened with :class:`csv.DictReader`, its schema detected from
    the header via :class:`SchemaAdapter`, and every data row mapped onto the
    common :class:`SourceRecord` by the adapter (R1.2, R1.6) — so both the
    legacy and the current open-data schema flow through the same path and
    ``narrative_text`` is drawn from the schema-appropriate column. Rows are
    yielded lazily, so even the ~152 MB current export is never fully
    materialized in memory (R1.1).

    For a well-formed data row the reader yields a :class:`SourceRecord` (with
    its originating ``source_file`` attached). For a row that cannot be mapped to
    the detected schema's required columns it yields a :class:`ParseError`
    marker carrying the ``case_id`` or row number **and** the originating file,
    so the pipeline can log and skip it without aborting (R1.5).

    File-level outcomes:

    * A missing file raises :class:`SourceFileNotFoundError` with the attempted
      absolute path (R1.4).
    * A file whose header matches no supported schema is **skipped**, not fatal:
      the reader records a :class:`SkippedFile` on :attr:`skipped_files`,
      invokes the optional ``on_file_error`` callback, and continues with the
      remaining files (R1.3).

    The single-file :meth:`read_records` is retained for backward compatibility
    and simply delegates to :meth:`read_many`.
    """

    def __init__(self) -> None:
        # Files skipped during the most recent read because their schema was
        # unrecognized (R1.3). Reset at the start of each read_many call so the
        # list reflects exactly one run. A caller inspects this after draining
        # the iterator to report which files were skipped.
        self.skipped_files: list[SkippedFile] = []

    def read_records(self, path: str) -> Iterator[ReadResult]:
        """Yield results for a single file (backward-compatible single-path API).

        Retained so existing callers and tests that pass one path keep working
        unchanged; delegates to :meth:`read_many` with a one-element list. Still
        raises :class:`SourceFileNotFoundError` on a missing file (R1.4) and
        yields :class:`ParseError` markers for unmappable rows (R1.5).

        Args:
            path: Path to a single Source_File CSV.

        Yields:
            A :class:`SourceRecord` or :class:`ParseError` per data row.
        """
        yield from self.read_many([path])

    def read_many(
        self,
        paths: Iterable[str],
        on_file_error: Optional["Callable[[SkippedFile], None]"] = None,
    ) -> Iterator[ReadResult]:
        """Yield results across every file in ``paths`` (R1.2, R1.3, R1.6).

        Each file is read in order; its records are concatenated into one
        stream, so the caller sees a single combined Source_Dataset (R1.6). Row
        numbering restarts at 2 per file (the header is row 1), matching the
        1-based line a user sees when opening that file.

        Args:
            paths: An iterable of Source_File paths. A single path should be
                wrapped in a list (``read_records`` does this for the
                single-path API).
            on_file_error: Optional callback invoked with a :class:`SkippedFile`
                the moment a file is skipped for an unrecognized schema (R1.3).
                Whether or not it is supplied, every skip is also appended to
                :attr:`skipped_files` for after-the-fact reporting.

        Raises:
            SourceFileNotFoundError: If any file in ``paths`` is missing (R1.4),
                carrying that file's attempted absolute path. Raised when the
                reader reaches that file in iteration order.

        Yields:
            A :class:`SourceRecord` for each well-formed data row (with
            ``source_file`` set) or a :class:`ParseError` marker for each
            unmappable row (R1.5). Rows stream lazily; an unrecognized-schema
            file contributes nothing but does not stop the stream (R1.3).
        """
        # Fresh per call so skipped_files reflects exactly this run.
        self.skipped_files = []
        # Thread the callback onto instance state for the duration of the call
        # so the ``_read_one`` generator helper can reach it (see note below).
        self._on_file_error = on_file_error

        try:
            for path in paths:
                absolute_path = os.path.abspath(path)
                if not os.path.isfile(path):
                    raise SourceFileNotFoundError(absolute_path)

                yield from self._read_one(path)
        finally:
            # Clear the per-call callback so it never leaks into a later run.
            self._on_file_error = None

    def _read_one(self, path: str) -> Iterator[ReadResult]:
        """Yield results for a single already-existing file, routed by schema.

        Detects the file's schema from its header (R1.2). If the header matches
        no supported schema the file is skipped: an :class:`UnknownSchemaError`
        is captured into a :class:`SkippedFile`, recorded on
        :attr:`skipped_files`, passed to the ``on_file_error`` callback when the
        enclosing :meth:`read_many` was given one, and the generator returns
        without yielding — the rest of the files keep processing (R1.3).
        """
        # ``newline=""`` lets the csv module handle newlines embedded in quoted
        # fields (the narrative text contains them in the real data). UTF-8 with
        # replacement keeps one malformed byte from aborting the whole run.
        with open(path, "r", encoding="utf-8", newline="", errors="replace") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []

            try:
                schema = SchemaAdapter.detect(header, source_file=path)
            except UnknownSchemaError as exc:
                # Unrecognized schema: skip this file, record it, and continue
                # with the remaining files (R1.3). Nothing is yielded here.
                skipped = SkippedFile(source_file=path, error=exc)
                self.skipped_files.append(skipped)
                if self._on_file_error is not None:
                    self._on_file_error(skipped)
                return

            required = SCHEMA_REQUIRED_COLUMNS[schema]
            # Enumerate so the first data row is row 2 (the header is row 1),
            # matching the 1-based line numbering a user would see in the file.
            for row_number, row in enumerate(reader, start=2):
                yield self._map_row(row, row_number, schema, required, path)

    # ``_on_file_error`` is threaded through as instance state for the duration
    # of a ``read_many`` call so ``_read_one`` (a plain generator helper) can
    # reach it without re-plumbing the callback through every yield. It is set
    # in ``read_many`` and defaults to ``None`` otherwise.
    _on_file_error: "Optional[Callable[[SkippedFile], None]]" = None

    @staticmethod
    def _map_row(
        row: dict,
        row_number: int,
        schema: Schema,
        required: tuple[str, ...],
        source_file: str,
    ) -> ReadResult:
        """Map one ``DictReader`` row to a record, or a parse-error marker.

        A row is unmappable when ``DictReader`` could not align it to the header:
        a short row leaves the detected schema's required columns missing, and a
        long row collects the overflow under the ``None`` key (``restkey``).
        Either case yields a :class:`ParseError` carrying the originating
        ``source_file`` rather than a partially-populated record (R1.5).

        A well-formed row is mapped onto the common :class:`SourceRecord` by
        :meth:`SchemaAdapter.to_source_record`, which draws ``narrative_text``
        from the schema-appropriate column and attaches ``source_file`` /
        ``row_number`` (R1.2, R1.6).
        """
        column_map = COLUMN_MAPS[schema]
        # Recover a best-effort identity up front from the schema's id column so
        # even a malformed row can be attributed to its case_id (or row number).
        id_column = column_map["case_id"]
        raw_case_id = row.get(id_column)
        case_id = raw_case_id.strip() if isinstance(raw_case_id, str) else None
        identity = case_id if case_id else f"row {row_number}"

        # Overflow values land under the None key when a row has more fields than
        # the header: the row does not conform to the expected schema.
        if None in row and row[None]:
            return ParseError(
                row_number=row_number,
                identity=identity,
                detail="row has more fields than the header",
                source_file=source_file,
            )

        # Any required column (for the detected schema) entirely absent (short
        # row / missing value DictReader could not fill) means the row cannot be
        # mapped.
        missing = [field for field in required if row.get(field) is None]
        if missing:
            return ParseError(
                row_number=row_number,
                identity=identity,
                detail=f"missing expected field(s): {', '.join(missing)}",
                source_file=source_file,
            )

        # Well-formed: route through the adapter so both schemas map onto the
        # common record and narrative_text comes from the right column (R1.2).
        return SchemaAdapter.to_source_record(
            row=row,
            schema=schema,
            source_file=source_file,
            row_number=row_number,
        )


# ===========================================================================
# run_filter — entry point (Requirements 1.1, 1.3, 6.1, 7.1, 7.2, 7.3)
#
# ``run_filter`` is the only component that performs I/O orchestration. It wires
# the shell together: open the reader, classify every record, route verified
# records to the writer and excluded records (plus parse-error markers) to the
# processing log, then emit the end-of-run report. The pure classification core
# and the thin I/O units it composes are all defined above.
# ===========================================================================

# Default source and output locations, resolved relative to the workspace root
# so the Filter behaves identically regardless of the current working directory
# (design "Technology Choice"). The source is the Boston 311 export named in
# R1.1; the output is a single clean CSV of Verified_Obstruction_Records (R6.1).
DEFAULT_SOURCE_FILENAME: str = "tmp7o3lwr4m.csv"
DEFAULT_OUTPUT_FILENAME: str = "verified_bike_lane_obstructions.csv"


def _workspace_root() -> str:
    """Return the workspace root — the parent of this package directory.

    ``filter.py`` lives in ``<root>/bike_lane_filter/``; the workspace root is
    its parent. Resolving paths against this makes the default source/output
    locations stable regardless of the current PowerShell working directory
    (design: "All file paths are resolved relative to the workspace root").
    """
    package_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(package_dir)


def _resolve_path(path: Optional[str], default_filename: str) -> str:
    """Resolve ``path`` to an absolute path, defaulting under the workspace root.

    ``None`` selects ``default_filename`` at the workspace root (R1.1 / R6.1). A
    relative path is resolved against the workspace root — not the process CWD —
    so behavior is independent of where the script is launched from. An absolute
    path is honored as given.
    """
    if path is None:
        return os.path.join(_workspace_root(), default_filename)
    if os.path.isabs(path):
        return path
    return os.path.join(_workspace_root(), path)


class OutputWriteError(OSError):
    """Raised when the Output_Dataset cannot be written (design Error Handling).

    Wraps the underlying :class:`OSError` (permissions, locked file, missing
    parent directory) and carries the target ``path`` so ``run_filter`` and the
    CLI can surface a descriptive, actionable message naming exactly where the
    write failed — rather than claiming success on a run that produced no output
    (R6.1). Subclasses :class:`OSError` so existing OS-error handlers keep
    working.
    """

    def __init__(self, path: str, cause: OSError) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"Failed to write Output_Dataset to {path}: {cause}")


def _parse_error_record(marker: ParseError) -> SourceRecord:
    """Build a minimal ``SourceRecord`` so a parse-error marker can be logged.

    The reader yields a :class:`ParseError` for rows it cannot map to the
    expected fields (R1.4). The :class:`ProcessingLog` records entries from
    ``SourceRecord``s, so wrap the marker's recovered ``case_id`` (or
    row-number fallback) and row number in an otherwise-empty record. The
    marker's ``identity`` is a bare ``case_id`` when one was recovered, or a
    ``row <n>`` label otherwise; only the former should populate ``case_id`` so
    the log's own fallback logic reproduces the right identity.
    """
    recovered_case_id = (
        marker.identity if marker.identity != f"row {marker.row_number}" else None
    )
    return SourceRecord(
        case_id=recovered_case_id,
        case_topic=None,
        service_name=None,
        closure_comments=None,
        closure_reason=None,
        case_status=None,
        longitude=None,
        latitude=None,
        row_number=marker.row_number,
        # Carry the originating file so the parse error is attributable to the
        # right Source_File in a multi-file run (R1.5); None for legacy markers.
        source_file=marker.source_file,
    )


def _normalize_source_paths(
    source_paths: Optional[Union[str, Iterable[str]]],
    source_path: Optional[str],
) -> list[str]:
    """Resolve the caller's source argument(s) to a list of absolute paths.

    Accepts the modern ``source_paths`` (a single path string or an iterable of
    them) and the legacy single ``source_path`` keyword, keeping both working so
    no existing call site breaks:

    * ``None`` for both → the single default legacy file at the workspace root
      (``tmp7o3lwr4m.csv``), per R1.1.
    * a ``str`` → a one-element list.
    * an iterable of ``str`` → each element resolved in order; an empty iterable
      falls back to the default so a run always has at least one file.

    Every path is resolved via :func:`_resolve_path`, so relative paths resolve
    against the workspace root regardless of the current working directory. File
    order is preserved so cross-file output ordering is deterministic (design
    "Determinism").

    ``source_paths`` takes precedence when both are given; a lone ``source_path``
    is treated as a single-element ``source_paths``.
    """
    # Legacy single-path keyword folds into the modern parameter.
    chosen = source_paths if source_paths is not None else source_path

    if chosen is None:
        return [_resolve_path(None, DEFAULT_SOURCE_FILENAME)]

    if isinstance(chosen, str):
        raw_paths: list[str] = [chosen]
    else:
        raw_paths = [p for p in chosen]
        if not raw_paths:
            # Empty iterable: treat as "unspecified" → default legacy file.
            return [_resolve_path(None, DEFAULT_SOURCE_FILENAME)]

    return [_resolve_path(p, DEFAULT_SOURCE_FILENAME) for p in raw_paths]


def run_filter(
    source_paths: Optional[Union[str, Iterable[str]]] = None,
    output_path: Optional[str] = None,
    log_path: Optional[str] = None,
    source_path: Optional[str] = None,
    on_file_error: Optional[Callable[[SkippedFile], None]] = None,
) -> RunSummary:
    """Run the full filter pipeline end-to-end and return its :class:`RunSummary`.

    Wires the I/O shell around the pure classification core (design
    "``run_filter`` (entry point)"):

    1. Open the reader over **every** configured Source_File, resolving each path
       relative to the workspace root (default the single legacy file
       ``tmp7o3lwr4m.csv``) so the run behaves identically regardless of the
       current working directory (R1.1). The reader detects each file's schema
       and maps its rows onto the common record (R1.2, R1.6).
    2. For each read result, count it toward ``total_read`` — **including**
       parse-error markers, which are logged under ``parse_error`` (R1.5) and
       counted so the reporting totals partition every source row across all
       files (R7.1).
    3. Classify each well-formed record through :func:`classify`; route verified
       records to the :class:`OutputWriter` and excluded records to the
       :class:`ProcessingLog` under their single :class:`ExclusionReason`.
    4. Write the single clean Output_Dataset CSV of Verified_Obstruction_Records
       combined across all files (R6.1), then build and return the end-of-run
       summary (R7.1–R7.3).

    A Source_File whose header matches no supported schema is **skipped** rather
    than aborting the whole run (R1.3): the ``on_file_error`` callback, when
    supplied, is invoked with a :class:`SkippedFile` for each skipped file, and
    every skipped file is also available on ``reader.skipped_files`` during the
    run. A **missing** file, by contrast, raises before any output is written
    (R1.4).

    Verified records are collected in memory before writing. The Output_Dataset
    is small relative to the inputs — only confirmed, in-bounds obstructions
    reach it — so this keeps memory reasonable while letting the writer report an
    accurate written count before the summary is produced.

    Args:
        source_paths: Source_File path(s). ``None`` defaults to the single
            legacy file ``tmp7o3lwr4m.csv`` at the workspace root; a single
            ``str`` reads one file; an iterable of ``str`` reads several,
            combining their records into one Source_Dataset (R1.1, R1.6). Each
            relative path resolves against the workspace root.
        output_path: Output_Dataset path. ``None`` defaults to
            ``verified_bike_lane_obstructions.csv`` at the workspace root; a
            relative path resolves against the workspace root (R6.1).
        log_path: Optional path for the ``.log`` mirror of the processing log.
            ``None`` keeps the log purely in memory.
        source_path: Deprecated single-path alias retained for backward
            compatibility; ignored when ``source_paths`` is given. Existing
            callers passing ``source_path=...`` keep working unchanged.
        on_file_error: Optional callback invoked with a :class:`SkippedFile` for
            each Source_File skipped due to an unrecognized schema (R1.3).

    Returns:
        The :class:`RunSummary` for the run (R7). The summary is balanced: the
        verified count plus every exclusion count equals ``total_read``.

    Raises:
        SourceFileNotFoundError: If any configured Source_File is missing (R1.4).
            Raised before the Output_Dataset is written, carrying the attempted
            absolute path.
        OutputWriteError: If the Output_Dataset cannot be written (permissions,
            locked file, missing parent directory). Raised instead of claiming
            success, carrying the target path (design Error Handling).
    """
    resolved_sources = _normalize_source_paths(source_paths, source_path)
    resolved_output = _resolve_path(output_path, DEFAULT_OUTPUT_FILENAME)

    reader = CsvReader()
    verified_records: list[SourceRecord] = []
    total_read = 0
    # Per-file verified counts for the summary (R7.4). Keyed by the record's
    # originating Source_File; a record with no source_file (legacy single-file
    # path) is keyed under its resolved file label so the arithmetic still
    # totals correctly even when attribution is coarse.
    verified_by_file: dict[str, int] = {}

    # The reader raises SourceFileNotFoundError when it reaches a missing file
    # (R1.4); we deliberately do not catch it here so it propagates before any
    # output is written. Unrecognized-schema files are skipped by the reader and
    # surfaced via on_file_error / reader.skipped_files without aborting (R1.3).
    with ProcessingLog(log_path) as log:
        for result in reader.read_many(resolved_sources, on_file_error=on_file_error):
            total_read += 1  # Every source row counts, parse errors included.

            if isinstance(result, ParseError):
                # Unmappable row: log under parse_error and skip (R1.5). It still
                # counted toward total_read above so the totals stay partitioned.
                log.record(_parse_error_record(result), ExclusionReason.PARSE_ERROR)
                continue

            classification = classify(result)
            if classification.is_verified:
                verified_records.append(result)
                # Tally this verified record against its originating file (R7.4).
                file_key = (
                    result.source_file
                    if result.source_file is not None
                    else "<unknown>"
                )
                verified_by_file[file_key] = verified_by_file.get(file_key, 0) + 1
            else:
                # Excluded: log the single reason for the first gate it failed
                # (R3.2, R4.5, R5.4).
                log.record(result, classification.reason)

        # Write the single clean Output_Dataset (R6.1). A write failure must
        # surface rather than be reported as success, so wrap the OS error with
        # the target path (design Error Handling).
        try:
            total_written = OutputWriter().write(verified_records, resolved_output)
        except OSError as exc:
            raise OutputWriteError(resolved_output, exc) from exc

        return Reporter().summarize(
            total_read, total_written, log, verified_by_file=verified_by_file
        )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI wrapper for :func:`run_filter` (Requirements 1.1, 1.3, 1.4, 6.1, 7).

    Parses optional ``--source`` (one or more paths), ``--output``, and
    ``--log`` overrides, runs the filter, prints a descriptive warning for any
    Source_File skipped due to an unrecognized schema (R1.3), prints the
    end-of-run report (R7), and returns a process exit code: ``0`` on success.
    On a missing Source_File (R1.4) or an output write failure (design Error
    Handling) it prints a descriptive message naming the offending path to
    stderr and returns a non-zero code before claiming success.

    Args:
        argv: Argument list for testing; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` on a missing source file or an output write
        failure.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="bike_lane_filter",
        description=(
            "Filter one or more Boston 311 exports into a clean CSV of verified "
            "bike-lane obstruction events."
        ),
    )
    parser.add_argument(
        "--source",
        nargs="+",
        default=None,
        metavar="CSV",
        help=(
            "One or more source 311 CSV paths (space-separated). Defaults to "
            f"{DEFAULT_SOURCE_FILENAME} at the workspace root when omitted."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Path for the output CSV. Defaults to "
            f"{DEFAULT_OUTPUT_FILENAME} at the workspace root."
        ),
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Optional path to mirror the processing log as a .log file.",
    )
    args = parser.parse_args(argv)

    # Collect skipped-schema files so each can be reported after the run (R1.3).
    skipped: list[SkippedFile] = []

    def _note_skip(skipped_file: SkippedFile) -> None:
        skipped.append(skipped_file)

    try:
        summary = run_filter(
            source_paths=args.source,
            output_path=args.output,
            log_path=args.log,
            on_file_error=_note_skip,
        )
    except SourceFileNotFoundError as exc:
        # R1.4: descriptive error naming the missing path; stop with non-zero.
        print(
            f"Error: source file not found at {exc.path}. "
            "Processing stopped.",
            file=sys.stderr,
        )
        return 1
    except OutputWriteError as exc:
        # Output could not be written: name the path and fail rather than
        # claiming success (design Error Handling).
        print(
            f"Error: could not write output dataset to {exc.path}: {exc.cause}.",
            file=sys.stderr,
        )
        return 1

    # R1.3: name each skipped file and why, without failing the run — the
    # remaining files were processed normally.
    for skipped_file in skipped:
        print(
            f"Warning: skipped {skipped_file.source_file} — {skipped_file.error}",
            file=sys.stderr,
        )

    print(summary.format_report())
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

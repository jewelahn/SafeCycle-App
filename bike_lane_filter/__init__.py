"""Bike Lane Obstruction Filter package.

A one-shot, standard-library-only Python 3 ETL pipeline that reads a Boston 311
service-request export, classifies each record through an ordered sequence of
inclusion/exclusion gates, and writes a single clean CSV of verified bike-lane
obstruction events for a downstream cyclist-routing application.

See ``.kiro/specs/bike-lane-obstruction-filter`` for requirements and design.
"""

from .filter import (
    # Coordinate bounds
    LONGITUDE_MIN,
    LONGITUDE_MAX,
    LATITUDE_MIN,
    LATITUDE_MAX,
    # Output projection field order
    OUTPUT_FIELDS,
    # Per-schema column maps
    LEGACY_COLUMN_MAP,
    CURRENT_COLUMN_MAP,
    COLUMN_MAPS,
    # Data models
    SourceRecord,
    OutputRecord,
    Classification,
    # Enums
    Schema,
    Verification,
    ExclusionReason,
    # Schema adaptation
    SchemaAdapter,
    UnknownSchemaError,
    # Processing log
    ExclusionEntry,
    ProcessingLog,
    # Input reading
    CsvReader,
    ParseError,
    SkippedFile,
    SourceFileNotFoundError,
    # Output writing
    OutputWriter,
    # Reporting
    Reporter,
    RunSummary,
    # Gate orchestrator
    classify,
    # Entry point
    run_filter,
    main,
    OutputWriteError,
    DEFAULT_SOURCE_FILENAME,
    DEFAULT_OUTPUT_FILENAME,
)

__all__ = [
    "LONGITUDE_MIN",
    "LONGITUDE_MAX",
    "LATITUDE_MIN",
    "LATITUDE_MAX",
    "OUTPUT_FIELDS",
    "LEGACY_COLUMN_MAP",
    "CURRENT_COLUMN_MAP",
    "COLUMN_MAPS",
    "SourceRecord",
    "OutputRecord",
    "Classification",
    "Schema",
    "Verification",
    "ExclusionReason",
    "SchemaAdapter",
    "UnknownSchemaError",
    "ExclusionEntry",
    "ProcessingLog",
    "CsvReader",
    "ParseError",
    "SkippedFile",
    "SourceFileNotFoundError",
    "OutputWriter",
    "Reporter",
    "RunSummary",
    "classify",
    "run_filter",
    "main",
    "OutputWriteError",
    "DEFAULT_SOURCE_FILENAME",
    "DEFAULT_OUTPUT_FILENAME",
]

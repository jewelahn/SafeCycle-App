# Implementation Plan: Bike Lane Obstruction Filter

## Overview

The filter is a one-shot Python 3 ETL script built on the standard-library `csv` module (no pandas), runnable on Windows/PowerShell. Implementation proceeds bottom-up: first the data models and enums, then the pure `Classifier` functions (the testable core) with their Hypothesis property tests written alongside, then the I/O shell (`CsvReader`, `OutputWriter`, `ProcessingLog`, `Reporter`), and finally the `run_filter` entry point that wires everything together and runs end-to-end against the real `tmp7o3lwr4m.csv`. Each correctness property from the design is implemented as a single Hypothesis test (min 100 iterations) placed close to the code it validates so structural guarantees are pinned early.

Tasks 1-13 delivered the original single-file filter and are complete. Tasks 14-19 extend it for **multiple yearly exports under differing schemas** (the current Boston open-data schema omits `closure_comments` and carries its narrative in `closure_reason`) and a **broadened verification vocabulary** that recovers addressed-obstruction phrasings ("cleared from", "picked up out of", "swept", "pruned back") while keeping negative-signal precedence so already-clear records stay excluded.

## Tasks

- [x] 1. Set up project structure, data models, and testing framework
  - Create the package directory with `__init__.py` and a module file (`filter.py`) for the pipeline
  - Define `SourceRecord` (with `case_id`, `case_topic`, `service_name`, `closure_comments`, `closure_reason`, `case_status`, `longitude`, `latitude`, `row_number`), `OutputRecord` projection, and `Classification` (outcome + optional reason)
  - Define `Verification` enum (`CONFIRMED`, `UNVERIFIED`) and `ExclusionReason` enum (`parse_error`, `no_bike_lane_context`, `unrelated_record`, `unverified_record`, `invalid_coordinates`)
  - Add module-level constants for coordinate bounds (lon [-71.3, -70.9], lat [42.2, 42.5], inclusive)
  - Add Hypothesis to the dev environment and create the test module with a shared `SourceRecord` generator strategy (randomized topic/service/comment text with bike-lane phrases in varied casing/punctuation/whitespace; coordinates in-bounds, out-of-bounds, missing, non-numeric; positive/negative verification signals)
  - _Requirements: 5.2, 6.3, 6.5_

- [x] 2. Implement Gate 1 — bike-lane context detection
  - [x] 2.1 Implement `has_bike_lane_context` and text normalization
    - Write the normalization helper (lowercase, collapse whitespace runs) and the whole-phrase regex matcher for `bike lane` / `bike lanes` / `bike line` across `case_topic`, `service_name`, `closure_comments`
    - Treat absent/empty/null fields as no match and continue evaluating remaining fields
    - Keep phrase sets in named module-level constants
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 2.2 Write property test for bike-lane context invariant
    - **Property 1: Every output record has bike-lane context**
    - **Validates: Requirements 2.1, 2.3, 2.4**
    - Single Hypothesis test, min 100 iterations, tagged `Feature: bike-lane-obstruction-filter, Property 1: ...`

  - [x] 2.3 Write unit/example tests for context matching edge cases
    - Mixed-case and punctuation-adjacent phrases, service-name-only matches, empty/missing `closure_comments`
    - _Requirements: 2.1, 2.2_

- [x] 3. Implement Gate 2 — unrelated-record detection
  - [x] 3.1 Implement `is_unrelated`
    - Detect stolen/abandoned bicycle and bike-rack subjects (`stolen bike`, `abandoned bike`, `abandoned bicycle`, `bike rack`, `bicycle rack`) against topic/service/comments using the shared normalization
    - Keep unrelated phrase sets in named module-level constants
    - _Requirements: 3.1_

  - [x] 3.2 Write unit/example tests for unrelated classification
    - A stolen-bike / bike-rack record that still mentions a bike lane is classified unrelated
    - _Requirements: 3.1, 3.2_

- [x] 4. Implement Gate 3 — verification of real obstructions
  - [x] 4.1 Implement `verification_outcome`
    - Define negative/no-obstruction signals (no violation found, unable to locate, no issue found, no bike was found, bike lane is clear, bike is lift up, lifted away, informational "for information on bike lanes") and positive/confirming signals (removal/ticketing/blocking of a bike lane obstruction, pothole in bike lane)
    - Return `CONFIRMED` only when a positive signal is present and no negative signal overrides it; negative signals take precedence; default ambiguous comments to `UNVERIFIED`
    - Apply primarily to `closure_comments` with `closure_reason` as a secondary signal; keep signal sets in named module-level constants
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [x] 4.2 Write property test for negative-signal precedence
    - **Property 6: Negative signals override positive signals in verification**
    - **Validates: Requirements 4.1, 4.2, 4.4**
    - Single Hypothesis test, min 100 iterations, tagged `Feature: bike-lane-obstruction-filter, Property 6: ...`

  - [x] 4.3 Write unit/example tests for verification from real comments
    - CONFIRMED: "Removed barrels from bike lane..."; "tree that was blocking the sidewalk and bike lane removed..."
    - UNVERIFIED: "For information on bike lanes, you can email..."; "No violation found by Code Enforcement"; "Unable to Locate"; "bike lane is clear"; "bike is lift up"; "no bike was found"; the "bike lane clearance ... no further pruning needed" pruning case
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [x] 5. Implement Gate 4 — coordinate validation
  - [x] 5.1 Implement `has_valid_coordinates`
    - Parse `longitude`/`latitude` as floats, require both present and numeric, and enforce Boston bounds inclusively
    - Treat non-numeric or out-of-range as invalid without raising
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 5.2 Write property test for coordinate invariant
    - **Property 2: Every output record has valid coordinates**
    - **Validates: Requirements 5.1, 5.2, 5.3, 6.2**
    - Single Hypothesis test, min 100 iterations, tagged `Feature: bike-lane-obstruction-filter, Property 2: ...`

- [x] 6. Implement the gate orchestrator `classify`
  - [x] 6.1 Implement `classify`
    - Run the four gates in order (R2 → R3 → R4 → R5); the first gate that rejects assigns its `ExclusionReason`; a record passing all gates is `verified`
    - Guarantee each record maps to exactly one `Classification` (single-outcome invariant)
    - _Requirements: 2.4, 3.1, 4.1, 4.2, 4.3, 4.4, 5.3, 5.4_

  - [x] 6.2 Write property test for exclusion safety
    - **Property 3: Excluded-class records never appear in the output**
    - **Validates: Requirements 2.4, 3.1, 4.1, 4.2, 4.4, 5.4, 6.2**
    - Single Hypothesis test, min 100 iterations, tagged `Feature: bike-lane-obstruction-filter, Property 3: ...`

  - [x] 6.3 Write property test for classification determinism
    - **Property 5: Classification is deterministic**
    - **Validates: Requirements 4.3, 4.4**
    - Single Hypothesis test, min 100 iterations, tagged `Feature: bike-lane-obstruction-filter, Property 5: ...`

- [x] 7. Checkpoint - classifier core complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement input reading and the processing log
  - [x] 8.1 Implement `CsvReader`
    - Open the source via `csv.DictReader`, locating fields by header row, and stream `SourceRecord`s lazily so the ~26 MB file is not fully materialized
    - Raise `SourceFileNotFoundError` carrying the attempted absolute path when the file is missing
    - Yield a parse-error marker for rows that cannot be mapped to the expected fields, attaching the `case_id` or row number
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 8.2 Implement `ProcessingLog`
    - Accumulate per-record exclusion entries (`case_id`/row + `ExclusionReason`) within the run, backed by an in-memory list and optionally mirrored to a `.log` file
    - _Requirements: 1.4, 3.2, 4.5, 5.4_

  - [x] 8.3 Write unit tests for reader error paths
    - Missing source file raises the descriptive error with the path (R1.3); a malformed row is logged under `parse_error` and skipped without aborting (R1.4)
    - _Requirements: 1.3, 1.4_

- [x] 9. Implement output writing
  - [x] 9.1 Implement `OutputWriter`
    - Write a single header row then one row per Verified_Obstruction_Record with exactly `case_id, case_topic, service_name, closure_comments, longitude, latitude` and no severity/weight column; return the number of rows written
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 9.2 Write property test for output projection shape
    - **Property 7: Output projection shape is exact**
    - **Validates: Requirements 6.3, 6.4, 6.5**
    - Single Hypothesis test, min 100 iterations, tagged `Feature: bike-lane-obstruction-filter, Property 7: ...`

- [x] 10. Implement the reporter
  - [x] 10.1 Implement `Reporter`
    - Produce the end-of-run summary: total records read, total verified records written, and excluded counts grouped by reason (`no_bike_lane_context`, `unrelated_record`, `unverified_record`, `invalid_coordinates`, `parse_error`)
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 10.2 Write property test for count partition
    - **Property 4: Counts partition the input**
    - **Validates: Requirements 7.1, 7.2, 7.3**
    - Single Hypothesis test, min 100 iterations, tagged `Feature: bike-lane-obstruction-filter, Property 4: ...`; feed a generated record set through the full filter and assert the arithmetic

- [x] 11. Wire the `run_filter` entry point
  - [x] 11.1 Implement `run_filter`
    - Open the reader, resolve the source path relative to the workspace root, classify each record, route verified records to the writer and excluded records to the log, then emit the report
    - Surface a non-zero exit with a descriptive message on missing source file or output write failure before claiming success
    - _Requirements: 1.1, 1.3, 6.1, 7.1, 7.2, 7.3_

  - [x] 11.2 Write integration test against a fixture CSV
    - Run `run_filter` end-to-end on a small hand-crafted CSV spanning every outcome; assert output contains only expected verified rows with exact R6 columns, the log lists each exclusion with a reason, and counts satisfy Property 4
    - _Requirements: 1.1, 1.4, 6.1, 6.2, 6.3, 7.1, 7.2, 7.3_

- [x] 12. Run the filter against the real dataset
  - [x] 12.1 Execute and smoke-check against `tmp7o3lwr4m.csv`
    - Run `run_filter` on the real workspace-root CSV, producing the clean Output_Dataset and processing log
    - Add a smoke test asserting the script completes, output is non-empty, and reported total-read matches the file's data-row count
    - _Requirements: 1.1, 1.2, 6.1, 7.1, 7.2, 7.3_

- [x] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Introduce the common record and schema adaptation
  - [x] 14.1 Add `Schema` enum, `narrative_text`/`source_file` fields, and per-schema column maps
    - Add the `Schema` enum (`LEGACY`, `CURRENT`) and extend `SourceRecord` with `narrative_text` and `source_file`; keep `closure_comments` populated for the legacy path and have the legacy adapter set `narrative_text` from it
    - Define named module-level column-map constants for both schemas (legacy: `case_id`/`case_topic`/`service_name`/`closure_comments`; current: `case_enquiry_id`→`case_id`, `case_title`→`case_topic`, `type`→`service_name`, `closure_reason`→`narrative_text`)
    - _Requirements: 1.2, 1.6_

  - [x] 14.2 Implement `SchemaAdapter` (`detect` + `to_source_record`)
    - `detect(header)` returns `LEGACY`/`CURRENT` or raises `UnknownSchemaError` naming the file and unrecognized columns; `to_source_record(...)` maps a row onto the common `SourceRecord`, selecting `narrative_text` from `closure_comments` (legacy) or `closure_reason` (current) and attaching `source_file`/`row_number`
    - _Requirements: 1.2, 1.3, 1.6_

  - [x] 14.3 Point the classifier at `narrative_text`
    - Update `has_bike_lane_context` and `verification_outcome` to read `record.narrative_text` instead of `closure_comments` directly, so the gates are schema-agnostic; keep behavior identical for legacy records
    - _Requirements: 2.1, 4.1, 4.2, 4.3_

  - [x] 14.4 Write unit tests for the schema adapter
    - Legacy and current headers each map to the correct common fields; an unrecognized header raises `UnknownSchemaError` naming the file; the current adapter draws `narrative_text` from `closure_reason`
    - _Requirements: 1.2, 1.3, 1.6_

  - [x] 14.5 Write property test for schema adaptation equivalence
    - **Property 8: Schema adaptation preserves the common record**
    - **Validates: Requirements 1.2, 1.6**
    - Single Hypothesis test, min 100 iterations, tagged `Feature: bike-lane-obstruction-filter, Property 8: ...`; generate an equivalent record under both schemas and assert identical classification

- [x] 15. Broaden the verification vocabulary
  - [x] 15.1 Extend positive and negative signal sets
    - Add positive confirmations (`cleared from`, `picked up out of`, `swept`/`mechanically swept`, `pruned back`) and negatives (`remains unimpeded`, `no further pruning needed`, `no work needed`, future-work `will be serviced`/`going to be serviced`) to the module-level constants; preserve negative-over-positive precedence
    - _Requirements: 4.2, 4.3, 4.4_

  - [x] 15.2 Update verification example tests for the broadened vocabulary
    - CONFIRMED: "Items ... picked up out of bike lane"; "Tree limb ... cleared from sidewalk and bike lane"; "bike lanes mechanically swept"; current-schema "Glass removed from bike lane" and "barrier has been removed from the bike lane" via `closure_reason`
    - UNVERIFIED by precedence: "Bike Lane clear / Ticket issued"; future-work "going to be serviced on Saturday"; "remains unimpeded. No work needed"
    - _Requirements: 4.2, 4.3, 4.4, 4.5_

- [x] 16. Support multiple source files end-to-end
  - [x] 16.1 Make `CsvReader` and `run_filter` multi-file and schema-aware
    - `CsvReader.read_records` accepts multiple paths, detects each file's schema via `SchemaAdapter`, streams common records across all files, raises `SourceFileNotFoundError` for a missing file, and skips an `UnknownSchemaError` file without aborting the rest; `run_filter` accepts a list of source files (default the legacy file), resolves each relative to the workspace root, and combines their records
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [x] 16.2 Add per-file verified counts to the reporter and log
    - Extend `ProcessingLog` entries and `Reporter` output so parse-error/exclusion entries carry the originating file and the summary reports verified counts per Source_File when more than one is processed
    - _Requirements: 1.5, 7.1, 7.4_

  - [x] 16.3 Write a two-schema integration test
    - Run `run_filter` on two fixture CSVs (one legacy, one current-schema) spanning every outcome; assert the output has only verified rows with exact R6 columns (narrative written to the `closure_comments` column), the log attributes each exclusion to its file, per-file verified counts are reported, and counts satisfy Property 4
    - _Requirements: 1.5, 1.6, 6.3, 7.1, 7.4_

- [x] 17. Checkpoint - multi-year pipeline complete
  - Ensure the full suite (original + new) passes; confirm Property 8 and the broadened-vocabulary examples hold. Ask the user if questions arise.

- [x] 18. Run against all available exports
  - [x] 18.1 Execute and smoke-check across 2024/2025/2026 files
    - Run `run_filter` over `tmp7o3lwr4m.csv`, `lagan_311_open_data_2025_rvsd3.csv`, and `tmpch36ak7j.csv`, producing the combined Output_Dataset and processing log; add/extend a smoke test asserting completion, non-empty output, per-file total-read matching each file's data-row count, and balanced counts (Property 4)
    - Spot-check the 2025 verified rows (the largest broadening effect) to confirm they are genuine addressed obstructions
    - _Requirements: 1.1, 1.2, 1.6, 6.1, 7.1, 7.2, 7.3, 7.4_

- [x] 19. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass across both schemas; confirm the combined verified count and per-file breakdown are reported. Ask the user if questions arise.

## Notes

- Tasks 1-13 are the completed original single-file filter; Tasks 14-19 are the multi-year / broadened-vocabulary revision and build on that code.
- Each task references specific requirement sub-clauses (R1-R7) for traceability.
- The schema adapter and the `narrative_text` redirect (Task 14) land before multi-file wiring (Task 16) so every gate is schema-agnostic before more files are introduced.
- Property 8 (schema adaptation) joins the original seven properties; each is a single Hypothesis test (min 100 iterations) tagged `Feature: bike-lane-obstruction-filter, Property N: ...`.
- Checkpoints provide incremental validation at the end of the classifier core, the original pipeline, and the multi-year pipeline.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1", "3.1", "4.1", "5.1", "8.1", "8.2", "9.1", "10.1"] },
    { "id": 1, "tasks": ["2.2", "2.3", "3.2", "4.2", "4.3", "5.2", "8.3", "9.2"] },
    { "id": 2, "tasks": ["6.1"] },
    { "id": 3, "tasks": ["6.2", "6.3"] },
    { "id": 4, "tasks": ["11.1"] },
    { "id": 5, "tasks": ["10.2", "11.2"] },
    { "id": 6, "tasks": ["12.1"] },
    { "id": 7, "tasks": ["14.1"] },
    { "id": 8, "tasks": ["14.2", "14.3", "15.1"] },
    { "id": 9, "tasks": ["14.4", "14.5", "15.2"] },
    { "id": 10, "tasks": ["16.1", "16.2"] },
    { "id": 11, "tasks": ["16.3"] },
    { "id": 12, "tasks": ["18.1"] }
  ]
}
```

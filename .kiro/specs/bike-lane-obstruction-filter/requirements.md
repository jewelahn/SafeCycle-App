# Requirements Document

## Introduction

This feature produces a single, clean dataset of **verified bike lane obstruction events** derived from one or more Boston 311 service-request exports. The dataset is the input to a downstream cyclist routing application serving cyclists in Boston that prioritizes safety first and travel time second. Each verified obstruction is treated as an equal-weight safety signal that the routing decision layer can map onto the route network.

The source 311 feed has no dedicated "bike lane obstruction" category. Obstruction records must be identified by matching bike-lane context across the case topic, service name, and resolution/closure narrative text, and then separating genuine obstructions from records that are purely informational, found no violation, found the lane already clear, or concern unrelated matters (for example stolen bikes or bike racks). Only records that both confirm a real obstruction and carry valid geographic coordinates are retained in the output.

Boston publishes 311 data as one export per year, and the column schema has changed across years. Older exports (for example `tmp7o3lwr4m.csv`) include a human-written `closure_comments` narrative; newer exports (2025 and later) use different column names (for example `case_enquiry_id`, `case_title`, `type`) and omit `closure_comments`, redacting the complaint description for privacy while retaining a resolution narrative inside `closure_reason`. The Filter must therefore accept multiple source files under differing schemas and map each onto a common internal record before classification. Because the richest confirming text in newer exports lives in `closure_reason`, the verification vocabulary reads whichever narrative field is available.

## Glossary

- **Filter**: The system component that reads the source 311 exports, applies all inclusion and exclusion rules, and writes the output dataset.
- **Source_File**: A single Boston 311 export file provided as input. One or more Source_Files may be processed in a single run.
- **Source_Schema**: The set of column names used by a Source_File. Two schemas are supported: the legacy schema (`case_id`, `case_topic`, `service_name`, `closure_comments`, `closure_reason`, `case_status`, `longitude`, `latitude`) and the current open-data schema (`case_enquiry_id`, `case_title`, `type`, `closure_reason`, `case_status`, `longitude`, `latitude`, with no `closure_comments`).
- **Schema_Adapter**: The mapping that translates a Source_File's columns, under either Source_Schema, onto the common internal Source_Record fields.
- **Source_Dataset**: The combined set of Source_Records read from all Source_Files in a run.
- **Source_Record**: A single row read from a Source_File and mapped onto the common internal fields: `case_id`, `case_topic`, `service_name`, `narrative_text`, `closure_reason`, `case_status`, `longitude`, `latitude`, plus the originating file and row number for logging.
- **Narrative_Text**: The free-text resolution/closure narrative a Source_Record carries, drawn from `closure_comments` when the legacy schema provides it and from `closure_reason` otherwise. This is the primary text the verification rules read.
- **Bike_Lane_Context**: Presence of the phrase "bike lane", "bike lanes", or "bike line" (case-insensitive) in a Source_Record's `case_topic`, `service_name`, or Narrative_Text.
- **Obstruction_Event**: A Source_Record whose text confirms that a physical obstruction of a bike lane actually occurred and was present or addressed (for example debris, barrels, fallen branches, dumped items, a pothole in a bike lane, or material removed, cleared, swept, or pruned from a bike lane).
- **Unverified_Record**: A Source_Record that references a bike lane but does not confirm an obstruction, including purely informational replies, no-violation findings, unable-to-locate findings, already-clear findings, and future-work notes.
- **Unrelated_Record**: A Source_Record that contains the word "bike" but does not describe a bike lane obstruction (for example abandoned or stolen bicycles, or bike racks).
- **Valid_Coordinates**: A `longitude` and `latitude` pair where both values are present, numeric, and fall within Boston-area geographic bounds (longitude between -71.3 and -70.9, latitude between 42.2 and 42.5).
- **Verified_Obstruction_Record**: An Obstruction_Event that also has Valid_Coordinates. These are the only records written to the Output_Dataset.
- **Output_Dataset**: The clean filtered dataset (CSV) consumed by the routing decision layer, containing only Verified_Obstruction_Records drawn from every Source_File in the run.
- **Routing_Decision_Layer**: The downstream consumer of the Output_Dataset that maps obstruction events onto the route network.

## Requirements

### Requirement 1: Read one or more source 311 datasets

**User Story:** As a routing-app developer, I want the Filter to read one or more Boston 311 exports across differing yearly schemas, so that obstruction records can be extracted from several years of raw service-request data.

#### Acceptance Criteria

1. WHEN the Filter is run, THE Filter SHALL read every configured Source_File, defaulting to `tmp7o3lwr4m.csv` at the workspace root when no Source_File is specified.
2. WHEN the Filter reads a Source_File, THE Filter SHALL detect its Source_Schema from the header row and apply the matching Schema_Adapter to map each row onto the common Source_Record fields, drawing Narrative_Text from `closure_comments` under the legacy schema and from `closure_reason` under the current open-data schema.
3. IF a Source_File's header row matches no supported Source_Schema, THEN THE Filter SHALL report a descriptive error that identifies the file and its unrecognized columns and SHALL stop processing that file without aborting the remaining Source_Files.
4. IF a configured Source_File is not found at its expected path, THEN THE Filter SHALL report a descriptive error that identifies the missing file path and SHALL stop processing.
5. IF a Source_Record cannot be parsed into the expected fields, THEN THE Filter SHALL exclude that Source_Record from the Output_Dataset and SHALL record the affected `case_id` or row number, together with the originating Source_File, in a processing log.
6. WHEN the Filter reads multiple Source_Files, THE Filter SHALL combine their Source_Records into a single Source_Dataset for classification and output.

### Requirement 2: Identify bike-lane-obstruction candidates

**User Story:** As a routing-app developer, I want the Filter to find records that reference a bike lane obstruction, so that only bike-lane-relevant records are considered for the output.

#### Acceptance Criteria

1. WHEN the Filter evaluates a Source_Record, THE Filter SHALL perform case-insensitive matching for the phrases "bike lane", "bike lanes", and "bike line" against the `case_topic`, `service_name`, and Narrative_Text fields, treating a phrase as matched when it appears as a whole word or word sequence regardless of surrounding punctuation or whitespace.
2. IF a `case_topic`, `service_name`, or Narrative_Text field is absent, empty, or null in a Source_Record, THEN THE Filter SHALL treat that field as containing no match and continue evaluating the remaining fields.
3. WHERE a Source_Record contains Bike_Lane_Context, THE Filter SHALL mark that Source_Record as a bike-lane-obstruction candidate.
4. WHERE a Source_Record does not contain Bike_Lane_Context, THE Filter SHALL exclude that Source_Record from the Output_Dataset.

### Requirement 3: Exclude unrelated bike records

**User Story:** As a routing-app developer, I want the Filter to drop records that mention bikes but are not bike lane obstructions, so that the output is not polluted by irrelevant signals.

#### Acceptance Criteria

1. IF a candidate Source_Record describes an abandoned bicycle, a stolen bicycle, a bike rack, or another subject that is not an obstruction of a bike lane, THEN THE Filter SHALL classify that Source_Record as an Unrelated_Record and SHALL exclude it from the Output_Dataset.
2. WHEN the Filter excludes an Unrelated_Record, THE Filter SHALL record the `case_id` and the exclusion reason in a processing log.

### Requirement 4: Exclude unverified and no-violation records

**User Story:** As a routing-app developer, I want the Filter to keep only records that confirm a real obstruction occurred, so that routing decisions are based on verified safety signals.

#### Acceptance Criteria

1. IF a candidate Source_Record is a purely informational reply whose Narrative_Text provides general guidance or contact information and does not assert that a physical bike-lane obstruction existed (for example, text matching the intent of "For information on bike lanes, you can email..."), THEN THE Filter SHALL classify that Source_Record as an Unverified_Record and SHALL exclude it from the Output_Dataset.
2. IF a candidate Source_Record's Narrative_Text asserts any of the following negative or non-actioned outcomes — no violation was found, the location could not be located or identified, no issue or no bike was found at the location, the bike lane was already clear or unimpeded or the obstruction was lifted away without confirming a prior lane obstruction, or the work is only scheduled for a future date and not yet performed (for example, text matching the intent of "No violation found by Code Enforcement", "Unable to Locate", "No issue found at location", "bike lane is clear", "bike lane remains unimpeded", "no further pruning needed", "bike is lift up", "no bike was found", or "will be serviced on <date>") — THEN THE Filter SHALL classify that Source_Record as an Unverified_Record and SHALL exclude it from the Output_Dataset.
3. WHERE a candidate Source_Record's Narrative_Text asserts that a physical obstruction of a bike lane occurred, was ticketed, or was addressed by removing, clearing, picking up, sweeping, or pruning material from the bike lane (for example, text matching the intent of "Removed barrels from bike lane", "mattress was removed from the bike lane", "glass removed from bike lane", "cleared from ... bike lane", "picked up out of bike lane", "bike lanes mechanically swept", "thorns ... pruned back" from a bike lane, "ticketed for bike lane", "tree ... blocking the ... bike lane removed", or "bad pothole in bike lane"), AND no negative or non-actioned outcome from criterion 2 is also present, THE Filter SHALL classify that Source_Record as an Obstruction_Event and SHALL include it in the Output_Dataset.
4. IF a candidate Source_Record's Narrative_Text matches both a negative or non-actioned outcome from criterion 2 and an obstruction-addressed phrase from criterion 3, THEN the negative outcome SHALL take precedence and THE Filter SHALL classify that Source_Record as an Unverified_Record and SHALL exclude it from the Output_Dataset.
5. IF a candidate Source_Record's Narrative_Text matches neither the Unverified_Record criteria in criteria 1 and 2 nor the Obstruction_Event criteria in criterion 3, THEN THE Filter SHALL classify that Source_Record as an Unverified_Record and SHALL exclude it from the Output_Dataset.
6. WHEN the Filter excludes an Unverified_Record, THE Filter SHALL record the `case_id` and the exclusion reason in a processing log within the same processing run that performed the exclusion.

### Requirement 5: Require valid geographic coordinates

**User Story:** As a routing-app developer, I want every retained record to carry mappable coordinates, so that the Routing_Decision_Layer can place each obstruction on the route network.

#### Acceptance Criteria

1. WHEN the Filter evaluates an Obstruction_Event, THE Filter SHALL confirm that the `longitude` and `latitude` fields are both present and parse as numeric values.
2. WHEN the Filter evaluates the coordinates of an Obstruction_Event, THE Filter SHALL confirm that `longitude` falls between -71.3 and -70.9 and that `latitude` falls between 42.2 and 42.5.
3. WHERE an Obstruction_Event has Valid_Coordinates, THE Filter SHALL classify that Source_Record as a Verified_Obstruction_Record.
4. IF an Obstruction_Event does not have Valid_Coordinates, THEN THE Filter SHALL exclude that Source_Record from the Output_Dataset and SHALL record the `case_id` and the exclusion reason in a processing log.

### Requirement 6: Produce the clean output dataset

**User Story:** As a routing-app developer, I want a single clean CSV of verified obstruction events, so that the Routing_Decision_Layer can consume the data directly.

#### Acceptance Criteria

1. WHEN the Filter finishes evaluating all Source_Records across all Source_Files, THE Filter SHALL write exactly one Output_Dataset file in CSV format.
2. THE Filter SHALL write only Verified_Obstruction_Records to the Output_Dataset.
3. THE Output_Dataset SHALL include, for each Verified_Obstruction_Record, the `case_id`, `case_topic`, `service_name`, Narrative_Text (written to a `closure_comments` column), `longitude`, and `latitude` fields.
4. THE Filter SHALL write a header row as the first line of the Output_Dataset naming each included field.
5. WHEN the Filter treats obstruction events as routing signals, THE Filter SHALL assign every Verified_Obstruction_Record equal standing without any severity or weighting field.

### Requirement 7: Report filtering results

**User Story:** As a routing-app developer, I want a summary of what was kept and dropped, so that I can confirm the filter behaved correctly across the datasets.

#### Acceptance Criteria

1. WHEN the Filter completes processing, THE Filter SHALL report the total count of Source_Records read across all Source_Files.
2. WHEN the Filter completes processing, THE Filter SHALL report the count of Verified_Obstruction_Records written to the Output_Dataset.
3. WHEN the Filter completes processing, THE Filter SHALL report the count of excluded records grouped by exclusion reason, covering no Bike_Lane_Context, Unrelated_Record, Unverified_Record, missing Valid_Coordinates, and parse errors.
4. WHERE more than one Source_File is processed, THE Filter SHALL additionally report the count of Verified_Obstruction_Records contributed by each Source_File.

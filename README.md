# Hackachusetts-2026 — Bike Lane Obstruction Filter

A one-shot Python 3 ETL pipeline that reads Boston 311 service-request exports,
identifies **verified bike-lane obstruction events**, and writes a single clean
CSV for a downstream cyclist-routing application. Only records that both confirm
a real obstruction and carry valid Boston-area coordinates are kept.

The full specification lives in `.kiro/specs/bike-lane-obstruction-filter/`
(`requirements.md`, `design.md`, `tasks.md`).

## Data files at the workspace root

These CSVs are **Boston 311 service-request exports** downloaded from the City
of Boston open-data portal (Analyze Boston). Their file names are the raw
download names and are not self-describing, so they are documented here. The
"Event dates" column is the range of request open-dates actually found in each
file (from the `open_date` / `open_dt` column).

| File | What it is | Event dates | Schema | Rows | Size |
|------|-----------|-------------|--------|------|------|
| `tmp7o3lwr4m.csv` | Boston 311 export | 2025-08 to 2026-10 | Legacy | ~75,851 | ~26 MB |
| `lagan_311_open_data_2025_rvsd3.csv` | Boston 311 export | 2025 (full year) | Current open-data | ~276,093 | ~152 MB |
| `tmpch36ak7j.csv` | Boston 311 export | 2026 (through ~Oct) | Current open-data | ~190,668 | ~102 MB |

### Two schemas

The column schema differs between the older and newer exports:

- **Legacy** (`tmp7o3lwr4m.csv`): carries a human-written `closure_comments`
  narrative, and names the identity/topic/service columns `case_id` /
  `case_topic` / `service_name`.
- **Current open-data** (the two `...2025...` / `tmpch36ak7j` files): renames
  those columns to `case_enquiry_id` / `case_title` / `type` and **omits
  `closure_comments`** (the complaint description is redacted for privacy). The
  surviving resolution narrative lives in `closure_reason`.

The pipeline's `SchemaAdapter` detects each file's schema from its header and
maps both onto one common internal record, so the classifier reads a single
`narrative_text` field regardless of source year.

## Generated output files (not source data)

| File | What it is |
|------|-----------|
| `verified_bike_lane_obstructions.csv` | **The pipeline output.** One row per verified bike-lane obstruction (`case_id, case_topic, service_name, closure_comments, longitude, latitude`), combined across all input files. 28 rows from the three exports above. |
| `processing.log` | Per-record exclusion log from the most recent run (gitignored). Each line records a dropped record and the reason it was excluded. |

## Project layout

- `bike_lane_filter/` — the pipeline package (`filter.py` holds the data models,
  schema adapter, classification gates, reader/writer, reporter, and the
  `run_filter` entry point).
- `tests/` — pytest suite, including Hypothesis property tests and end-to-end
  integration/smoke tests.
- `.kiro/specs/bike-lane-obstruction-filter/` — requirements, design, and the
  implementation plan.

## Running

Requires Python 3 and the dev dependencies in `requirements-dev.txt`
(`pytest`, `hypothesis`).

Run the filter (defaults to the 2024 file; pass `--source` one or more times to
combine years):

```
python -m bike_lane_filter --source tmp7o3lwr4m.csv \
  --source lagan_311_open_data_2025_rvsd3.csv \
  --source tmpch36ak7j.csv \
  --output verified_bike_lane_obstructions.csv \
  --log processing.log
```

Run the tests:

```
python -m pytest
```

## How it classifies

Each record passes through four ordered gates; the first that rejects it owns
the outcome, and only records clearing all four are written:

1. **Bike-lane context** — the text must mention a bike lane.
2. **Unrelated** — drops stolen/abandoned bikes and bike racks.
3. **Verification** — the narrative must confirm an obstruction was present or
   addressed (removed / cleared / swept / ticketed); negative or "already clear"
   findings are excluded, with negative signals taking precedence.
4. **Valid coordinates** — longitude/latitude must be numeric and within Boston
   bounds.

The end-of-run summary reports total read, total verified, excluded counts by
reason, and a per-file verified breakdown.


---

## Web app (GitHub Pages)

The `docs/` folder is a self-contained static web app. It shows all crash and
obstruction data on an interactive Google Map and lets users plan a cycling
route between any two Boston addresses.

### File structure

```
docs/
├── index.html          — page shell, loads Maps SDK
├── style.css           — layout and component styles
├── app.js              — all map, marker, and routing logic
└── data/
    ├── dispatch.json   — 1 000 crash-dispatch records (generated from CSV)
    └── obstructions.json — 28 verified bike-lane obstruction records
```

### One-time setup: add your API key

1. Open `docs/index.html`.
2. Find the `<script src="https://maps.googleapis.com/maps/api/js?key=YOUR_API_KEY…">` tag near the bottom.
3. Replace `YOUR_API_KEY` with your Google Maps API key.
4. In [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials), add an **HTTP referrer restriction**:
   ```
   https://YOUR_GITHUB_USERNAME.github.io/*
   ```
   This prevents the key from being used by other sites even though it is visible in the HTML source.

**Never commit a `.env` file** — the `.gitignore` already blocks `*.env` and `API_KEY.env`.

### Regenerate the data JSON (after updating CSVs)

```bash
python3 - <<'EOF'
import csv, json

dispatch = []
with open("boston_dispatch_data.csv") as f:
    for row in csv.DictReader(f):
        try:
            dispatch.append({"id": row["_id"], "date": row["dispatch_ts"],
                "mode": row["mode_type"], "type": row["location_type"],
                "street": row["street"], "xstreet1": row["xstreet1"],
                "xstreet2": row["xstreet2"],
                "lat": float(row["lat"]), "lng": float(row["long"])})
        except (ValueError, KeyError): pass

obstructions = []
with open("verified_bike_lane_obstructions.csv") as f:
    for row in csv.DictReader(f):
        try:
            obstructions.append({"id": row["case_id"], "topic": row["case_topic"],
                "service": row["service_name"], "comment": row["closure_comments"],
                "lat": float(row["latitude"]), "lng": float(row["longitude"])})
        except (ValueError, KeyError): pass

with open("docs/data/dispatch.json", "w") as f:
    json.dump(dispatch, f, separators=(",", ":"))
with open("docs/data/obstructions.json", "w") as f:
    json.dump(obstructions, f, separators=(",", ":"))
print(f"Wrote {len(dispatch)} dispatch + {len(obstructions)} obstruction records.")
EOF
```

### Deploy to GitHub Pages

1. Push this repo to GitHub.
2. Go to **Settings → Pages** in your repository.
3. Under *Source*, choose **Deploy from a branch**.
4. Set branch to `main` (or whatever your default branch is) and folder to `/docs`.
5. Click **Save**. GitHub will publish the site at:
   ```
   https://YOUR_GITHUB_USERNAME.github.io/REPO_NAME/
   ```

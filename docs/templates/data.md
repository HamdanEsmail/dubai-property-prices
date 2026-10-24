# Data: where it comes from and how it is cleaned

Everything in this project is built from the Dubai Land Department (DLD) open-data
Transactions dataset, fetched on 24 October 2026. This page records exactly what
was fetched, what each column means, every cleaning rule with the number of rows
it removed, and the quirks found along the way. All counts below are written by
a script from `data/processed/clean_report.json`, so they match the artefacts.

## Source

DLD publishes transactions at
<https://dubailand.gov.ae/en/open-data/real-estate-data/> (the "Transactions" tab).
The page is driven by a JSON endpoint that answers without an account:

```
POST https://gateway.dubailand.gov.ae/open-data/transactions
Content-Type: application/json
Origin: https://dubailand.gov.ae
Referer: https://dubailand.gov.ae/en/open-data/real-estate-data/

{"P_FROM_DATE":"01/01/2026","P_TO_DATE":"10/25/2026","P_GROUP_ID":"1",
 "P_IS_OFFPLAN":"","P_IS_FREE_HOLD":"","P_AREA_ID":"","P_USAGE_ID":"",
 "P_PROP_TYPE_ID":"","P_TAKE":"2000","P_SKIP":"0","P_SORT":"TRANSACTION_NUMBER_ASC"}
```

Dates are `MM/DD/YYYY`. `P_GROUP_ID` is `1` for sales, `2` for mortgages and `3` for
gifts. A page holds up to 2,000 rows and every row carries `TOTAL`, the number of
rows matching the query, which the client uses to check that nothing was missed.

Three facts about the endpoint shaped the pipeline:

1. **It serves the current calendar year only.** Every query for 2025 or earlier
   returned an empty result on 24 October 2026. Earlier years are in the
   `dld_transactions` bulk file on [data.dubai](https://data.dubai/), which needs
   a UAE Pass login. Dropping that CSV into `data/raw/pulse/` merges it through the
   same cleaning code (`parse.frame_from_pulse_csv`); the mapper was written from the
   file's published column list and has not yet been exercised on a real download.
2. **Its date filter is not a clean day boundary.** Fetching 2026 month by month
   returned 627 fewer rows than one request for the whole year, all of them dated
   on the first or last day of a month (418 on 31 July alone). The client therefore
   fetches one window per download and sends the day after the requested end date
   as `P_TO_DATE`, then compares the rows received with `TOTAL`.
3. **It is slow but tolerant.** A 2,000-row page takes 3 to 10 seconds. The client
   makes one request at a time with a one-second pause, retries 429 and 5xx with
   exponential backoff, and caches every page on disk so an interrupted download
   resumes where it stopped. A full refresh is about 90 requests.

### What was fetched

| Group | API `TOTAL` | Rows in the snapshot | Used |
|---|---:|---:|---|
| Sales | 132,562 | {{clean.raw_rows|,}} (`dld_sales_2026.parquet`) | yes, this is the dataset |
| Mortgages | 38,569 | {{export.mortgage_rows|,}} (`dld_mortgages_2026.parquet`; portfolio mortgages repeat identical rows, which the parser collapses) | no, kept for a future question |
| Gifts | 7,501 | not fetched | no |

Window: 1 January to 24 October 2026 inclusive. The snapshot is the typed,
English-only copy of the raw rows with no cleaning applied; `uv run dubai-prices
build` turns it into `data/processed/sales_clean.parquet`.

## Licence

The dataset is covered by the [Dubai Open Data Licence](https://data.dubai/documents/d/guest/open-data-license-english-1-1-pdf)
(linked from the [data.dubai terms](https://data.dubai/en/terms-conditions)). In short:

- A worldwide, royalty-free, non-exclusive licence to use the information for
  commercial and non-commercial purposes (clause 2.1).
- Attribution is required: name the originator and link to the original material
  (clause 3.2.1). This project credits the Dubai Land Department on every page.
- No implied endorsement: a work product must not be associated with DLD or the
  Government of Dubai (clause 3.2.2). This project is independent.
- Adapted material, meaning modified data, must be made available under the same
  licence (clause 3.3.1). The snapshot and the cleaned table in this repository are
  therefore redistributed under the Dubai Open Data Licence, not under the MIT
  licence that covers the code.
- The original information may not be sold (clause 3.6), and it is provided as is
  with no warranty (clause 4.1).

## Columns

The API returns 52 fields. Arabic duplicates (`*_AR`) are dropped, and the numeric
id fields (`AREA_ID`, `PROCEDURE_ID`, `PROPERTY_ID`, ...) are all zero in the public
feed, so names are the keys. The tidy table keeps these:

| Column | API field | Notes |
|---|---|---|
| `transaction_number` | `TRANSACTION_NUMBER` | e.g. `102-1-2026`; the prefix is the procedure code |
| `date` | `INSTANCE_DATE` | registration timestamp, ISO 8601 |
| `group`, `procedure` | `GROUP_EN`, `PROCEDURE_EN` | Sales / Mortgage / Gifts, and the procedure within the group |
| `is_offplan`, `is_freehold` | `IS_OFFPLAN`, `IS_FREE_HOLD` | 1/0 flags |
| `usage` | `USAGE_EN` | Residential / Commercial |
| `area` | `AREA_EN` | community, title-cased by the cleaner |
| `property_type`, `property_subtype` | `PROP_TYPE_EN`, `PROP_SB_TYPE_EN` | Unit / Flat, Building / Villa, Land / ... |
| `rooms` | `ROOMS_EN` | Studio, `1 B/R` ... `7 B/R`, PENTHOUSE, NA |
| `parking` | `PARKING` | free text, see below |
| `building_age` | `BUILDING_AGE` | published as 0 on every 2026 row |
| `project`, `master_project` | `PROJECT_EN`, `MASTER_PROJECT_EN` | master project is empty on {{clean.profile.missing_share.master_project|pct1}} of rows |
| `nearest_metro`, `nearest_mall`, `nearest_landmark` | `NEAREST_*_EN` | missing on {{clean.profile.missing_share.nearest_metro|pct}}, {{clean.profile.missing_share.nearest_mall|pct}} and {{clean.profile.missing_share.nearest_landmark|pct}} of rows |
| `value_aed` | `TRANS_VALUE` | registered sale value in AED |
| `procedure_area_sqm`, `actual_area_sqm` | `PROCEDURE_AREA`, `ACTUAL_AREA` | both in square metres; they agree on {{clean.profile.procedure_area_equals_actual_share|pct}} of rows |
| `source` | | `dld-api`, or `pulse` for rows merged from the bulk file |

## What the raw sales rows contain

{{clean.profile.rows|,}} rows, {{clean.profile.offplan_share|pct}} of them off-plan.
Property types:

{{table:property_types}}

The Sales group contains more than straightforward sales. Median price per square
metre by procedure, for residential flats and villas with a positive price and area:

{{table:procedures}}

Development registrations and "Sell Development" transfers sit at roughly half the
market price per square metre, and lease-to-own contracts are rent-to-own
agreements rather than sales, so only the four procedures marked "kept" are used.

## Cleaning rules

Rules run in this order. Each one is a `CleanStep` in `src/dubai_prices/clean.py`
and the counts come from the last build.

{{table:steps}}

Notes on the rules that involve a judgement call:

- **Residential flats and villas only.** Villas are published as property type
  "Building" with sub-type "Villa"; flats as "Unit" / "Flat". Land, offices, shops,
  hotel apartments, hotel rooms and whole buildings are different markets with
  different units of value, so they are out of scope rather than cleaned.
- **Bulk deals.** When a developer sells several units in one transaction, the API
  repeats the transaction number on every unit and puts the whole deal's value on
  each row, so the price per square metre of each unit looks like the price of the
  portfolio. Every row whose transaction number appears more than once is dropped.
  Portfolio deals that were registered against a single unit are caught by the price
  ceiling instead.
- **Size bounds.** Flats of 20 to 1,000 sqm and villas of 50 to 3,000 sqm. The 2026
  distribution has flats from 21 sqm up to a 2,916 sqm outlier and villas from 51 to
  5,396 sqm; the bounds remove entries that are almost certainly whole floors,
  plots or typing errors without touching the real tails.
- **Price per square metre bounds.** 3,000 to 120,000 AED per sqm. Below 3,000 are
  nominal transfers and data errors (sales recorded at 163 AED, for example); the
  cheapest genuine market in Dubai sells at roughly 6,000 to 8,000 AED per sqm.
  Above 120,000 per sqm are portfolio deals recorded against one unit. The bounds
  remove {{clean.steps.price_per_sqm_bounds.dropped|,}} rows, about
  {{clean.steps.price_per_sqm_bounds.dropped_share|pct}} of the residential rows.
- **Procedure area as a fallback.** The bulk file only has `procedure_area`; when
  `actual_area` is missing it is used instead. In the API data the two agree on
  {{clean.profile.procedure_area_equals_actual_share|pct}} of rows.

## Derived columns

- `price_per_sqm` = `value_aed` / `actual_area_sqm`.
- `ym` (`2026-03`) and `month_n` (3) from the registration date.
- `rooms_n`: Studio is 0, `N B/R` is N, PENTHOUSE and NA are missing.
- `parking_n`: the `PARKING` field is free text. A bare integer is taken as a
  count ("1", "2"); anything else is a list of bay labels separated by commas or
  runs of spaces ("B1-12, B1-13", "1  0", "EU  0") and the number of labels is the
  count; missing is 0; the count is capped at 10 because a few rows list a whole
  floor. This is an approximation and the model treats it as a weak signal.
- `area`: community names arrive in mixed case ("BUSINESS BAY" and "Business Bay"
  are {{clean.profile.area_case_variants}} such pairs), so they are title-cased
  into one key, keeping roman numerals and codes as they are ("Arabian Ranches II").

## Quirks worth knowing

- `BUILDING_AGE` is 0 on {{clean.profile.building_age_zero_share|pct}} of rows, so
  it carries no information and is not a model feature.
- `MASTER_PROJECT_EN` is empty on {{clean.profile.missing_share.master_project|pct1}}
  of rows and `PROJECT_EN` on {{clean.profile.missing_share.project|pct1}}.
- There are only {{clean.profile.distinct.nearest_mall}} distinct nearest malls and
  {{clean.profile.distinct.nearest_landmark}} landmarks, against
  {{clean.profile.distinct.nearest_metro}} metro stations (with a few spelling
  variants such as "Jumeirah Beach Resdency"), {{clean.profile.distinct.area|,}}
  communities and {{clean.profile.distinct.project|,}} projects.
- `ROOMS_EN` is missing or "NA" mostly on land and commercial rows; among flats
  and villas it is nearly always filled.
- Off-plan sales ("Sell - Pre registration") are registered at contract time; a
  unit sold in 2026 may complete years later. The registration date is what the
  model sees.
- The feed has no unit identifier, so the same unit resold twice in the year
  appears as two independent rows, which is the right treatment for a price model.

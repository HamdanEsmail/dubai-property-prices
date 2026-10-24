# Dubai property prices

What does a flat or a villa sell for per square metre in each Dubai community, and
how sure can we be? This project pulls every sale registered with the Dubai Land
Department (DLD) in 2026, cleans it, fits a price model that is scored on months it
never saw, and ships the result as a small static explorer that runs in the browser.

**Explorer:** <https://hamdanesmail.github.io/dubai-property-prices/>

[![The explorer on a desktop screen](docs/screenshots/explorer-desktop.png)](https://hamdanesmail.github.io/dubai-property-prices/)

| | |
|---|---|
| Sales used | 109,706 residential flats and villas, 1 January to 24 October 2026, in 147 communities |
| Source | DLD open data, Transactions (132,562 raw sales rows), fetched 24 October 2026 |
| Validation | fitted on January to August (93,377 sales), scored on September to 24 October (16,329 sales) |
| Baseline error | median by community, type and rooms: 10.9% median absolute error per sqm |
| Model error | gradient boosting: 7.8% median absolute error per sqm (2,205 AED per sqm MAE) |
| 80% interval | covers 78% of the later sales after calibration on August (74% before) |

## The question

Dubai has a public register of every property sale, but what people actually see
are asking prices. A student renting or buying wants two things: the range that
units like theirs really sold for recently, and a sanity check on a specific
property. The first is a lookup; the second needs a model, and a model is only
useful if it is honest about its error on data it has not seen.

## The data

DLD publishes registered transactions on its open-data page, and the page is fed by
a JSON endpoint that answers without an account. It serves the current calendar
year only, so this project covers 1 January to 24 October 2026:
132,562 sales rows, plus 38,187 mortgage rows that are
snapshotted but not used yet. The pipeline is written so that the multi-year bulk
file from data.dubai (behind a UAE Pass login) can be dropped into `data/raw/pulse/`
and merged through the same cleaning code.

Two things about the feed are worth knowing before trusting any number from it.
Its date filter is not a clean day boundary: fetching month by month lost 627 rows
at month edges, so the client fetches the whole year in one window and checks the
count against the total the API reports. And some fields are published but empty:
building age is 0 on every row, and the nearest metro, mall and landmark are
missing on roughly half of them.

The data is used under the [Dubai Open Data Licence](https://data.dubai/documents/d/guest/open-data-license-english-1-1-pdf),
which allows commercial and non-commercial use with attribution, requires adapted
data to be shared under the same licence (the snapshot in this repository is), and
forbids implying any association with DLD or the Government of Dubai. Full detail,
including the endpoint, every column and every quirk found: [docs/data.md](docs/data.md).

## Cleaning

Each rule is a named step with a plain-English description, and the build prints
how many rows it removed. In order:

| # | Rule | Dropped | Remaining |
|---|---|---:|---:|
| 1 | `sales_only`: Keep the Sales group only (mortgages and gifts are separate groups). | 0 | 132,562 |
| 2 | `sale_procedures`: Keep the four sale procedures (Sale, Sell - Pre registration, Delayed Sell, Sale On Payment Plan). Drop lease-to-own contracts, development registrations and land additions, which are not market sales. | 3,782 | 128,780 |
| 3 | `exact_duplicates`: Drop rows that repeat another row exactly (same page served twice). | 0 | 128,780 |
| 4 | `residential_flat_villa`: Keep residential flats and villas only. Drop land, offices, shops, hotel apartments, hotel rooms and whole buildings. | 18,356 | 110,424 |
| 5 | `missing_value_or_area`: Drop rows with a missing or zero price or a missing or zero built-up area (procedure area stands in when built-up area is missing, as in the bulk file). | 0 | 110,424 |
| 6 | `bulk_deals`: Drop bulk deals: rows that share a transaction number carry the whole portfolio price on every unit. | 42 | 110,382 |
| 7 | `area_bounds`: Keep flats of 20 to 1000 sqm and villas of 50 to 3000 sqm. | 43 | 110,339 |
| 8 | `price_per_sqm_bounds`: Keep 3,000 to 120,000 AED per sqm. Below that are nominal transfers and data errors; above it are portfolio deals recorded against one unit. | 633 | 109,706 |

The judgement calls, in short. The Sales group includes lease-to-own contracts and
developer "development registration" transfers, which sit at about half the market
price per square metre, so only the four straightforward sale procedures are kept.
Bulk deals repeat one transaction number across many units with the whole deal's
value on every row, which would make each unit look like it sold for the price of
the portfolio. The size and price-per-sqm bounds were set after looking at the
distribution: they remove nominal transfers (sales recorded at a few hundred
dirhams) and portfolio deals registered against one unit, without touching the real
tails of the market. Reasoning for each rule: [docs/data.md](docs/data.md#cleaning-rules).

![Median price per square metre by month](docs/charts/monthly_price_per_sqm.png)

![Median price per sqm in the busiest communities](docs/charts/communities_top.png)

## The model

**Target.** Log of price per square metre. Modelling the log makes a 10% error
cost the same on a 600,000 AED studio as on a 6,000,000 AED villa, and the
prediction is turned back into AED per sqm and multiplied by the size.

**Features.** Community, property sub-type (flat or villa), rooms, size (log sqm),
off-plan or ready, freehold, parking count, nearest metro, mall and landmark, and
the month of the year. Building age is published as zero on every row, so it is
left out. Project name is deliberately not a feature: the explorer asks for a
community, not a building, and the model should answer the question the user can
ask.

**Two models, so the gain is visible.**

- *Baseline:* the median price per sqm of 2026 sales in the same community, sub-type
  and room count, falling back to community and sub-type, then sub-type. This is
  what a careful person with a spreadsheet would do.
- *Model:* LightGBM gradient boosting, 496 trees of up to 31
  leaves, with the categorical features handled natively.

**Validation is temporal, not random.** The model is fitted on sales registered
from January to August and scored on September to 24 October. A random split would
let the model see September prices while predicting September and would flatter it.
Within the training window, August is held out once more to choose the number of
trees (early stopping), and then the model is refitted on all eight months.

**The interval.** Two more LightGBM models fit the 10th and 90th percentiles of log
price per sqm. On their own they were too narrow: fitted on January to July, they
covered 76% of August sales rather than 80%. So the
band is widened by a constant in log space chosen on August (a split-conformal step,
0.9% each way) and then checked on the test months, which
it never saw.

### Results on September to 24 October 2026 (16,329 sales)

| | Baseline | Model |
|---|---:|---:|
| Median absolute error, AED per sqm | 10.9% | 7.8% |
| Mean absolute error, AED per sqm | 3,129 | 2,205 |
| Median absolute error, total price | 10.9% | 7.8% |
| Mean absolute error, total price (AED) | 343,796 | 249,340 |
| 80% interval, share of sales covered | | 78% (raw 74%) |
| Median interval width, as a share of the estimate | | 32% |

By segment:

| Segment | Test sales | Baseline MdAPE | Model MdAPE | Model MAE (AED/sqm) | 80% interval coverage |
|---|---:|---:|---:|---:|---:|
| Flat | 15,061 | 10.8% | 7.7% | 2,190 | 78% |
| Villa | 1,268 | 11.6% | 8.5% | 2,391 | 76% |
| off-plan | 11,422 | 9.0% | 6.9% | 2,031 | 76% |
| ready | 4,907 | 16.3% | 10.3% | 2,612 | 81% |

![Actual against predicted price per sqm on the test months](docs/charts/actual_vs_predicted.png)

![Error by segment](docs/charts/error_by_segment.png)

![Interval coverage by predicted price](docs/charts/interval_coverage.png)

**What the model leans on.** Community carries most of the signal, then size, then
whether the unit is off-plan. The month feature matters little, which is reassuring
for a model that has to extrapolate past August: it is not riding a trend.

| Feature | Share of split gain |
|---|---:|
| `area` | 65.8% |
| `log_area` | 10.1% |
| `is_offplan` | 6.6% |
| `nearest_metro` | 6.5% |
| `nearest_landmark` | 5.7% |
| `parking_n` | 1.4% |
| `month_n` | 1.4% |
| `rooms_n` | 1.2% |
| `rooms` | 0.7% |
| `property_subtype` | 0.6% |
| `nearest_mall` | 0.1% |
| `is_freehold` | 0.0% |

![Share of split gain by feature](docs/charts/feature_importance.png)

**Where it fails.** The eight worst test-month predictions, by percentage error:

| Registered | Community | Unit | Registered price | Registered AED/sqm | Model AED/sqm (80% band) | Error |
|---|---|---|---:|---:|---:|---:|
| 2026-10-12 | Palm Jumeirah | 4 B/R flat, 452 sqm, ready | 2,500,000 | 5,533 | 28,072 (20,848 to 49,384) | 407% |
| 2026-09-25 | Burj Khalifa | 2 B/R flat, 184 sqm, off-plan | 1,480,000 | 8,053 | 40,275 (25,576 to 42,307) | 400% |
| 2026-09-02 | Hadaeq Sheikh Mohammed Bin Rashid | 2 B/R flat, 101 sqm, off-plan | 548,269 | 5,433 | 25,820 (22,232 to 30,249) | 375% |
| 2026-09-03 | Burj Khalifa | 2 B/R flat, 103 sqm, ready | 752,800 | 7,292 | 29,685 (21,608 to 39,881) | 307% |
| 2026-09-25 | Jumeirah Beach Residence | 3 B/R flat, 182 sqm, ready | 1,218,464 | 6,678 | 26,159 (14,419 to 41,566) | 292% |
| 2026-10-08 | Dubai Creek Harbour | 1 B/R flat, 64 sqm, ready | 400,000 | 6,294 | 24,348 (22,004 to 28,910) | 287% |
| 2026-09-08 | Dubai Hills | 3 B/R flat, 212 sqm, ready | 1,422,935 | 6,698 | 25,673 (18,434 to 30,769) | 283% |
| 2026-10-20 | Meydan Avenue | 2 B/R flat, 87 sqm, ready | 486,220 | 5,575 | 21,083 (14,508 to 21,201) | 278% |

Every one of them is a sale registered at a fraction of what anything in that
community sells for: a four-bedroom flat on Palm Jumeirah at 2.5 million dirhams,
a two-bedroom flat next to Burj Khalifa at 752,800, a one-bedroom flat in Dubai
Creek Harbour at 400,000. These are almost certainly not market sales (transfers
between related parties, part shares, or payment-plan instalments registered as
the sale value) but they clear the 3,000 AED per sqm floor, and the feed carries
no flag for them. The model cannot tell them apart from a bargain. A tighter rule,
such as dropping sales below a third of the community median, would remove them,
but choosing it after seeing the test set would be cheating, so they stay and the
numbers above include them. The big errors are one-sided: of the
407 test sales with an error above 50%, 93% are
cases where the registered price sits *below* the estimate, which is what you would
expect if the outliers are under-registered values rather than model blind spots.
Across all test sales the estimate is above the registered price
49% of the time, so there is no general bias.

On a separate point, the three models are fitted independently, so the point
estimate landed outside its own 80% band on 1.6% of test
rows before a final rule that always widens the band to include it. The coverage
figures above include that rule.

**Size against accuracy.** The model is shipped to the browser as a plain JSON dump
of its trees (1.39 MB for the point model and the two interval
models together) and evaluated by about seventy lines of JavaScript, so the explorer
needs no server and no machine-learning runtime. A larger configuration (63 leaves,
1,260 trees, reproducible with `uv run dubai-prices train --params large
--models-dir models/large`) scores 7.2% median absolute error instead
of 7.8%, but its trees weigh 3.5 MB against
1.39 MB. Twice the download for 0.6 points of error
was the wrong trade for a page meant to open on a phone, so the smaller one is shipped
and the larger one's metrics are kept in `models/large/metrics.json`.

## The explorer

Pick a community, flat or villa, rooms and size. The page shows the model's
estimate with its 80% range, the range that recent sales like this one actually
closed in, the most recent comparable sales behind that range, and the community's
price per square metre by month against all of Dubai. When fewer than five sales
match the exact type and room count it says so and shows the wider community
range instead of a confident number.

[![The explorer on a phone](docs/screenshots/explorer-phone.png)](https://hamdanesmail.github.io/dubai-property-prices/)

A visitor knows the community but not the building, and inside a community the
nearest metro, mall and landmark behave like a sub-location: for a ready two-bedroom
flat in Dubai Marina, the highest of the six most common landmark profiles gives an
estimate 113% above the lowest. So the page
does not guess one "typical" building; it averages the model (in log space) over
the most common nearby-landmark profiles among that cell's own sales. Scored that
way on the test months, as if the building were unknown for every sale, the median
error is 8.3% (against 7.8%
when the building's landmarks are known) and the 80% range covers
76% of sales. Those are the numbers the page quotes
for itself.

Everything is precomputed: per-community statistics, comparables and the
landmark profiles are JSON files under `web/data/`, and the model is the tree dump
described above. There is no server, no tracking and no build step; GitHub Pages
serves the `web/` folder as is.

## Reproduce

Three commands rebuild the cleaned dataset, the models, the charts and the
explorer's data from the committed snapshot:

```bash
git clone https://github.com/HamdanEsmail/dubai-property-prices.git && cd dubai-property-prices
uv sync
uv run dubai-prices all
```

`uv run dubai-prices fetch` refreshes the raw data from the DLD endpoint (about 90
polite requests; resumable), `uv run pytest` runs the tests, and `make all` wraps
the three steps for people who prefer make. The LightGBM run is deterministic, so
the numbers in `models/metrics.json` should match this page; the page itself is
generated from those artefacts by `uv run python scripts/fill_docs.py`, which fills
the templates under `docs/templates/`, so no figure here was typed by hand.

```
src/dubai_prices/   api (client) -> fetch (cache) -> parse -> clean -> build
                    features -> model -> export_model (browser trees) -> export_web -> charts
tests/              fixture pages, no network
data/snapshot/      typed raw rows, Dubai Open Data Licence
data/processed/     cleaned table and clean_report.json (built, not committed)
models/             point.txt, q10.txt, q90.txt, metrics.json, failures.csv
web/                the explorer: index.html, js/, css/, data/
docs/               data.md, charts/, screenshots/
```

## Limitations

- **One year of data.** The feed serves the current year only. Seasonality and
  year-on-year change are invisible, and a year with a strong trend would stretch
  the model more than 2026 did.
- **Registration dates, not contract dates.** Off-plan sales are registered when
  the contract is, and the unit may complete years later. Off-plan prices include
  what buyers pay for that wait.
- **No building identity.** The model sees community, not building. Two towers in
  the same community can differ by more than the interval; the comparables table
  is there for exactly that reason.
- **The interval is calibrated on one month.** August may not resemble every future
  month. The reported coverage is what happened on September to October.
- **Approximate parking and missing landmarks.** Parking is parsed from free text,
  and nearest metro, mall and landmark are missing on about half the rows; the
  model treats "missing" as its own category.
- **Not a valuation.** Registered prices can include furniture packages, payment
  plans and related-party deals that the feed does not flag.

## Licence and credit

Code is released under the MIT licence (see `LICENSE`). Data: Dubai Land Department,
Open Data, Transactions, <https://dubailand.gov.ae/en/open-data/real-estate-data/>,
used and redistributed under the Dubai Open Data Licence. This project is
independent and is not affiliated with or endorsed by the Dubai Land Department or
the Government of Dubai.

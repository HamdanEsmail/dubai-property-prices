# Data snapshot

`dld_sales_2026.parquet` and `dld_mortgages_2026.parquet` are the typed, English-only
copies of every row the Dubai Land Department open-data Transactions endpoint
returned for 1 January to 24 October 2026 (see `docs/data.md` for the field list).
No cleaning has been applied; `uv run dubai-prices build` turns them into
`data/processed/sales_clean.parquet`.

Source: Dubai Land Department, Open Data, Transactions
(https://dubailand.gov.ae/en/open-data/real-estate-data/), used and redistributed
under the Dubai Open Data Licence
(https://data.dubai/documents/d/guest/open-data-license-english-1-1-pdf).
Data is provided as is; this project is not affiliated with or endorsed by the
Dubai Land Department or the Government of Dubai.

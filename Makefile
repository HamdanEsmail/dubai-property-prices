# One-command reproduction. On Windows without make, run the uv commands directly.
.PHONY: all data model web test fetch

all: data model web

data:        ## parse the committed snapshot (or data/raw if present) and clean it
	uv run dubai-prices build

model:       ## fit baseline + LightGBM with temporal validation into models/
	uv run dubai-prices train

web:         ## write web/data JSON, the browser model and docs/charts
	uv run dubai-prices export

test:
	uv run pytest -q

fetch:       ## refresh data/raw from the DLD open-data API (polite, resumable)
	uv run dubai-prices fetch

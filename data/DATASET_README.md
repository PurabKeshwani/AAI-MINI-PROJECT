# PS-08 Dataset

Prepared for SupplyMind by Purab Keshwani (26), Khushi Dayaramani (14),
and Sahil Jethnani (21), GCR7, VESIT, academic year 2026-27.

Source: Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning
Repository. https://doi.org/10.24432/C5BW33. Licensed CC BY 4.0.

Download source: https://archive.ics.uci.edu/static/public/352/online+retail.zip

The original source contains 541909 rows. This prepared subset retains five
fixed SKUs and UK transactions from 2011-01-01 through 2011-11-30. Cancellations,
nonpositive quantities/prices and 39 exact duplicate rows are excluded. The
remaining 7980 transaction rows aggregate into 5010 daily product-location rows.

The 3 warehouse allocations use 50/30/20 shares with rounding into the last
location, conserving the daily product total. Zero-activity days receive zero.

ACTUAL: SKU, product description, source daily positive sales.
SIMULATED: warehouse allocation, stock, storage capacity, supplier names and
records, lead times, reliability, order constraints and INR prices/costs.
INR prices are scenario quotes, not conversions of original sterling prices.

## Files and fields

sales.csv: date (ISO), sku (string), warehouse (string), units (nonnegative
integer), weekday (Monday 0 through Sunday 6). 5010 rows.

products.csv: sku, name, selling_price_inr, holding_cost_day_inr,
stockout_penalty_inr. 5 rows. Prices and costs are simulated.

inventory.csv: sku, warehouse, on_hand, reserved, capacity. 15 rows.

vendors.csv: vendor_id, name, sku, unit_price_inr, lead_days, reliability,
min_qty, lot_size, max_qty, shipping_inr, discount_threshold, discount_rate.
15 rows. Reliability is a scenario value in [0,1]. Rates are fractions.

metadata.json: complete source attribution, source ZIP SHA-256, filters,
field origin and limitations.

## Validation and limits

Daily keys (date, sku, warehouse) are unique and dates are continuous. All
daily units are nonnegative integers. No customer or invoice identifiers are
included. Observed sales do not prove unconstrained demand. Missing activity
can reflect closure or unknown stockouts. Promotions, weather, supplier
performance and theft labels are unavailable. This small subset illustrates
the agent workflow and is not a representative operational deployment.

To rebuild, run prepare_data.py from the implementation package. The raw Excel
is intentionally not redistributed here; the official download remains above.

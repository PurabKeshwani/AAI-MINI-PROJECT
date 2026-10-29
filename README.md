# SupplyMind

## PS-08 Supply Chain and Inventory Management Agent

AAI & EA mini project, VESIT, 2026-27. Group GCR7, D17 B, Batch A.

- Purab Keshwani, roll 26
- Khushi Dayaramani, roll 14
- Sahil Jethnani, roll 21

SupplyMind is an executable local prototype with eleven cooperating decision
agents, demand forecasting, stockout alerts, warehouse transfers, constrained
vendor selection, SQLite purchase orders and a browser dashboard.

## Run on Windows

Install Python 3.11 or newer. Extract this folder, open a terminal inside it:

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:8000 in Chrome or Edge. Prepared CSV files are included,
so no download, API key, paid service or retraining is required for the demo.
On macOS/Linux use `python3 -m venv .venv` and `source .venv/bin/activate`.

## Five minute demo

1. Click **Reset demo**, then **Run agent cycle**.
2. Inspect the risk watch, warehouses and forecast chart.
3. Open **Vendor decisions** and explain price versus shortage-cost exposure.
4. Open **Purchase orders**, export CSV, then run the same cycle again.
   The application blocks duplicate execution for the same simulation date.
5. Click **Advance 1 day** and run again. Incoming transfers and POs arrive on
   their ETA and increase on-hand stock. Demand on future demo days is simulated
   from the displayed forecast, not newly collected real sales.
6. Reset. Set demand to 1.6 and vendor delay to 2 days, then run again.
7. Inspect **Agent trace** and **Evaluation**.
8. **Start auto loop** alternates a cycle and one simulated day every six seconds.
   Keep this browser tab open. Stop the loop before changing scenarios or resetting.

Set scenario controls before the day's first cycle. A second cycle on that day
is blocked even if scenario inputs change. Reset or advance to replan.

## Reproduce results

```shell
python -m unittest discover -s tests -v
python evaluate.py
```

Rebuild the source data only if necessary:

```shell
python prepare_data.py
```

This downloads the 22.6 MB source Excel file from UCI. The included operational
CSVs contain no customer identifiers, invoice identifiers or personal records.

## Files

- `agents.py`: forecast functions, Context blackboard, ten specialist agents and orchestrator
- `app.py`: local HTTP API, SQLite adapter, day advancement and idempotency
- `static/index.html`: responsive browser dashboard
- `data/`: attributed sales data and explicitly simulated operational tables
- `evaluate.py`: chronological holdout and historical inventory replay
- `results/`: reproducible CSV/JSON evaluation outputs
- `tests/test_system.py`: eight meaningful functional checks
- `DEMO_PREVIEW.html`: offline read-only dashboard snapshot, if included

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Snapshot, forecasts, evaluations, orders and trace |
| POST | `/api/cycle` | Plan and persist simulated transfers and purchase orders |
| POST | `/api/advance` | Advance one day, receive arrivals, consume simulated demand |
| POST | `/api/reset` | Restore the demonstration state |
| GET | `/api/orders.csv` | Export purchase and transfer orders |

Cycle JSON supports `multiplier` from 0.5 to 3.0 and `delay` from 0 to 5 days.
The API binds only to localhost. It has no authentication and is not suitable
for public deployment. Reset deletes the local demonstration ledger.

## Data provenance and limits

Actual demand proxy: positive, non-cancelled UK sales for five fixed UCI Online
Retail SKUs, 1 January to 30 November 2011. Exact duplicate transaction rows
are removed. Sales aggregate by day and SKU; zero-activity dates are filled
with zero. The 50/30/20 warehouse split conserves each source daily total.
**Warehouse assignments, inventory, capacity, suppliers, reliability, lead times,
INR quotes, shipping, penalties and holding costs are simulated assumptions.**
The original prices are sterling. Demo INR quotes are invented scenarios, not
a currency conversion. No promotion, weather, theft or real stockout labels
exist in this data. A statistical anomaly cannot establish theft or its cause.

Attribution: Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning
Repository. https://doi.org/10.24432/C5BW33. CC BY 4.0.

## What autonomy means here

The agents autonomously update a **local simulation ledger**. They do not send
emails, contact suppliers, create real financial obligations or make payments.
No LLM or reinforcement learning is implemented. Ridge regression learns from
data; the remaining decision agents apply inspectable statistical and rule
policies. The architecture coordinates agents centrally through shared state,
with explicit ordering and audit messages.

Vendor selection minimizes an estimated cost among enumerated feasible vendor
options. It is not a global multi-period optimum. The INR 100000 budget applies
per purchase order, not globally. Safety stock uses a normal approximation and
seasonal-naive residual variation, without calibration of a 95% service level.

Forecast selection uses the 28 days before the held-out final 28 days. Test
forecasts are recursive. The 56-day policy replay retrains only on data before
each replay date. It uses identical initial stocks and realised sales for both
policies. Purchase spending and closing inventory differ. Operating cost excludes
purchase cost and is not profit. One replay cannot establish general superiority.

Prepared artifact versions may retain a source-data cutoff of 2011-11-30 while
the academic report date is 2026. These are different dates by design.

## Further work

Real ERP connectors, verified supplier histories, calibrated uncertainty,
aggregate procurement budgets, shelf-life modelling, inventory reconciliation,
authentication and a live supplier execution adapter remain future work.

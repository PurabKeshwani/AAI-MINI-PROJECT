# SupplyMind

## PS-08 Supply Chain and Inventory Management Agent

AAI & EA mini project, VESIT, 2026-27. Group GCR7, D17 B, Batch A.

- Purab Keshwani, roll 26
- Khushi Dayaramani, roll 14
- Sahil Jethnani, roll 21

SupplyMind is a local inventory and procurement workspace. Eleven cooperating
agents forecast demand, assess stockout risk, plan internal transfers and draft
orders against supplier terms. Operators release plans, record stock movements
and receive deliveries in SQLite.

## Run on Windows

Install Python 3.11 or newer. Extract this folder, open a terminal inside it:

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:8000 in Chrome or Edge. The operations workspace starts
empty on first use. It uses the host's calendar and stores data in
`operations.db`. Stop the server before copying this file for backup.
On macOS/Linux use `python3 -m venv .venv` and `source .venv/bin/activate`.

## Work with your inventory

1. Open **Data** and download the CSV templates. Import products,
   product locations, supplier terms and sales history. You can upload all four
   together or add them as they become available.
2. Review **Planning coverage** and set your INR per-order budget. Each ready
   location needs a supplier for its SKU and 28 calendar days of completed sales
   history, with the latest reported day within seven days. Missing dates inside
   the supplied interval are treated as zero sales. Record zero-unit days to mark
   the end of a reporting period when there were no sales.
3. Click **Plan replenishment**. Agents produce the forecast, risk evidence,
   supplier comparison and draft purchase or transfer orders. The plan uses the
   latest completed sales dates and current stock. Stock and open inbound units
   change after release or a recorded movement.
4. In **Orders**, review and release the plan. Confirm dispatch for any listed
   transfers, then export released purchase orders for your supplier workflow.
   Use **Receive** for partial or full deliveries and record the delivery reference.
   Confirm changes with the supplier before cancelling an open purchase balance.
5. Use **Record stock activity** to enter a sale or stock adjustment. A sale
   reduces available stock and contributes to demand history after that date
   closes. The activity ledger retains your reference and unit change.

Existing opening balances are preserved on reimport. Inventory CSV updates can
change reserved units and capacity. Sales CSVs extend completed daily history
without changing stock. Correct a physical balance through stock activity. Use
ISO dates (`YYYY-MM-DD`), decimal INR amounts and whole units. Supplier lead
times are 1 to 14 days, reliability and discount are fractions from 0 to 1, and
minimum quantity is a multiple of lot size. See [dataset fields](data/DATASET_README.md)
for the shared column definitions.

Imported terms, stock changes and receipts can supersede an unreleased draft.
Run planning again to incorporate the updated records. Transfer and purchase
drafts release together because vendor quantities depend on planned transfers.

## Five minute viva

Open http://127.0.0.1:8000/practice to use the same workflow with preloaded
historical records in a separate `practice.db`. The practice date is 1 December
2011. Use **Restore practice data** to return to starting balances.

1. Run a replenishment plan and open a flagged location. Walk through the
   forecast, projected shortage, supplier scores and draft quantities.
2. Open **Agent trace** to show the specialists handing evidence to the
   orchestrator. The trace records the agents that actually ran.
3. Release the plan, export a purchase order and receive part of an order.
   Open **Inventory** to verify the stock change and receipt reference.

The [scenario lab](http://127.0.0.1:8000/sample) is the separate PS-08 experiment
with demand and delay controls, simulated days and the historical evaluation.

1. In **Overview**, choose **Baseline**, confirm **Reset simulation** and click
   **Run agent cycle**. The workflow shows forecasts, replenishment proposals
   and committed orders from the run.
2. Open a product in the risk watch. Follow its forecast validation, stockout
   assessment, supplier costs and ledger orders in the decision details.
3. In **Agent trace**, expand the specialists to see how each decision was made.
   In **Orders**, export the ledger as CSV. Run the cycle a second time to show
   duplicate protection for the simulation date.
4. Click **Advance 1 day** and check **Orders → Received**. Incoming stock arrives
   on its ETA. Later days consume demand derived from the most recent forecast.
5. Return to **Overview**, reset the simulation, choose **Supply disruption**
   (1.6× demand and a two-day supplier delay) and run the cycle again. Compare the
   risk watch and supplier decisions with the baseline. Open **Evaluation** for
   the separate historical backtest and policy replay.
6. **Start auto loop** alternates planning and advancing one day, with a six-second
   pause between actions. Keep the tab open and stop the loop before editing inputs.

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
- `operations.py`: operational SQLite data import, planning, release and stock activity
- `app.py`: local HTTP API, workspace routing and scenario adapter
- `static/`: responsive dashboard, decision details, agent trace and offline fonts
- `data/`: attributed sales data and explicitly simulated operational tables
- `evaluate.py`: chronological holdout and historical inventory replay
- `results/`: reproducible CSV/JSON evaluation outputs
- `tests/`: agent checks and operational transaction regressions
- `DEMO_PREVIEW.html`: offline read-only dashboard snapshot, if included

## Operational API

Use `/api/operations/` for workspace data and `/api/practice/` for the isolated
practice records. `GET state`, `GET template.csv?type=products` and
`GET orders.csv` return the workspace, CSV templates and released orders.
`POST import`, `settings`, `activity`, `plan`, `release`, `receipt` and `cancel`
accept JSON. Activity and receipt submissions take a UUID request token so a
retry applies once. The API binds to localhost for single-machine use.

## Scenario API

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
**The bundled practice and scenario records assume warehouse assignments,
inventory, capacity, suppliers, lead times, INR quotes and costs.**
The original prices are sterling. Demo INR quotes are invented scenarios, not
a currency conversion. No promotion, weather, theft or real stockout labels
exist in this data. A statistical anomaly cannot establish theft or its cause.

Attribution: Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning
Repository. https://doi.org/10.24432/C5BW33. CC BY 4.0.

## What autonomy means here

In operations, agents autonomously produce draft orders. Operators control
release, physical stock movements and delivery receipts. Suppliers receive
orders through whatever external process the operator uses after exporting a PO.
The scenario lab autonomously updates its separate simulation ledger.
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

"""Persistent inventory and procurement workspace using the SupplyMind agents."""

import csv
import io
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import date, timedelta
from threading import Lock
from uuid import UUID

import pandas as pd

from agents import Context, MasterOrchestrator, ROOT


HEADERS = {
    'products': ('sku', 'name', 'selling_price_inr', 'holding_cost_day_inr', 'stockout_penalty_inr'),
    'inventory': ('sku', 'warehouse', 'on_hand', 'reserved', 'capacity'),
    'vendors': ('vendor_id', 'name', 'sku', 'unit_price_inr', 'lead_days', 'reliability',
                'min_qty', 'lot_size', 'max_qty', 'shipping_inr', 'discount_threshold', 'discount_rate'),
    'sales': ('date', 'sku', 'warehouse', 'units'),
}
MIN_HISTORY_DAYS = 28
MAX_HISTORY_AGE = 7
MAX_HISTORY_DAYS = 365
ORDER_FIELDS = ('id', 'kind', 'sku', 'warehouse', 'source', 'vendor_id', 'vendor', 'quantity',
                'received_quantity', 'unit_price_inr', 'total_inr', 'created_on', 'eta', 'status')
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    budget REAL NOT NULL CHECK (budget > 0),
    revision INTEGER NOT NULL
);
INSERT OR IGNORE INTO settings VALUES (1, 'Operations workspace', 100000, 0);
CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS positions (
    sku TEXT NOT NULL REFERENCES products(sku),
    warehouse TEXT NOT NULL,
    on_hand INTEGER NOT NULL CHECK (on_hand >= 0),
    reserved INTEGER NOT NULL CHECK (reserved BETWEEN 0 AND on_hand),
    capacity INTEGER NOT NULL CHECK (capacity >= on_hand),
    PRIMARY KEY (sku, warehouse)
);
CREATE TABLE IF NOT EXISTS vendors (
    sku TEXT NOT NULL REFERENCES products(sku),
    vendor_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (sku, vendor_id)
);
CREATE TABLE IF NOT EXISTS sales (
    day TEXT NOT NULL,
    sku TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    units INTEGER NOT NULL CHECK (units >= 0),
    PRIMARY KEY (day, sku, warehouse),
    FOREIGN KEY (sku, warehouse) REFERENCES positions(sku, warehouse)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    token TEXT UNIQUE,
    payload TEXT NOT NULL
);
"""


class Conflict(ValueError):
    """The operator needs to reload against the current workspace state."""


def integer(value, field, minimum=0, maximum=2_147_483_647):
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f'{field} must be a whole number') from None
    if not minimum <= result <= maximum:
        raise ValueError(f'{field} must be from {minimum} to {maximum}')
    return result


def number(value, field, minimum=0, maximum=1_000_000_000):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{field} must be numeric') from None
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f'{field} must be from {minimum} to {maximum}')
    return result


def text(value, field, maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f'{field} must contain 1 to {maximum} characters')
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise ValueError(f'{field} cannot contain control characters')
    return result


def day_value(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError('date must use YYYY-MM-DD') from None
    if value != parsed.isoformat():
        raise ValueError('date must use YYYY-MM-DD')
    return parsed


def identifier(value, field):
    result = text(value, field, 64)
    if '/' in result:
        raise ValueError(f'{field} cannot contain a slash')
    return result


def request_token(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ValueError('A request identifier is required') from None


def parse_product(row):
    return dict(
        sku=identifier(row['sku'], 'SKU'),
        name=text(row['name'], 'Product name'),
        selling_price_inr=number(row['selling_price_inr'], 'Selling price'),
        holding_cost_day_inr=number(row['holding_cost_day_inr'], 'Daily holding cost'),
        stockout_penalty_inr=number(row['stockout_penalty_inr'], 'Stockout cost'),
    )


def parse_position(row):
    on_hand = integer(row['on_hand'], 'Opening stock')
    return dict(
        sku=identifier(row['sku'], 'SKU'),
        warehouse=identifier(row['warehouse'], 'Warehouse'),
        on_hand=on_hand,
        reserved=integer(row['reserved'], 'Reserved stock', 0, on_hand),
        capacity=integer(row['capacity'], 'Capacity', max(1, on_hand)),
    )


def parse_vendor(row):
    lot = integer(row['lot_size'], 'Lot size', 1)
    minimum = integer(row['min_qty'], 'Minimum quantity', 1)
    if minimum % lot:
        raise ValueError('Minimum quantity must be a multiple of lot size')
    return dict(
        vendor_id=identifier(row['vendor_id'], 'Supplier ID'),
        name=text(row['name'], 'Supplier name'),
        sku=identifier(row['sku'], 'SKU'),
        unit_price_inr=number(row['unit_price_inr'], 'Unit price'),
        lead_days=integer(row['lead_days'], 'Lead time in days', 1, 14),
        reliability=number(row['reliability'], 'Reliability', 0, 1),
        min_qty=minimum,
        lot_size=lot,
        max_qty=integer(row['max_qty'], 'Maximum quantity', minimum),
        shipping_inr=number(row['shipping_inr'], 'Shipping cost'),
        discount_threshold=integer(row['discount_threshold'], 'Discount threshold'),
        discount_rate=number(row['discount_rate'], 'Discount rate', 0, 1),
    )


def parse_sale(row, today):
    day = day_value(row['date'])
    if day >= today:
        raise ValueError('Import sales only for days that have already closed')
    return dict(
        day=day.isoformat(),
        sku=identifier(row['sku'], 'SKU'),
        warehouse=identifier(row['warehouse'], 'Warehouse'),
        units=integer(row['units'], 'Sold units'),
    )


def csv_rows(kind, content, today):
    if not isinstance(content, str):
        raise ValueError(f'{kind}.csv must contain text')
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff'), newline=''), strict=True)
    try:
        headers = reader.fieldnames or []
    except csv.Error as error:
        raise ValueError(f'{kind}.csv has an invalid header: {error}') from None
    missing = set(HEADERS[kind]) - set(headers)
    if missing or len(headers) != len(set(headers)):
        description = ', '.join(sorted(missing)) if missing else 'duplicate columns'
        raise ValueError(f'{kind}.csv has invalid headers ({description})')
    parsers = {'products': parse_product, 'inventory': parse_position, 'vendors': parse_vendor,
               'sales': lambda row: parse_sale(row, today)}
    fields = {'products': ('sku',), 'inventory': ('sku', 'warehouse'), 'vendors': ('sku', 'vendor_id'),
              'sales': ('day', 'sku', 'warehouse')}
    seen = set()
    parsed = []
    try:
        for row in reader:
            try:
                if None in row or any(row[column] is None for column in HEADERS[kind]):
                    raise ValueError('Row does not match the column headers')
                record = parsers[kind](row)
                key = tuple(record[field] for field in fields[kind])
                if key in seen:
                    raise ValueError('Duplicate record in this file')
                seen.add(key)
                parsed.append(record)
            except ValueError as error:
                raise ValueError(f'{kind}.csv line {reader.line_num}: {error}') from None
    except csv.Error as error:
        raise ValueError(f'{kind}.csv line {reader.line_num}: {error}') from None
    if not parsed:
        raise ValueError(f'{kind}.csv must contain at least one data row')
    return parsed


def position_key(row):
    return row['sku'], row['warehouse']


def json_records(db, query, args=()):
    return [json.loads(row['payload']) for row in db.execute(query, args)]


def eligible_history(positions, sales, vendors, today, include_series=False):
    """Plan only positions with usable history and an eligible supplier list."""
    dates_by_position = defaultdict(dict)
    for sale in sales:
        dates_by_position[position_key(sale)][sale['day']] = sale['units']
    supplier_skus = {vendor['sku'] for vendor in vendors}
    coverage, history, eligible = [], [], []
    for position in positions:
        sku, warehouse = position_key(position)
        observed = dates_by_position[(sku, warehouse)]
        start, end = (min(observed), max(observed)) if observed else (None, None)
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 if end else 0
        reasons = []
        if sku not in supplier_skus:
            reasons.append('Add a supplier')
        if days < MIN_HISTORY_DAYS:
            reasons.append(f'Import at least {MIN_HISTORY_DAYS} calendar days of sales history')
        elif (today - date.fromisoformat(end)).days > MAX_HISTORY_AGE:
            reasons.append('Import more recent completed sales')
        coverage.append(dict(sku=sku, warehouse=warehouse, days=days, last_sale_date=end,
                             ready=not reasons, reasons=reasons))
        if not reasons and include_series:
            eligible.append(position)
            history.extend(dict(date=day, sku=sku, warehouse=warehouse, units=observed.get(day.date().isoformat(), 0))
                           for day in pd.date_range(start, end, freq='D'))
    return coverage, history, eligible


def csv_export(rows, fields):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    # Treat locally supplied text as text when the export is opened in a spreadsheet.
    for row in rows:
        writer.writerow({key: "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@'))
                         else value for key, value in row.items()})
    return output.getvalue()


class Operations:
    def __init__(self, path=ROOT / 'operations.db', today=date.today):
        self.path = path
        self.today = today

    @contextmanager
    def database(self, write=False):
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys = ON')
            db.executescript(SCHEMA)
            with db:
                db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield db

    def inputs(self, db, today):
        settings = dict(db.execute('SELECT name, budget, revision FROM settings').fetchone())
        products = json_records(db, 'SELECT payload FROM products ORDER BY sku')
        positions = [dict(row) for row in db.execute('SELECT * FROM positions ORDER BY sku, warehouse')]
        vendors = json_records(db, 'SELECT payload FROM vendors ORDER BY sku, vendor_id')
        sales = [dict(row) for row in db.execute(
            'SELECT * FROM sales WHERE day >= ? AND day < ? ORDER BY day',
            ((today - timedelta(days=MAX_HISTORY_DAYS)).isoformat(), today.isoformat()),
        )]
        return settings, products, positions, vendors, sales

    def latest_run(self, db):
        return db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()

    def orders(self, db):
        return json_records(db, 'SELECT payload FROM orders ORDER BY run_id, id')

    def snapshot(self, db, message='', today=None):
        today = today or self.today()
        settings, products, positions, vendors, sales = self.inputs(db, today)
        coverage, _, _ = eligible_history(positions, sales, vendors, today)
        run = self.latest_run(db)
        analysis = json.loads(run['payload']) if run else {}
        forecasts = analysis.get('forecasts', [])
        forecast_keys = {position_key(row) for row in forecasts}
        daily = defaultdict(int)
        for row in sales:
            if not forecast_keys or position_key(row) in forecast_keys:
                daily[row['day']] += row['units']
        history = [{'date': day.date().isoformat(), 'units': daily[day.date().isoformat()]}
                   for day in pd.date_range(min(daily), max(daily), freq='D')[-42:]] if daily else []
        run_info = analysis.get('last_run')
        if run:
            current = run['revision'] == settings['revision'] and run['day'] == today.isoformat()
            run_info = dict(run_info, id=run['id'], status=run['status'], current=current)
        return dict(
            operational=True, date=today.isoformat(), settings=settings,
            products=products, inventory=positions, vendors=vendors, orders=self.orders(db),
            coverage=coverage, history=history, evaluation={},
            activity=json_records(db, 'SELECT payload FROM activity ORDER BY id DESC LIMIT 40'),
            forecasts=forecasts, risks=analysis.get('risks', []),
            proposals=analysis.get('proposals', []), log=analysis.get('log', []),
            anomalies=analysis.get('anomalies', []), last_run=run_info,
            last_action=message,
        )

    def state(self):
        with self.database() as db:
            return self.snapshot(db)

    def change_revision(self, db):
        db.execute('UPDATE settings SET revision = revision + 1 WHERE id = 1')
        self.supersede_drafts(db)

    def supersede_drafts(self, db):
        for order in json_records(db, "SELECT payload FROM orders WHERE status='DRAFT'"):
            self.save_order(db, order, 'SUPERSEDED')
        db.execute("UPDATE runs SET status='SUPERSEDED' WHERE status='DRAFT'")

    def configure(self, body):
        name = text(body.get('name'), 'Workspace name', 64)
        budget = number(body.get('budget'), 'Per-order budget', .01)
        with self.database(write=True) as db:
            previous = db.execute('SELECT budget FROM settings').fetchone()['budget']
            db.execute('UPDATE settings SET name=?, budget=? WHERE id=1', (name, budget))
            if budget != previous:
                self.change_revision(db)
            return self.snapshot(db, 'Workspace settings saved.')

    def import_data(self, body, replace=False):
        files = body.get('files')
        if not isinstance(files, dict) or not files or not files.keys() <= HEADERS.keys():
            raise ValueError('Choose one or more products, inventory, vendors or sales CSV files')
        today = self.today()
        imported = {kind: csv_rows(kind, files[kind], today) for kind in HEADERS if kind in files}
        with self.database(write=True) as db:
            before = db.total_changes
            if replace:
                for table in ('orders', 'runs', 'activity', 'sales', 'vendors', 'positions', 'products'):
                    db.execute(f'DELETE FROM {table}')
                db.execute("UPDATE settings SET name='Practice workspace', budget=100000 WHERE id=1")
            for product in imported.get('products', []):
                db.execute(
                    'INSERT INTO products VALUES (?, ?) ON CONFLICT(sku) DO UPDATE SET payload=excluded.payload '
                    'WHERE payload <> excluded.payload', (product['sku'], json.dumps(product)),
                )
            for kind in ('inventory', 'vendors'):
                for record in imported.get(kind, []):
                    if not db.execute('SELECT 1 FROM products WHERE sku=?', (record['sku'],)).fetchone():
                        raise ValueError(f"{kind}.csv references unknown SKU {record['sku']}")
                    if kind == 'inventory':
                        # Opening balance applies exactly once. Master data updates cannot overwrite stock.
                        self.import_position(db, record, today)
                    else:
                        db.execute(
                            'INSERT INTO vendors VALUES (?, ?, ?) ON CONFLICT(sku, vendor_id) '
                            'DO UPDATE SET payload=excluded.payload WHERE payload <> excluded.payload',
                            (record['sku'], record['vendor_id'], json.dumps(record)),
                        )
            for sale in imported.get('sales', []):
                self.position(db, position_key(sale))
                values = sale['day'], sale['sku'], sale['warehouse'], sale['units']
                existing = db.execute('SELECT units FROM sales WHERE day=? AND sku=? AND warehouse=?', values[:3]).fetchone()
                if existing and existing['units'] != sale['units']:
                    raise Conflict(f"Sales already recorded for {sale['sku']} at {sale['warehouse']} on {sale['day']}")
                if not existing:
                    db.execute('INSERT INTO sales VALUES (?, ?, ?, ?)', values)
            if db.total_changes != before:
                self.change_revision(db)
            counts = ', '.join(f'{len(rows)} {kind} rows' for kind, rows in imported.items())
            return self.snapshot(db, f'Processed {counts}.', today)

    def import_position(self, db, record, today):
        key = position_key(record)
        existing = db.execute('SELECT * FROM positions WHERE sku=? AND warehouse=?', key).fetchone()
        if not existing:
            db.execute('INSERT INTO positions VALUES (:sku, :warehouse, :on_hand, :reserved, :capacity)', record)
            self.add_activity(db, None, dict(date=today.isoformat(), kind='OPENING', sku=key[0], warehouse=key[1],
                                             quantity=record['on_hand'], delta=record['on_hand'], reference='CSV import'))
            return
        if record['capacity'] < existing['on_hand'] or record['reserved'] > existing['on_hand']:
            raise Conflict(f'{key[0]} at {key[1]} has an on-hand balance incompatible with the new limits')
        db.execute(
            'UPDATE positions SET reserved=?, capacity=? WHERE sku=? AND warehouse=? AND (reserved<>? OR capacity<>?)',
            (record['reserved'], record['capacity'], *key, record['reserved'], record['capacity']),
        )

    def position(self, db, key):
        row = db.execute('SELECT * FROM positions WHERE sku=? AND warehouse=?', key).fetchone()
        if not row:
            raise ValueError(f'Unknown product location {key[0]} at {key[1]}')
        return row

    def add_activity(self, db, token, data):
        db.execute('INSERT INTO activity(token, payload) VALUES (?, ?)', (token, json.dumps(data)))

    def already_recorded(self, db, token, fingerprint):
        row = db.execute('SELECT payload FROM activity WHERE token=?', (token,)).fetchone()
        if not row:
            return False
        if json.loads(row['payload']).get('request') != fingerprint:
            raise Conflict('This request identifier has already been used for another change')
        return True

    def change_stock(self, db, key, delta):
        position = self.position(db, key)
        new_stock = position['on_hand'] + delta
        if new_stock < position['reserved']:
            raise Conflict(f'The adjustment exceeds available stock for {key[0]} at {key[1]}')
        if new_stock > position['capacity']:
            raise Conflict(f'The adjustment exceeds capacity for {key[0]} at {key[1]}')
        db.execute('UPDATE positions SET on_hand=? WHERE sku=? AND warehouse=?', (new_stock, *key))

    def stock_activity(self, body):
        token = request_token(body.get('token'))
        key = identifier(body.get('sku'), 'SKU'), identifier(body.get('warehouse'), 'Warehouse')
        kind = body.get('kind')
        if kind not in ('SALE', 'STOCK_IN', 'STOCK_OUT'):
            raise ValueError('Choose a sale, stock addition or stock removal')
        quantity = integer(body.get('quantity'), 'Quantity', 1)
        reference = text(body.get('reference'), 'Reference')
        day = self.today()
        fingerprint = ['activity', *key, kind, quantity, reference]
        with self.database(write=True) as db:
            if self.already_recorded(db, token, fingerprint):
                return self.snapshot(db, 'This stock activity was already recorded.', day)
            delta = quantity if kind == 'STOCK_IN' else -quantity
            self.change_stock(db, key, delta)
            if kind == 'SALE':
                db.execute(
                    'INSERT INTO sales VALUES (?, ?, ?, ?) ON CONFLICT(day, sku, warehouse) '
                    'DO UPDATE SET units=units+excluded.units', (day.isoformat(), *key, quantity),
                )
            self.add_activity(db, token, dict(date=day.isoformat(), kind=kind, sku=key[0], warehouse=key[1],
                                             quantity=quantity, delta=delta, reference=reference, request=fingerprint))
            self.change_revision(db)
            return self.snapshot(db, 'Stock activity recorded.', day)

    def plan(self):
        today = self.today()
        with self.database() as db:
            settings, products, positions, vendors, sales = self.inputs(db, today)
            if self.has_current_run(db, settings['revision'], today):
                return self.snapshot(db, 'A plan already covers the current inputs.', today)
            incoming = [dict(order, quantity=order['quantity'] - order['received_quantity'])
                        for order in self.orders(db) if order['status'] == 'OPEN']
        coverage, history, eligible = eligible_history(positions, sales, vendors, today, include_series=True)
        if not eligible:
            raise ValueError('Add a stock location with supplier terms and sufficient recent sales history before planning')
        context = Context(today.isoformat(), eligible, pd.DataFrame(history),
                          {product['sku']: product for product in products}, vendors,
                          incoming, budget=settings['budget'], operational=True)
        MasterOrchestrator().run(context)
        new_orders = context.transfers + context.purchases
        analysis = dict(
            forecasts=[dict(sku=key[0], warehouse=key[1], **forecast) for key, forecast in context.forecasts.items()],
            risks=context.risks, proposals=context.proposals, anomalies=context.anomalies, log=context.log,
            last_run=dict(date=context.date, high_risks=sum(risk['level'] == 'HIGH' for risk in context.risks),
                          positions=len(eligible), excluded=len(coverage) - len(eligible), proposals=len(context.proposals),
                          transfers=len(context.transfers), purchases=len(context.purchases), budget=context.budget),
        )
        with self.database(write=True) as db:
            if db.execute('SELECT revision FROM settings').fetchone()['revision'] != settings['revision'] or self.today() != today:
                raise Conflict('Workspace inputs changed while the agents were planning. Run the plan again.')
            if self.has_current_run(db, settings['revision'], today):
                return self.snapshot(db, 'A plan already covers the current inputs.', today)
            self.supersede_drafts(db)
            status = 'DRAFT' if new_orders else 'NO_ORDERS'
            run_id = db.execute('INSERT INTO runs(day, revision, status, payload) VALUES (?, ?, ?, ?)',
                                (context.date, settings['revision'], status, json.dumps(analysis))).lastrowid
            for index, order in enumerate(new_orders, start=1):
                prefix = 'TR' if order['kind'] == 'TRANSFER' else 'PO'
                order.update(id=f'{prefix}-{today:%Y%m%d}-{run_id:04d}-{index:03d}', status='DRAFT',
                             received_quantity=0, created_on=context.date, plan_id=run_id)
                db.execute('INSERT INTO orders VALUES (?, ?, ?, ?)', (order['id'], run_id, 'DRAFT', json.dumps(order)))
            message = f'{len(new_orders)} orders drafted for review.' if new_orders else 'Plan complete. No orders were drafted.'
            return self.snapshot(db, message, today)

    def has_current_run(self, db, revision, today):
        run = self.latest_run(db)
        return run and run['revision'] == revision and run['day'] == today.isoformat()

    def save_order(self, db, order, status):
        order['status'] = status
        db.execute('UPDATE orders SET payload=?, status=? WHERE id=?', (json.dumps(order), status, order['id']))

    def release(self, body):
        run_id = integer(body.get('plan_id'), 'Plan ID', 1)
        today = self.today()
        with self.database(write=True) as db:
            run = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if run and run['status'] == 'RELEASED':
                return self.snapshot(db, 'This plan was already released.', today)
            if not run or run['status'] != 'DRAFT' or self.latest_run(db)['id'] != run_id or not self.has_current_run(db, run['revision'], today):
                raise Conflict('This draft has expired or been replaced. Run a new plan.')
            revision = db.execute('SELECT revision FROM settings').fetchone()['revision']
            if run['revision'] != revision:
                raise Conflict('Workspace inputs changed. Run a new plan before release.')
            orders = json_records(db, "SELECT payload FROM orders WHERE run_id=? AND status='DRAFT' ORDER BY id", (run_id,))
            for order in orders:
                if order['kind'] == 'TRANSFER':
                    source = order['sku'], order['source']
                    self.change_stock(db, source, -order['quantity'])
                    self.add_activity(db, None, dict(date=today.isoformat(), kind='DISPATCH', sku=source[0],
                                                     warehouse=source[1], quantity=order['quantity'],
                                                     delta=-order['quantity'], reference=order['id']))
                order['released_on'] = today.isoformat()
                self.save_order(db, order, 'OPEN')
            db.execute("UPDATE runs SET status='RELEASED' WHERE id=?", (run_id,))
            self.change_revision(db)
            return self.snapshot(db, f'Released {len(orders)} orders to the ledger.', today)

    def receipt(self, body):
        token = request_token(body.get('token'))
        order_id = text(body.get('id'), 'Order ID', 64)
        quantity = integer(body.get('quantity'), 'Received quantity', 1)
        reference = text(body.get('reference'), 'Receipt reference')
        today = self.today()
        fingerprint = ['receipt', order_id, quantity, reference]
        with self.database(write=True) as db:
            if self.already_recorded(db, token, fingerprint):
                return self.snapshot(db, 'This receipt was already recorded.', today)
            order = self.open_order(db, order_id)
            if quantity > order['quantity'] - order['received_quantity']:
                raise Conflict('The receipt is larger than the remaining order quantity')
            self.change_stock(db, position_key(order), quantity)
            order['received_quantity'] += quantity
            complete = order['received_quantity'] == order['quantity']
            if complete:
                order['received_on'] = today.isoformat()
            self.save_order(db, order, 'RECEIVED' if complete else 'OPEN')
            self.add_activity(db, token, dict(date=today.isoformat(), kind='RECEIPT', sku=order['sku'],
                                             warehouse=order['warehouse'], quantity=quantity, delta=quantity,
                                             reference=reference, order_id=order_id, request=fingerprint))
            self.change_revision(db)
            return self.snapshot(db, f'Received {quantity} units for {order_id}.', today)

    def open_order(self, db, order_id):
        result = json_records(db, "SELECT payload FROM orders WHERE id=? AND status='OPEN'", (order_id,))
        if not result:
            raise Conflict('This order is no longer open')
        return result[0]

    def cancel_purchase(self, body):
        order_id = text(body.get('id'), 'Order ID', 64)
        today = self.today()
        with self.database(write=True) as db:
            existing = json_records(db, 'SELECT payload FROM orders WHERE id=?', (order_id,))
            if existing and existing[0]['status'] == 'CANCELLED':
                return self.snapshot(db, 'The remaining purchase was already cancelled.', today)
            order = self.open_order(db, order_id)
            if order['kind'] != 'PURCHASE':
                raise Conflict('Only unreceived supplier quantities can be cancelled here')
            order['cancelled_on'] = today.isoformat()
            self.save_order(db, order, 'CANCELLED')
            self.add_activity(db, None, dict(date=today.isoformat(), kind='CANCEL', sku=order['sku'],
                                             warehouse=order['warehouse'],
                                             quantity=order['quantity'] - order['received_quantity'],
                                             delta=0, reference=order_id))
            self.change_revision(db)
            return self.snapshot(db, f'Cancelled the remaining units on {order_id}.', today)

    def export_orders(self, order_id=None):
        with self.database() as db:
            orders = [order for order in self.orders(db) if order['status'] in ('OPEN', 'RECEIVED', 'CANCELLED')]
            if order_id:
                orders = [order for order in orders if order['id'] == order_id]
                if not orders:
                    raise ValueError('Released order not found')
            return csv_export(orders, ORDER_FIELDS)


operations = Operations()


class PracticeOperations(Operations):
    def __init__(self, path=ROOT / 'practice.db'):
        super().__init__(path, today=lambda: date(2011, 12, 1))
        self.seed_lock = Lock()
        self.initialized = False

    def initialize(self):
        with self.seed_lock:
            if not self.initialized:
                if not self.state()['inventory']:
                    self.restore()
                self.initialized = True

    def restore(self):
        files = {kind: (ROOT / 'data' / f'{kind}.csv').read_text(encoding='utf-8-sig') for kind in HEADERS}
        result = self.import_data({'files': files}, replace=True)
        result['last_action'] = 'Practice data restored.'
        return result


practice = PracticeOperations()

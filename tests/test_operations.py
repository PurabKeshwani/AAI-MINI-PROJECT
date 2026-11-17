import csv
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from operations import Conflict, HEADERS, Operations, PracticeOperations


def sheet(kind, rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(HEADERS[kind])
    writer.writerows(rows)
    return output.getvalue()


def stock(snapshot, warehouse):
    return next(row for row in snapshot['inventory'] if row['warehouse'] == warehouse)['on_hand']


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'operations.db'
        self.today = date(2026, 11, 18)
        self.workspace = Operations(self.path, today=lambda: self.today)
        daily = []
        for offset in range(112, 0, -1):
            day = (self.today - timedelta(days=offset)).isoformat()
            daily.extend([(day, 'BOX-5', 'North', 15), (day, 'BOX-5', 'South', 4)])
        self.files = dict(
            products=sheet('products', [('BOX-5', 'Packing Box', 40, .4, 25)]),
            inventory=sheet('inventory', [('BOX-5', 'North', 8, 3, 400), ('BOX-5', 'South', 350, 10, 500)]),
            vendors=sheet('vendors', [('V-01', 'Carton Works', 'BOX-5', 18, 4, .98, 5, 5, 300, 20, 50, .05)]),
            sales=sheet('sales', daily),
        )

    def import_workspace(self):
        return self.workspace.import_data({'files': self.files})

    def test_import_is_atomic_and_keeps_recorded_stock(self):
        self.assertEqual(self.workspace.state()['inventory'], [])
        broken = sheet('sales', [('2026-11-14', 'BOX-5', 'Invalid warehouse', 20)])
        with self.assertRaises(ValueError):
            self.workspace.import_data({'files': dict(self.files, sales=broken)})
        self.assertEqual(self.workspace.state()['products'], [])
        imported = self.import_workspace()
        self.assertEqual(sum(row['ready'] for row in imported['coverage']), 2)
        self.assertEqual(imported['settings']['revision'], self.import_workspace()['settings']['revision'])

        args = dict(token=str(uuid4()), sku='BOX-5', warehouse='North', kind='SALE', quantity=2, reference='INV-001')
        self.assertEqual(stock(self.workspace.stock_activity(args), 'North'), 6)
        self.assertEqual(stock(self.workspace.stock_activity(args), 'North'), 6)
        with self.assertRaises(Conflict):
            self.workspace.stock_activity(dict(args, quantity=3))
        self.assertEqual(stock(self.import_workspace(), 'North'), 6)

        self.today += timedelta(days=1)
        history = self.workspace.state()['history']
        self.assertEqual(history[-1], {'date': '2026-11-18', 'units': 2})

    def test_plan_release_then_partial_and_complete_receipts(self):
        initial = self.import_workspace()
        plan = self.workspace.plan()
        plan_id = plan['last_run']['id']
        self.assertEqual(len({row['agent'] for row in plan['log']}), 11)
        self.assertTrue(all(len(forecast['values']) == 21 for forecast in plan['forecasts']))
        self.assertEqual({row['status'] for row in plan['orders']}, {'DRAFT'})
        self.assertEqual(stock(plan, 'South'), stock(initial, 'South'))
        self.assertEqual(self.workspace.plan()['last_run']['id'], plan_id)
        self.assertEqual(len(self.workspace.export_orders().strip().splitlines()), 1)

        release = self.workspace.release({'plan_id': plan_id})
        transfer = next(row for row in release['orders'] if row['kind'] == 'TRANSFER')
        purchase = next(row for row in release['orders'] if row['kind'] == 'PURCHASE')
        self.assertEqual(stock(release, 'North'), stock(initial, 'North'))
        self.assertEqual(stock(release, 'South'), stock(initial, 'South') - transfer['quantity'])
        self.assertEqual(stock(release, 'South'), stock(self.workspace.release({'plan_id': plan_id}), 'South'))
        self.assertIn(purchase['id'], self.workspace.export_orders(purchase['id']))

        receipt = dict(id=transfer['id'], quantity=1, reference='DEL-01', token=str(uuid4()))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.workspace.receipt, [receipt, receipt]))
        self.assertEqual(stock(self.workspace.state(), 'North'), stock(initial, 'North') + 1)
        with self.assertRaises(Conflict):
            self.workspace.receipt(dict(receipt, token=str(uuid4()), quantity=transfer['quantity']))

        complete = self.workspace.receipt(dict(receipt, token=str(uuid4()), reference='DEL-02',
                                               quantity=transfer['quantity'] - 1))
        stored = next(row for row in complete['orders'] if row['id'] == transfer['id'])
        self.assertEqual(stored['status'], 'RECEIVED')
        self.assertEqual(stock(complete, 'North'), stock(initial, 'North') + transfer['quantity'])
        self.assertEqual(stock(complete, 'North'), stock(Operations(self.path, today=lambda: self.today).state(), 'North'))

    def test_input_change_invalidates_draft_and_budget_is_enforced(self):
        self.import_workspace()
        first = self.workspace.plan()
        stale = self.workspace.configure({'name': 'Live warehouse', 'budget': 1})
        self.assertEqual({row['status'] for row in stale['orders']}, {'SUPERSEDED'})
        with self.assertRaises(Conflict):
            self.workspace.release({'plan_id': first['last_run']['id']})
        latest = self.workspace.plan()
        self.assertTrue(latest['last_run']['current'])
        self.assertEqual(latest['last_run']['purchases'], 0)
        self.assertTrue(any('Over per-order budget' in option['constraint_reasons']
                            for proposal in latest['proposals'] for option in proposal['candidates']))

    def test_physical_capacity_includes_reserved_stock(self):
        self.files['inventory'] = sheet('inventory', [('BOX-5', 'North', 35, 30, 40), ('BOX-5', 'South', 350, 10, 500)])
        self.import_workspace()
        plan = self.workspace.plan()
        self.assertEqual(sum(order['quantity'] for order in plan['orders'] if order['warehouse'] == 'North'), 5)
        self.workspace.release({'plan_id': plan['last_run']['id']})
        transfer = next(order for order in plan['orders'] if order['kind'] == 'TRANSFER')
        self.assertEqual(stock(self.workspace.receipt(dict(
            id=transfer['id'], quantity=5, reference='Small warehouse', token=str(uuid4()),
        )), 'North'), 40)

    def test_practice_has_an_independent_restore(self):
        self.import_workspace()
        practice_path = Path(self.temporary.name) / 'practice.db'
        practice = PracticeOperations(practice_path)
        practice.initialize()
        self.assertEqual(len(practice.state()['inventory']), 15)
        self.assertEqual(practice.state()['date'], '2011-12-01')
        self.assertEqual(practice.state()['orders'], [])
        practice.configure({'name': 'Training', 'budget': 99})
        practice.restore()
        self.assertEqual(practice.state()['settings']['budget'], 100000)
        self.assertEqual(len(self.workspace.state()['inventory']), 2)

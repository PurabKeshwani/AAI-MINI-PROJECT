import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agents import *
import app

class SupplyChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.sales,cls.inventory,cls.products,cls.vendors=load_inputs()
    def context(self): return Context('2011-11-30',copy.deepcopy(self.inventory),self.sales,self.products,self.vendors)
    def test_forecast_no_future_data(self):
        h=self.sales[(self.sales.sku=='85123A')&(self.sales.warehouse=='WH-1')].set_index('date').units
        truncated=h.iloc[:-28]; a=predict(truncated,14,'ridge'); h.iloc[-28:]=999999
        np.testing.assert_allclose(a,predict(h.iloc[:-28],14,'ridge'))
    def test_inventory_position_includes_orders(self):
        c=self.context();r=c.inventory[0]; c.orders=[dict(sku=r['sku'],warehouse=r['warehouse'],quantity=75,status='OPEN')]
        InventoryTrackingAgent().run(c);self.assertEqual(r['position'],r['available']+75)
    def test_invalid_stock_rejected(self):
        c=self.context();c.inventory[0]['on_hand']=-1
        with self.assertRaises(ValueError):InventoryTrackingAgent().run(c)
    def test_transfer_conservation(self):
        c=self.context(); initial=sum(r['on_hand'] for r in c.inventory)
        for a in [InventoryTrackingAgent,DemandForecastingAgent,StockoutRiskAgent,WarehouseOptimizationAgent]:a().run(c)
        self.assertEqual(initial,sum(r['on_hand'] for r in c.inventory)+sum(t['quantity'] for t in c.transfers))
        self.assertTrue(all(r['on_hand']>=0 for r in c.inventory))
    def test_vendor_constraints_and_minimum_score(self):
        c=MasterOrchestrator().run(self.context())
        self.assertTrue(c.proposals)
        for p in c.proposals:
            if 'selected' in p:
                v=p['selected']; self.assertTrue(v['feasible']);self.assertLessEqual(v['landed'],c.budget)
                self.assertEqual(v['quantity']%v['lot_size'],0);self.assertGreaterEqual(v['quantity'],v['min_qty'])
                self.assertLessEqual(v['quantity']+p['position'],p['capacity'])
                self.assertEqual(v['score'],min(x['score'] for x in p['candidates'] if x['feasible']))
    def test_budget_blocks_purchase(self):
        c=self.context(); c.budget=1;MasterOrchestrator().run(c);self.assertEqual(c.purchases,[])
    def test_eleven_agents_logged(self):
        c=MasterOrchestrator().run(self.context());self.assertEqual(len(set(l['agent'] for l in c.log)),11)
    def test_execution_idempotency_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=app.DB; app.DB=Path(tmp)/'test.db'
            try:
                app.reset();first=app.cycle();again=app.cycle()
                self.assertEqual(len(first['orders']),len(again['orders']))
                self.assertTrue(first['orders'])
                for i in range(8):final=app.advance()
                self.assertTrue(all(o['status']=='RECEIVED' for o in final['orders']))
                self.assertTrue(all(r['on_hand']>=0 for r in final['inventory']))
            finally:app.DB=original

if __name__=='__main__': unittest.main(verbosity=2)

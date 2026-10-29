"""Eleven cooperating agents with a shared state and an explicit audit trail.

Agents use statistical learning and inspectable inventory policies. They do not
use an LLM or claim to implement the MARL papers reviewed in the report.
"""
from dataclasses import dataclass, field
from pathlib import Path
import math
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent
MODELS = ['mean28','seasonal7','ridge']

def features(values, date):
    d = pd.Timestamp(date); a=np.asarray(values,dtype=float)
    return [*[(d.dayofweek==i)*1. for i in range(7)],
            d.dayofyear/365, math.sin(2*math.pi*d.dayofyear/365),
            math.cos(2*math.pi*d.dayofyear/365),
            a[-1],a[-7],a[-14],a[-7:].mean(),a[-28:].mean()]

def predict(history, horizon=14, model='mean28'):
    vals=history.astype(float).tolist(); dates=pd.date_range(history.index[-1]+pd.Timedelta(days=1),periods=horizon)
    fitted=None
    if model=='ridge' and len(vals)>=60:
        X=[features(vals[:i],history.index[i]) for i in range(28,len(vals))]
        fitted=make_pipeline(StandardScaler(),Ridge(alpha=30)).fit(X,vals[28:])
    out=[]
    for day in dates:
        if fitted is not None: p=float(fitted.predict([features(vals,day)])[0])
        elif model=='seasonal7': p=float(vals[-7])
        else: p=float(np.mean(vals[-28:]))
        p=float(np.clip(p,0,max(10,max(vals[-56:])*2)))
        out.append(p); vals.append(p)
    return np.array(out)

def wape(actual,pred):
    return float(np.abs(np.asarray(actual)-np.asarray(pred)).sum()/max(1,np.abs(actual).sum()))

def choose_model(history):
    if len(history)<90: return 'mean28',{}
    train=history.iloc[:-28]; valid=history.iloc[-28:]
    errors={m:wape(valid.values,predict(train,28,m)) for m in MODELS}
    return min(errors,key=errors.get),errors

@dataclass
class Context:
    date: str
    inventory: list
    history: pd.DataFrame
    products: dict
    vendors: list
    orders: list=field(default_factory=list)
    demand_multiplier: float=1.
    delay_days: int=0
    budget: float=100000.
    review_days: int=7
    forecasts: dict=field(default_factory=dict)
    risks: list=field(default_factory=list)
    transfers: list=field(default_factory=list)
    proposals: list=field(default_factory=list)
    purchases: list=field(default_factory=list)
    anomalies: list=field(default_factory=list)
    log: list=field(default_factory=list)

    def emit(self,agent,message): self.log.append(dict(agent=agent,message=message))

class InventoryTrackingAgent:
    def run(self,c):
        for r in c.inventory:
            if r['on_hand']<0 or r['reserved']<0 or r['reserved']>r['on_hand']:
                raise ValueError('Invalid inventory or reservation')
            r['available']=r['on_hand']-r['reserved']
            r['on_order']=sum(o['quantity'] for o in c.orders if o['sku']==r['sku'] and o['warehouse']==r['warehouse'] and o['status']=='OPEN')
            r['position']=r['available']+r['on_order']
        c.emit(type(self).__name__,f'Validated {len(c.inventory)} stock records; counted open orders in inventory position.')

class DemandForecastingAgent:
    def run(self,c):
        for (sku,wh),g in c.history.groupby(['sku','warehouse']):
            h=g.set_index('date').units.sort_index()
            model, scores=choose_model(h)
            f=predict(h,21,model)*c.demand_multiplier
            # Seasonal-naive residuals are an inexpensive uncertainty proxy.
            residual=(h-h.shift(7)).dropna().tail(56)
            sigma=max(1,float(residual.std(ddof=1)))*c.demand_multiplier
            c.forecasts[(sku,wh)]={'values':f.tolist(),'mean':float(np.mean(f[:7])),
                                     'sigma':sigma,'model':model,'validation_wape':scores}
        c.emit(type(self).__name__,'Selected mean28, seasonal7 or Ridge using prior 28-day validation; forecast next 21 days.')

class StockoutRiskAgent:
    def run(self,c):
        c.risks=[]
        for r in c.inventory:
            f=c.forecasts[(r['sku'],r['warehouse'])]; mu=f['mean']
            lead=min(v['lead_days'] for v in c.vendors if v['sku']==r['sku'])+c.delay_days
            safety=math.ceil(1.645*f['sigma']*math.sqrt(lead))
            need=sum(f['values'][:lead]); cover=r['available']/max(.01,mu)
            projected=r['available']; shortage=0
            for d in range(1,lead+1):
                due=(pd.Timestamp(c.date)+pd.Timedelta(days=d)).date().isoformat()
                projected+=sum(o['quantity'] for o in c.orders if o['sku']==r['sku'] and o['warehouse']==r['warehouse'] and o['status']=='OPEN' and o['eta']==due)
                projected-=f['values'][d-1]; shortage=max(shortage,-projected)
            level='HIGH' if shortage>0 else ('MEDIUM' if r['position']<need+safety else 'LOW')
            if r['available']>sum(f['values'][:14])+1.645*f['sigma']*math.sqrt(14): level='OVERSTOCK'
            c.risks.append(dict(sku=r['sku'],warehouse=r['warehouse'],level=level,
                                days_cover=round(cover,2),safety_stock=safety,
                                expected_shortage=round(shortage,1),lead_days=lead))
        c.emit(type(self).__name__,f"Detected {sum(r['level']=='HIGH' for r in c.risks)} high risks and {sum(r['level']=='OVERSTOCK' for r in c.risks)} overstock positions.")

class AnomalyDetectionAgent:
    def run(self,c):
        for key,g in c.history.groupby(['sku','warehouse']):
            a=g.sort_values('date').units.to_numpy(dtype=float); ref=a[-29:-1]
            med=float(np.median(ref)); mad=float(np.median(np.abs(ref-med)))
            score=abs(a[-1]-med)/max(1,1.4826*mad)
            if score>3.5: c.anomalies.append(dict(sku=key[0],warehouse=key[1],robust_z=round(score,2),type='Unusual sales; cause unverified'))
        if c.delay_days: c.anomalies.append(dict(type=f'Scenario supplier delay +{c.delay_days} days'))
        c.emit(type(self).__name__,f'Logged {len(c.anomalies)} alerts using median absolute deviation and scenario delay signals.')

class WarehouseOptimizationAgent:
    def run(self,c):
        # One-day transfers reserve donor stock immediately and arrive through the pipeline.
        for risk in c.risks:
            if risk['level']!='HIGH': continue
            receiver=next(r for r in c.inventory if (r['sku'],r['warehouse'])==(risk['sku'],risk['warehouse']))
            f=c.forecasts[(risk['sku'],risk['warehouse'])]
            wanted=max(0,math.ceil(sum(f['values'][:3])+risk['safety_stock']-receiver['position']))
            for donor in c.inventory:
                if donor['sku']!=receiver['sku'] or donor['warehouse']==receiver['warehouse']: continue
                df=c.forecasts[(donor['sku'],donor['warehouse'])]
                protected=sum(df['values'][:14])+1.645*df['sigma']*math.sqrt(14)
                surplus=max(0,math.floor(donor['available']-protected))
                q=min(wanted,surplus,max(0,receiver['capacity']-receiver['position']))
                if q<=0: continue
                donor['on_hand']-=q; donor['available']-=q; donor['position']-=q
                receiver['position']+=q; receiver['on_order']+=q; wanted-=q
                t=dict(sku=receiver['sku'],warehouse=receiver['warehouse'],source=donor['warehouse'],
                       quantity=q,eta=(pd.Timestamp(c.date)+pd.Timedelta(days=1)).date().isoformat(),
                       status='OPEN',kind='TRANSFER')
                c.transfers.append(t); c.orders.append(t)
        c.emit(type(self).__name__,f'Planned {len(c.transfers)} transfers while protecting 14 days plus donor buffer.')

class ReorderDecisionAgent:
    def run(self,c):
        for r in c.inventory:
            f=c.forecasts[(r['sku'],r['warehouse'])]
            lead=min(v['lead_days'] for v in c.vendors if v['sku']==r['sku'])+c.delay_days
            safety=1.645*f['sigma']*math.sqrt(lead)
            if r['position']<sum(f['values'][:lead])+safety:
                c.proposals.append(dict(sku=r['sku'],warehouse=r['warehouse'],position=r['position'],
                                        available=r['available'],capacity=r['capacity']))
        c.emit(type(self).__name__,f'{len(c.proposals)} positions need a purchase after considering transfers and pipeline stock.')

class VendorIntelligenceAgent:
    def run(self,c):
        for p in c.proposals:
            f=c.forecasts[(p['sku'],p['warehouse'])]; candidates=[]
            for v in [v for v in c.vendors if v['sku']==p['sku']]:
                L=v['lead_days']+c.delay_days; horizon=L+c.review_days
                target=sum(f['values'][:horizon])+1.645*f['sigma']*math.sqrt(horizon)
                q=math.ceil(max(0,target-p['position'])/v['lot_size'])*v['lot_size']
                q=max(v['min_qty'],q)
                room=min(v['max_qty'],p['capacity']-p['position'])
                q=min(q,int(room//v['lot_size'])*v['lot_size'])
                price=v['unit_price_inr']*(1-v['discount_rate'] if q>=v['discount_threshold'] else 1)
                landed=q*price+v['shipping_inr']
                feasible=q>=v['min_qty'] and landed<=c.budget
                # Timed open arrivals enter the projected shortage cost exactly once.
                projected=p['available']; shortage=0
                for d in range(1,L+1):
                    day=(pd.Timestamp(c.date)+pd.Timedelta(days=d)).date().isoformat()
                    projected+=sum(o['quantity'] for o in c.orders if o['sku']==p['sku'] and o['warehouse']==p['warehouse'] and o['status']=='OPEN' and o['eta']==day)
                    projected-=f['values'][d-1]; shortage=max(shortage,-projected)
                product=c.products[p['sku']]
                exposure=(max(0,shortage)+(1-v['reliability'])*f['mean']*2)*product['stockout_penalty_inr']
                holding=q*product['holding_cost_day_inr']*horizon/2
                candidates.append(dict(**v,quantity=q,actual_price=round(price,2),landed=round(landed,2),
                                        shortage_cost=round(exposure,2),holding_cost=round(holding,2),
                                        score=round(landed+exposure+holding,2),feasible=feasible,
                                        effective_lead=L))
            p['candidates']=candidates
        c.emit(type(self).__name__,'Enumerated all suppliers with MOQ, lot size, capacity, discounts and a per-order INR 100000 limit.')

class CostOptimizationAgent:
    def run(self,c):
        for p in c.proposals:
            feasible=[v for v in p['candidates'] if v['feasible']]
            if feasible: p['selected']=min(feasible,key=lambda v:v['score'])
            else: p['review']='No feasible supplier under configured constraints'
        c.emit(type(self).__name__,'Minimized purchase, holding and estimated shortage cost over enumerated feasible candidates; no global-optimality claim.')

class PurchaseOrderAgent:
    def run(self,c):
        for p in c.proposals:
            if 'selected' not in p: continue
            v=p['selected']
            c.purchases.append(dict(sku=p['sku'],warehouse=p['warehouse'],vendor_id=v['vendor_id'],
                    vendor=v['name'],quantity=v['quantity'],unit_price_inr=v['actual_price'],
                    total_inr=v['landed'],eta=(pd.Timestamp(c.date)+pd.Timedelta(days=v['effective_lead'])).date().isoformat(),
                    status='OPEN',kind='PURCHASE',reason=f"Lowest feasible estimated total cost INR {v['score']:.2f}"))
        c.emit(type(self).__name__,f'Generated {len(c.purchases)} structured purchase orders for internal simulated execution.')

class ReportingDashboardAgent:
    def run(self,c):
        c.emit(type(self).__name__,'Published forecasts, risk alerts, supplier comparison, purchase orders and the complete decision trace.')

class MasterOrchestrator:
    agents=[InventoryTrackingAgent,DemandForecastingAgent,StockoutRiskAgent,AnomalyDetectionAgent,
            WarehouseOptimizationAgent,ReorderDecisionAgent,VendorIntelligenceAgent,
            CostOptimizationAgent,PurchaseOrderAgent,ReportingDashboardAgent]
    def run(self,c):
        c.emit(type(self).__name__,'Started detect-decide-execute cycle on a shared blackboard.')
        for agent in self.agents: agent().run(c)
        c.emit(type(self).__name__,'Cycle complete. Persistence and idempotency belong to the execution adapter.')
        return c

def load_inputs():
    data=ROOT/'data'
    sales=pd.read_csv(data/'sales.csv',parse_dates=['date'])
    inventory=pd.read_csv(data/'inventory.csv').to_dict('records')
    products={r['sku']:r for r in pd.read_csv(data/'products.csv',dtype={'sku':str}).to_dict('records')}
    vendors=pd.read_csv(data/'vendors.csv',dtype={'sku':str}).to_dict('records')
    sales['sku']=sales.sku.astype(str)
    for r in inventory: r['sku']=str(r['sku'])
    return sales,inventory,products,vendors

"""Reproducible chronological forecasts and a 56-day inventory replay."""
import copy, json
from pathlib import Path
import numpy as np
import pandas as pd
from agents import *

def evaluate():
    out=ROOT/'results'; out.mkdir(exist_ok=True)
    sales,inventory,products,vendors=load_inputs()
    metrics=[]; preds=[]
    for (sku,wh),g in sales.groupby(['sku','warehouse']):
        h=g.set_index('date').units.sort_index(); train=h.iloc[:-28]; actual=h.iloc[-28:]
        selected,validation=choose_model(train)
        for model in MODELS+['selected']:
            p=predict(train,28,selected if model=='selected' else model)
            metrics.append(dict(sku=sku,warehouse=wh,model=model,selected_model=selected,
                                mae=float(np.mean(np.abs(actual-p))),wape=wape(actual.values,p),
                                absolute_error=float(np.abs(actual.values-p).sum()),actual_units=int(actual.sum())))
            if model=='selected':
                for day,y,yhat in zip(actual.index,actual,p):
                    preds.append(dict(date=str(day.date()),sku=sku,warehouse=wh,actual=int(y),forecast=round(float(yhat),3)))
    pd.DataFrame(metrics).to_csv(out/'forecast_metrics.csv',index=False)
    pd.DataFrame(preds).to_csv(out/'forecast_predictions.csv',index=False)
    # Both policies start with identical stocks defined using pre-replay sales only.
    start=sales.date.max()-pd.Timedelta(days=55)
    initial=copy.deepcopy(inventory)
    for r in initial:
        h=sales[(sales.sku==r['sku'])&(sales.warehouse==r['warehouse'])&(sales.date<start)]
        mu=float(h.tail(28).units.mean()); r['on_hand']=int(mu*8); r['capacity']=int(max(100,mu*40))
    cache={}
    for day in pd.date_range(start,sales.date.max()):
        c=Context(str((day-pd.Timedelta(days=1)).date()),[],sales[sales.date<day],products,vendors)
        DemandForecastingAgent().run(c); cache[day]=c.forecasts
    totals=[]; daily=[]
    for policy in ['threshold30','agentic']:
        stock=copy.deepcopy(initial); orders=[]
        total=dict(policy=policy,demand=0,served=0,lost_units=0,holding_inr=0.,
                   shortage_inr=0.,procurement_inr=0.,transfer_inr=0.,order_count=0)
        for day in pd.date_range(start,sales.date.max()):
            today=str(day.date()); previous=str((day-pd.Timedelta(days=1)).date())
            for o in orders:
                if o['status']=='OPEN' and o['eta']<=today:
                    r=next(r for r in stock if (r['sku'],r['warehouse'])==(o['sku'],o['warehouse']))
                    r['on_hand']+=o['quantity']; o['status']='RECEIVED'
            c=Context(previous,stock,sales[sales.date<day],products,vendors,orders)
            c.forecasts=copy.deepcopy(cache[day]); InventoryTrackingAgent().run(c)
            if policy=='agentic':
                for a in [StockoutRiskAgent,WarehouseOptimizationAgent,ReorderDecisionAgent,
                          VendorIntelligenceAgent,CostOptimizationAgent,PurchaseOrderAgent]: a().run(c)
                orders.extend(c.purchases); total['transfer_inr']+=sum(t['quantity']*2 for t in c.transfers)
                buys=c.purchases
            else:
                buys=[]
                for r in stock:
                    if r['position']<.3*r['capacity']:
                        v=min([v for v in vendors if v['sku']==r['sku']],key=lambda x:x['unit_price_inr'])
                        q=max(v['min_qty'],int(np.ceil((.7*r['capacity']-r['position'])/5))*5)
                        q=min(q,v['max_qty'])
                        price=v['unit_price_inr']*(.97 if q>=500 else 1)
                        if q*price+100>c.budget: continue
                        o=dict(sku=r['sku'],warehouse=r['warehouse'],quantity=q,
                               eta=str((day+pd.Timedelta(days=v['lead_days']-1)).date()),status='OPEN',
                               total_inr=q*price+100,kind='PURCHASE')
                        buys.append(o); orders.append(o)
            total['order_count']+=len(buys); total['procurement_inr']+=sum(o['total_inr'] for o in buys)
            lost_today=0
            for r in stock:
                units=int(sales[(sales.date==day)&(sales.sku==r['sku'])&(sales.warehouse==r['warehouse'])].units.iloc[0])
                served=min(units,r['on_hand']); lost=units-served; r['on_hand']-=served
                total['demand']+=units; total['served']+=served; total['lost_units']+=lost; lost_today+=lost
                total['shortage_inr']+=lost*products[r['sku']]['stockout_penalty_inr']
                total['holding_inr']+=r['on_hand']*products[r['sku']]['holding_cost_day_inr']
            daily.append(dict(date=today,policy=policy,on_hand=sum(r['on_hand'] for r in stock),lost_units=lost_today))
        total['fill_rate']=total['served']/max(1,total['demand'])
        total['operating_cost_inr']=total['holding_inr']+total['shortage_inr']+total['transfer_inr']
        total['closing_on_hand']=sum(r['on_hand'] for r in stock)
        total['open_pipeline_units']=sum(o['quantity'] for o in orders if o['status']=='OPEN')
        totals.append(total)
    pd.DataFrame(totals).to_csv(out/'policy_comparison.csv',index=False)
    pd.DataFrame(daily).to_csv(out/'policy_daily.csv',index=False)
    agg=[]
    for model,g in pd.DataFrame(metrics).groupby('model'):
        agg.append(dict(model=model,wape=float(g.absolute_error.sum()/g.actual_units.sum()),
                        mae=float(g.mae.mean())))
    summary=dict(forecast=agg,policy=totals,
                 forecast_test_start='2011-11-03',forecast_test_end='2011-11-30',
                 validation_start='2011-10-06',validation_end='2011-11-02',
                 replay_start=str(start.date()),replay_end='2011-11-30',
                 caveat='Replay uses historical observed sales as demand, simulated procurement assumptions and one 56-day window. Operating cost excludes purchase cost, reported separately. Policies end with different inventories; no net profit claim.')
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))

if __name__=='__main__': evaluate()

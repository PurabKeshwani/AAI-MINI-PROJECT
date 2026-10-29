"""Prepare a small, attributed UCI dataset. Warehouse/vendor fields are simulated."""
from pathlib import Path
import hashlib, json, zipfile, urllib.request
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SOURCE = 'https://archive.ics.uci.edu/static/public/352/online+retail.zip'
CODES = ['85123A', '85099B', '22423', '47566', '20725']

def prepare(source_path=None):
    data = ROOT / 'data'; data.mkdir(exist_ok=True)
    if source_path is None:
        source_path = ROOT / 'source.zip'
        urllib.request.urlretrieve(SOURCE, source_path)
    with zipfile.ZipFile(source_path) as z:
        with z.open('Online Retail.xlsx') as f: raw = pd.read_excel(f)
    raw['StockCode'] = raw.StockCode.astype(str)
    mask = ((raw.Country == 'United Kingdom') & raw.StockCode.isin(CODES)
            & ~raw.InvoiceNo.astype(str).str.upper().str.startswith('C')
            & (raw.Quantity > 0) & (raw.UnitPrice > 0)
            & (raw.InvoiceDate >= '2011-01-01') & (raw.InvoiceDate < '2011-12-01'))
    clean = raw.loc[mask].copy()
    before = len(clean)
    clean = clean.drop_duplicates()
    clean['date'] = clean.InvoiceDate.dt.normalize()
    dates = pd.date_range('2011-01-01', '2011-11-30')
    products, sales, stock, vendors = [], [], [], []
    shares = [.5, .3, .2]
    for j, sku in enumerate(CODES):
        part = clean[clean.StockCode == sku]
        name = part.Description.mode().iloc[0]
        series = part.groupby('date').Quantity.sum().reindex(dates, fill_value=0)
        mean = float(series.tail(56).mean())
        # These are scenario quotes in INR, not a conversion of UCI sterling prices.
        base = [90, 65, 340, 180, 75][j]
        products.append(dict(sku=sku, name=name, selling_price_inr=round(base*1.8),
                             holding_cost_day_inr=.5, stockout_penalty_inr=base*1.5))
        for day, n in series.items():
            allocated = [int(n*.5), int(n*.3)]
            allocated.append(int(n)-sum(allocated))
            for w, q in enumerate(allocated):
                sales.append(dict(date=day.date().isoformat(), sku=sku,
                                  warehouse=f'WH-{w+1}', units=q, weekday=day.dayofweek))
        for w, share in enumerate(shares):
            m = max(1, mean*share)
            cover = [1.8, 27, 3.0][w] if j % 2 == 0 else [4, 8, 22][w]
            stock.append(dict(sku=sku,warehouse=f'WH-{w+1}',on_hand=int(m*cover),
                              reserved=0,capacity=int(max(100,m*40))))
        for v, (label, price, lead, reliability) in enumerate([
            ('Value Supplies', base*.92, 6, .90),
            ('Rapid Logistics', base*1.08, 2, .98),
            ('Balanced Traders', base, 4, .96)]):
            vendors.append(dict(vendor_id=f'V{v+1}',name=label,sku=sku,
                                unit_price_inr=round(price,2),lead_days=lead,
                                reliability=reliability,min_qty=10,lot_size=5,
                                max_qty=5000,shipping_inr=100,
                                discount_threshold=500,discount_rate=.03))
    for name, rows in [('sales',sales),('products',products),('inventory',stock),('vendors',vendors)]:
        pd.DataFrame(rows).to_csv(data/f'{name}.csv',index=False)
    meta = dict(source_name='UCI Online Retail', creator='Daqing Chen',
                source_url=SOURCE,doi='https://doi.org/10.24432/C5BW33',
                license='CC BY 4.0',source_rows=len(raw),selected_rows=len(clean),
                exact_duplicates_removed=before-len(clean),daily_rows=len(sales),
                start='2011-01-01',end='2011-11-30',skus=CODES,
                source_sha256=hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
                actual_fields=['SKU','product description','daily positive sales aggregated from UCI'],
                simulated_fields=['warehouse allocation 50/30/20 with conserved units','inventory',
                                  'capacities','vendors','INR prices and costs','lead times','reliability'],
                limitations=['Observed sales are not unconstrained demand',
                             'Zero-sales days may reflect closure or missing activity',
                             'No observed promotions, supplier, warehouse or stockout labels',
                             'Five fixed SKUs illustrate a system, not a representative retail sample'])
    (data/'metadata.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(meta,indent=2))

if __name__ == '__main__':
    import sys
    prepare(sys.argv[1] if len(sys.argv)>1 else None)

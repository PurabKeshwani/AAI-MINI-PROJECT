"""Local dashboard and SQLite simulation adapter. No supplier API or real orders."""
import copy,csv,io,json,sqlite3,threading
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse
from agents import *
import numpy as np

DB=ROOT/'supplychain.db'
LOCK=threading.RLock()
def connect():
    db=sqlite3.connect(DB)
    db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, payload TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, payload TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS cycles (cycle_key TEXT PRIMARY KEY)')
    return db

def reset():
    sales,stock,products,vendors=load_inputs()
    s=dict(date='2011-11-30',inventory=stock,orders=[],log=[],risks=[],proposals=[],
           forecasts=[],anomalies=[],last_action='Ready. Run the agent cycle.',simulated_days=0)
    with connect() as db:
        for table in ['state','orders','cycles']: db.execute('DELETE FROM '+table)
        db.execute('INSERT INTO state VALUES(1,?)',(json.dumps(s),))
    return s

def state():
    with connect() as db:
        row=db.execute('SELECT payload FROM state WHERE id=1').fetchone()
    return json.loads(row[0]) if row else reset()

def store(s,db): db.execute('UPDATE state SET payload=? WHERE id=1',(json.dumps(s),))

def cycle(multiplier=1.,delay=0):
    with LOCK,connect() as db:
        s=state(); key=s['date'] # One committed procurement cycle per simulated day.
        if db.execute('SELECT 1 FROM cycles WHERE cycle_key=?',(key,)).fetchone():
            s['last_action']='Cycle already executed for this day. Advance a day or reset for a new scenario.'; return s
        sales,_,products,vendors=load_inputs()
        # Future simulated days keep the last real history, with an explicit time shift.
        shift=(pd.Timestamp(s['date'])-sales.date.max()).days
        sales = sales.copy()
        sales['date'] = (
            sales['date'].to_numpy(dtype='datetime64[ns]') + np.timedelta64(int(shift), 'D')
        )
        c=Context(s['date'],copy.deepcopy(s['inventory']),sales,products,vendors,
                  copy.deepcopy(s['orders']),float(multiplier),int(delay))
        MasterOrchestrator().run(c)
        new=c.transfers+c.purchases
        for i,o in enumerate(new):
            o['id']=f"{'TR' if o['kind']=='TRANSFER' else 'PO'}-{s['date'].replace('-','')}-{i+1:03d}"
            db.execute('INSERT INTO orders VALUES(?,?)',(o['id'],json.dumps(o)))
        s.update(inventory=c.inventory,orders=c.orders+c.purchases,log=c.log,risks=c.risks,
                 proposals=c.proposals,anomalies=c.anomalies,
                 forecasts=[dict(sku=k[0],warehouse=k[1],**v) for k,v in c.forecasts.items()],
                 last_action=f'Executed {len(c.purchases)} simulated POs and {len(c.transfers)} transfer orders.')
        db.execute('INSERT INTO cycles VALUES(?)',(key,)); store(s,db)
        return s

def advance():
    with LOCK,connect() as db:
        s=state(); day=pd.Timestamp(s['date'])+pd.Timedelta(days=1); s['date']=str(day.date())
        received=0
        for o in s['orders']:
            if o['status']=='OPEN' and o['eta']<=s['date']:
                r=next(r for r in s['inventory'] if (r['sku'],r['warehouse'])==(o['sku'],o['warehouse']))
                r['on_hand']+=o['quantity']; o['status']='RECEIVED'; received+=o['quantity']
                db.execute('UPDATE orders SET payload=? WHERE id=?',(json.dumps(o),o['id']))
        consumed=0; lost=0
        for r in s['inventory']:
            f=next((f for f in s['forecasts'] if (f['sku'],f['warehouse'])==(r['sku'],r['warehouse'])),None)
            demand=int(round(f['mean'])) if f else 0
            q=min(demand,r['on_hand']-r['reserved']); r['on_hand']-=q; consumed+=q; lost+=demand-q
        s['simulated_days']+=1; s['risks']=[]; s['proposals']=[]
        s['last_action']=f'Simulated day: received {received}, served {consumed}, unmet {lost} units. Demand is forecast-derived, not new real sales.'
        store(s,db); return s

def public(s):
    sales,_,products,_=load_inputs()
    s=copy.deepcopy(s); s['products']=list(products.values())
    s['history']=sales.groupby('date').units.sum().tail(42).reset_index().assign(date=lambda x:x.date.dt.strftime('%Y-%m-%d')).to_dict('records')
    s['evaluation']=json.loads((ROOT/'results/summary.json').read_text()) if (ROOT/'results/summary.json').exists() else {}
    return s

class Handler(BaseHTTPRequestHandler):
    def respond(self,body,status=200,kind='application/json'):
        body=body if isinstance(body,bytes) else body.encode()
        self.send_response(status); self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/api/state': return self.respond(json.dumps(public(state())))
        if path=='/api/orders.csv':
            out=io.StringIO(); fields=['id','kind','sku','warehouse','vendor','quantity','total_inr','eta','status']
            w=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(state()['orders'])
            return self.respond(out.getvalue(),kind='text/csv')
        if path=='/': return self.respond((ROOT/'static/index.html').read_bytes(),kind='text/html; charset=utf-8')
        self.respond('Not found',404,'text/plain')
    def do_POST(self):
        try:
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}')
            if self.path=='/api/cycle':
                m=float(body.get('multiplier',1)); d=int(body.get('delay',0))
                if not (.5<=m<=3 and 0<=d<=5): raise ValueError('Scenario outside supported limits')
                s=cycle(m,d)
            elif self.path=='/api/advance': s=advance()
            elif self.path=='/api/reset':
                with LOCK: s=reset()
            else: return self.respond('Not found',404,'text/plain')
            self.respond(json.dumps(public(s)))
        except (ValueError,TypeError,json.JSONDecodeError) as e: self.respond(json.dumps({'error':str(e)}),400)
    def log_message(self,*args): pass

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8000); a=p.parse_args()
    print(f'Local simulation dashboard: http://127.0.0.1:{a.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()

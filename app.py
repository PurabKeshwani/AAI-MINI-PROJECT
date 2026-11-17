"""Serve the operations workspace and a separate case-study workspace."""
import copy,csv,io,json,sqlite3,threading
from contextlib import closing, contextmanager
from datetime import date, timedelta
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from agents import *
from operations import Conflict, HEADERS, csv_export, operations, practice
import numpy as np

DB=ROOT/'supplychain.db'
LOCK=threading.RLock()
ASSETS = {
    '/tokens.css': 'text/css',
    '/workspace.css': 'text/css',
    '/workspace.js': 'text/javascript',
    '/operations-ui.js': 'text/javascript',
    '/fonts/bricolage.woff2': 'font/woff2',
    '/fonts/plex-mono.woff2': 'font/woff2',
}
OPERATION_ACTIONS = {
    'import': 'import_data',
    'settings': 'configure',
    'activity': 'stock_activity',
    'plan': 'plan',
    'release': 'release',
    'receipt': 'receipt',
    'cancel': 'cancel_purchase',
}
MAX_REQUEST_SIZE = 5 * 1024 * 1024


def workspace_route(path):
    for prefix, workspace in (('/api/operations/', operations), ('/api/practice/', practice)):
        if path.startswith(prefix):
            return workspace, path.removeprefix(prefix)
    return None, None
@contextmanager
def connect():
    with closing(sqlite3.connect(DB)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS cycles (cycle_key TEXT PRIMARY KEY)')
        yield db

def reset():
    sales,stock,products,vendors=load_inputs()
    s=dict(date='2011-11-30',inventory=stock,orders=[],log=[],risks=[],proposals=[],
           forecasts=[],anomalies=[],last_action='Ready. Run the agent cycle.',simulated_days=0)
    with connect() as db:
        for table in ['state','orders','cycles']: db.execute('DELETE FROM '+table)
        db.execute('INSERT INTO state VALUES(1,?)',(json.dumps(s),))
    return s

def state():
    with LOCK, connect() as db:
        row=db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        if row is None:
            return reset()
        snapshot = json.loads(row[0])
        if snapshot.get('log') and 'last_run' not in snapshot:
            # Ledgers created before scenario metadata was saved still have cycle dates.
            last_cycle = db.execute('SELECT MAX(cycle_key) FROM cycles').fetchone()[0]
            if last_cycle:
                snapshot['last_run'] = {'date': last_cycle}
        return snapshot

def store(s,db): db.execute('UPDATE state SET payload=? WHERE id=1',(json.dumps(s),))

def cycle(multiplier=1.,delay=0):
    with LOCK,connect() as db:
        s=state(); key=s['date'] # One committed procurement cycle per simulated day.
        if db.execute('SELECT 1 FROM cycles WHERE cycle_key=?',(key,)).fetchone():
            s['last_action']='Cycle already executed for this day. Advance a day or reset for a new scenario.'; return s
        sales,_,products,vendors=load_inputs()
        # Future simulated days keep the last real history, with an explicit time shift.
        shift=(date.fromisoformat(s['date'])-sales.date.max().date()).days
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
        s['last_run'] = dict(date=key, multiplier=multiplier, delay=delay,
                             high_risks=sum(r['level'] == 'HIGH' for r in c.risks),
                             proposals=len(c.proposals), purchases=len(c.purchases),
                             transfers=len(c.transfers))
        db.execute('INSERT INTO cycles VALUES(?)',(key,)); store(s,db)
        return s

def advance():
    with LOCK,connect() as db:
        s=state(); s['date']=(date.fromisoformat(s['date'])+timedelta(days=1)).isoformat()
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
        s['last_day'] = dict(date=s['date'], received=received, served=consumed, unmet=lost)
        s['last_action']=f'Simulated day: received {received}, served {consumed}, unmet {lost} units. Demand is forecast-derived, not new real sales.'
        store(s,db); return s

def public(s):
    sales,_,products,_=load_inputs()
    s=copy.deepcopy(s); s['products']=list(products.values())
    s['history']=sales.groupby('date').units.sum().tail(42).reset_index().assign(date=lambda x:x.date.dt.strftime('%Y-%m-%d')).to_dict('records')
    s['evaluation']=json.loads((ROOT/'results/summary.json').read_text()) if (ROOT/'results/summary.json').exists() else {}
    return s

class Handler(BaseHTTPRequestHandler):
    def respond(self,body,status=200,kind='application/json',download=None):
        body=body if isinstance(body,bytes) else body.encode()
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def error(self, message, status):
        return self.respond(json.dumps({'error': message}), status)

    def local_request(self):
        host = self.headers.get('Host')
        port = self.server.server_port
        origin = self.headers.get('Origin')
        return host in (f'localhost:{port}', f'127.0.0.1:{port}') and (origin is None or origin == f'http://{host}')

    def operation_get(self, workspace, endpoint, params):
        if not self.local_request():
            return self.error('Open the workspace using its localhost address', 403)
        try:
            if endpoint == 'template.csv':
                sheet = params.get('type', [''])[0]
                if sheet not in HEADERS:
                    return self.error('Unknown data template', 404)
                return self.respond(csv_export([], HEADERS[sheet]), kind='text/csv; charset=utf-8', download=f'{sheet}.csv')
            if workspace is practice:
                workspace.initialize()
            if endpoint == 'state':
                return self.respond(json.dumps(workspace.state()))
            if endpoint == 'orders.csv':
                order_id = params.get('id', [None])[0]
                return self.respond(workspace.export_orders(order_id), kind='text/csv; charset=utf-8',
                                    download='order.csv' if order_id else 'orders.csv')
            return self.error('Unknown workspace endpoint', 404)
        except ValueError as error:
            return self.error(str(error), 404)
        except sqlite3.OperationalError:
            return self.error('Could not read the workspace database', 503)

    def operation_post(self, workspace, endpoint):
        if not self.local_request():
            return self.error('Open the workspace using its localhost address', 403)
        action = OPERATION_ACTIONS.get(endpoint)
        if workspace is practice and endpoint == 'restore':
            action = 'restore'
        if not action:
            return self.error('Unknown workspace action', 404)
        if self.headers.get_content_type() != 'application/json':
            return self.error('Workspace data must be JSON', 415)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_REQUEST_SIZE:
                return self.error('The workspace request must contain at most 5 MB of data', 413)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Workspace data must be a JSON object')
            if workspace is practice:
                workspace.initialize()
            method = getattr(workspace, action)
            result = method() if action in ('plan', 'restore') else method(body)
            return self.respond(json.dumps(result))
        except Conflict as error:
            return self.error(str(error), 409)
        except (ValueError, TypeError, csv.Error) as error:
            return self.error(str(error), 400)
        except sqlite3.OperationalError:
            return self.error('Could not write to the workspace database', 503)

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        workspace, endpoint = workspace_route(path)
        if workspace:
            return self.operation_get(workspace, endpoint, parse_qs(url.query))
        if path=='/api/state': return self.respond(json.dumps(public(state())))
        if path=='/api/orders.csv':
            out=io.StringIO(); fields=['id','kind','sku','warehouse','vendor','quantity','total_inr','eta','status']
            w=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(state()['orders'])
            return self.respond(out.getvalue(),kind='text/csv')
        if path in ('/', '/sample', '/practice'):
            page = (ROOT / 'static/index.html').read_text(encoding='utf-8')
            if path != '/':
                page = page.replace('data-mode="operations"', f'data-mode="{path[1:]}"', 1)
            return self.respond(page, kind='text/html; charset=utf-8')
        if path in ASSETS:
            asset = ROOT / 'static' / path.lstrip('/')
            if asset.is_file():
                return self.respond(asset.read_bytes(),kind=ASSETS[path])
        self.respond('Not found',404,'text/plain')
    def do_POST(self):
        workspace, endpoint = workspace_route(urlparse(self.path).path)
        if workspace:
            return self.operation_post(workspace, endpoint)
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
    print(f'SupplyMind: http://127.0.0.1:{a.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()

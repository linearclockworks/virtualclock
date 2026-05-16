#!/usr/bin/python3
import argparse, base64, io, os, sys, json, re, threading, webbrowser, time

print("--- Initializing Industrial Clockworks Build System (v13.0) ---")
try:
    from PIL import Image
    from http.server import HTTPServer, BaseHTTPRequestHandler
except ImportError as e:
    sys.exit(f"Missing dependency: {e}. Run: pip install pillow")

# --- Shopify & App Config ---
SHOPIFY_SHOP    = os.environ.get('SHOPIFY_SHOP_NAME', 'linear-clockworks.myshopify.com')
SHOPIFY_TOKEN   = os.environ.get('SHOPIFY_ACCESS_TOKEN', '')
SHOPIFY_API_VER = '2024-01'

# All pct-based values (0-100). nudge6am/3pm/Mid estimated from product photo pixel positions.
DEFAULTS = {
    "rotation": 0.14,
    "trackYFrac": 38.0,
    "nudge6am": 5.5,
    "nudge3pm": 50.0,
    "nudgeMid": 92.0,
    "ptrRatio": 47.0,
    "nudgeDotsY": 31.0,
    "nudgePtrLumeY": 32.0,
    "ptrNudgeX": -1.2,
    "demoSpeed": 2.3,
    "nightDim": 0.46,
    "version": 13.0
}
TEMPLATE = os.path.join(os.path.dirname(__file__), 'clock_template.html')
REQ_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}

def shopify_graphql(query, variables=None):
    import requests as _requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    url = f'https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VER}/graphql.json'
    session = _requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    try:
        r = session.post(url, headers={'X-Shopify-Access-Token': SHOPIFY_TOKEN, 'Content-Type': 'application/json', **REQ_HEADERS}, 
                         json={'query': query, 'variables': variables or {}}, timeout=30)
        r.raise_for_status()
        return r.json().get('data', {})
    except Exception as e:
        sys.exit(f"  [Error] Shopify Connection Failed: {e}")

def find_product_by_sku(sku, cached_id=None):
    if cached_id:
        print(f"  [API] Fast-tracking lookup via cached ID: {cached_id}...")
        query = """query($id: ID!) { product(id: $id) { id title handle images(first: 20) { edges { node { url } } } } }"""
        data = shopify_graphql(query, {'id': cached_id})
        p = data.get('product')
        if p: return p
        print("  [API] Cached ID no longer valid, falling back to SKU search...")

    print(f"  [API] Querying SKU: {sku}...")
    query = """query($q: String!) { productVariants(first: 1, query: $q) { edges { node { sku product { id title handle images(first: 20) { edges { node { url } } } } } } } }"""
    data = shopify_graphql(query, {'q': f'sku:{sku}'})
    edges = data.get('productVariants', {}).get('edges', [])
    if not edges: return None
    p = edges[0]['node']['product']
    return {
        'id': p['id'], 
        'title': p['title'], 
        'handle': p.get('handle'), 
        'images': [e['node']['url'] for e in p['images']['edges']]
    }

def get_friendly_name(title):
    name = re.split(r'[:|,]', title)[0].strip()
    return re.sub(r'[^\w\s-]', '', name).replace(' ', '-')

def straightening_process(img):
    import numpy as np
    import cv2
    print("  [OpenCV] Leveling wood grain horizon...")
    cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, 100, minLineLength=100, maxLineGap=10)
    if lines is None: return img
    angles = [np.degrees(np.arctan2(l[0][3]-l[0][1], l[0][2]-l[0][0])) for l in lines]
    return img.rotate(np.median(angles), resample=Image.BICUBIC, expand=True)

def strip_calibration(html, product_url="#"):
    hide_css = """<style>#cal-bar, .tool-btn[onclick*='showSetup'], #setup-overlay { display: none !important; }</style>"""
    html = html.replace('</head>', hide_css + '</head>')
    return html

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sku')
    ap.add_argument('--calibrate', action='store_true')
    args = ap.parse_args()
    
    sku = args.sku.upper()
    cal_json = f'{sku}-cal.json'
    
    cached_id = None
    if os.path.exists(cal_json):
        try:
            with open(cal_json, 'r') as j:
                cached_id = json.load(j).get('shopify_id')
        except: pass

    product = find_product_by_sku(sku, cached_id=cached_id)
    if not product: sys.exit(f"SKU {sku} not found")
    
    fname = get_friendly_name(product['title'])
    f_cache, p_cache = f'{sku}_face.png', f'{sku}_ptr.png'

    if not os.path.exists(f_cache) or not os.path.exists(p_cache):
        import requests as _requests
    if not os.path.exists(f_cache):
        print(f"  [Download] Fetching face image...")
        img_data = _requests.get(product['images'][5], headers=REQ_HEADERS).content
        img = Image.open(io.BytesIO(img_data)).convert('RGB')
        straightening_process(img).save(f_cache)
        print(f"  [Cache] Saved {f_cache}")
    if not os.path.exists(p_cache):
        print(f"  [Download] Fetching pointer image...")
        ptr_data = _requests.get(product['images'][4], headers=REQ_HEADERS).content
        Image.open(io.BytesIO(ptr_data)).convert('RGBA').save(p_cache)
        print(f"  [Cache] Saved {p_cache}")

    f_b64 = base64.b64encode(open(f_cache, 'rb').read()).decode()
    p_b64 = base64.b64encode(open(p_cache, 'rb').read()).decode()

    with open(TEMPLATE, 'r') as f: html = f.read()
    html = html.replace('FACE_DATA_URI', f'data:image/png;base64,{f_b64}').replace('PTR_DATA_URI', f'data:image/png;base64,{p_b64}')
    
    # Merge cal JSON over DEFAULTS
    cal = DEFAULTS.copy()
    if os.path.exists(cal_json):
        with open(cal_json, 'r') as j:
            saved_data = json.load(j)
        if saved_data.get('version') == 13.0:
            cal.update(saved_data)
            print(f"  [Cal] Loaded v13 calibration from {cal_json}")
        else:
            print(f"  [Cal] Ignoring {cal_json} (version={saved_data.get('version','none')}, not v13) — using DEFAULTS")

    cal['productSku'] = sku
    cal['version'] = 13.0

    # Inject cal values — simple sentinel replace, no regex needed
    html = html.replace('CAL_JSON_HERE', json.dumps(cal, indent=2))

    if args.calibrate:
        cal_out = f'{sku}_calibrate.html'
        done_bar = f'<div id="cal-bar" style="position:fixed;top:0;left:0;right:0;background:#111;color:#fff;padding:10px;z-index:10000;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #c8a96e;font-family:sans-serif;"><span>Calibrating: {sku}</span><button onclick="sendDone()" style="background:#c8a96e;border:none;padding:5px 15px;cursor:pointer;font-weight:bold;color:#000;">SAVE TO PWA</button></div>'
        done_bar += '<script>function sendDone(){ fetch("/done",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(window.__wipCal||{})}).then(()=>window.close()); }</script>'
        html = html.replace('</body>', done_bar + '</body>')
        with open(cal_out, 'w') as f: f.write(html)
        
        cal_result = []
        class H(BaseHTTPRequestHandler):
            def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(open(cal_out, 'rb').read())
            def do_POST(self):
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length) if length else b''
                try:
                    cal_result.append(json.loads(body))
                except json.JSONDecodeError:
                    print(f"  [Warning] Empty or invalid POST body, ignoring")
                self.send_response(200); self.end_headers()
            def log_message(self, *a): pass
        
        server = HTTPServer(('localhost', 19888), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        webbrowser.open('http://localhost:19888')
        while not cal_result: time.sleep(0.5)
        
        save_data = cal_result[0]
        save_data['shopify_id'] = product['id']
        with open(cal_json, 'w') as j: json.dump(save_data, j, indent=2)
        
        server.shutdown(); server.server_close()
        os.system(f"python3 {sys.argv[0]} {args.sku}") 
    else:
        html = strip_calibration(html, f"https://{SHOPIFY_SHOP}/products/{product['handle']}")
        cv = int(time.time())
        sw_name, mn_name = f"{fname}-sw.js", f"{fname}.webmanifest"
        
        pwa_head = f'<link rel="manifest" href="./{mn_name}"><meta name="theme-color" content="#0d0b08"><link rel="apple-touch-icon" href="data:image/png;base64,{f_b64}"><script>if(\'serviceWorker\' in navigator){{window.addEventListener(\'load\',()=>{{navigator.serviceWorker.register(\'./{sw_name}\');}});}}</script>'
        html = html.replace('</head>', pwa_head + '</head>')
        
        sw_content = f"const CACHE=\'lc-{fname}-{cv}\';self.addEventListener(\'install\',e=>{{e.waitUntil(caches.open(CACHE).then(c=>c.add(new Request(\'./{fname}.html\',{{cache:\'reload\'}}))));self.skipWaiting();}});self.addEventListener(\'activate\',e=>{{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k.startsWith(\'lc-{fname}-\')&&k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim();}});self.addEventListener(\'fetch\',e=>{{if(e.request.url.endsWith(\'.html\')||e.request.url.endsWith(\'/\')){{e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)));}}}});"
        manifest = {"name":f"Linear Clock {fname}","short_name":fname,"start_url":f"./{fname}.html","display":"standalone","background_color":"#0d0b08","theme_color":"#c8a96e","icons":[{"src":f"data:image/png;base64,{f_b64}","sizes":"512x512","type":"image/png"}]}
        
        with open(f"{fname}.html", 'w') as f: f.write(html)
        with open(sw_name, 'w') as f: f.write(sw_content)
        with open(mn_name, 'w') as f: json.dump(manifest, f, indent=2)
        
        print(f"\n[Success] PWA Build Complete: {fname}")
        print("-" * 45)
        print(f"URL: https://linearclockworks.github.io/virtualclock/{fname}.html")
        print(f"git add {fname}.html {sw_name} {mn_name}")
        print(f'git commit -m "{fname} teaching clock build"')
        print(f"git push")
        print("-" * 45)

if __name__ == '__main__': main()
#!/usr/bin/env python3
import argparse, base64, io, os, sys, json, re, threading, webbrowser, time

print("--- Initializing Industrial Clockworks Build System ---")
try:
    import requests as _requests
    from PIL import Image
    import numpy as np
    import cv2
    from http.server import HTTPServer, BaseHTTPRequestHandler
except ImportError as e:
    sys.exit(f"Missing dependency: {e}. Please install requests, pillow, numpy, and opencv-python.")

# --- Shopify config ---
SHOPIFY_SHOP    = os.environ.get('SHOPIFY_SHOP_NAME', 'linear-clockworks.myshopify.com')
SHOPIFY_TOKEN   = os.environ.get('SHOPIFY_ACCESS_TOKEN', '')
SHOPIFY_API_VER = os.environ.get('SHOPIFY_API_VERSION', '2023-01')

DEFAULTS = dict(leftFrac=0.12, rightFrac=0.852, trackYFrac=0.554, nudge6am=0, nudge3pm=0, nudgeMid=0, sweepSpeed=1.0, ptrRatio=0.28)
TEMPLATE = os.path.join(os.path.dirname(__file__), 'clock_template.html')

def shopify_graphql(query, variables=None):
    url = f'https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VER}/graphql.json'
    r = _requests.post(url, headers={'X-Shopify-Access-Token': SHOPIFY_TOKEN, 'Content-Type': 'application/json'}, json={'query': query, 'variables': variables or {}}, timeout=60)
    r.raise_for_status()
    return r.json().get('data', {})

def find_product_by_sku(sku):
    print(f"  [API] Querying Shopify for SKU: {sku}...")
    query = """query($q: String!) { productVariants(first: 1, query: $q) { edges { node { sku product { title handle images(first: 20) { edges { node { url } } } } } } } }"""
    data = shopify_graphql(query, {'q': f'sku:{sku}'})
    edges = data.get('productVariants', {}).get('edges', [])
    if not edges: return None
    p = edges[0]['node']['product']
    return {'title': p['title'], 'handle': p.get('handle'), 'images': [e['node']['url'] for e in p['images']['edges']]}

def get_friendly_name(title):
    # Splits by : or , and takes the first part (e.g. "Rufino: Chechen" -> "Rufino")
    name = re.split(r'[:|,]', title)[0].strip()
    # Safe filename: remove non-alphanumeric, replace spaces with hyphens
    return re.sub(r'[^\w\s-]', '', name).replace(' ', '-')

def straightening_process(img):
    print("  [OpenCV] Detecting skew and straightening source image...")
    cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, 100, minLineLength=100, maxLineGap=10)
    if lines is None: return img
    angles = [np.degrees(np.arctan2(l[0][3]-l[0][1], l[0][2]-l[0][0])) for l in lines if -45 < np.degrees(np.arctan2(l[0][3]-l[0][1], l[0][2]-l[0][0])) < 45]
    if not angles: return img
    return img.rotate(np.median(angles), resample=Image.BICUBIC, expand=True)

def strip_calibration(html, product_url="#"):
    """Surgical strip: Hides UI and fixes Demo behavior for final build."""
    # 1. CSS to hide gear and calibration UI
    hide_css = """
<style>
  #setup-overlay, #cal-bar, .tool-btn[onclick*='showSetup'] { display: none !important; }
  .demo-group { display: flex; align-items: center; gap: 8px; margin-left: 10px; border-left: 1px solid var(--muted); padding-left: 10px; }
  #toolbar-demo-speed { width: 60px; accent-color: var(--gold); cursor: pointer; }
</style>
"""
    html = html.replace('</head>', hide_css + '</head>')
    
    # 2. Inject Production Demo UI (Replacing the Gear button area)
    demo_ui = f"""
  <div class="demo-group">
    <button class="tool-btn" id="toolbar-demo-btn" onclick="startSweep()" style="font-family:'Playfair Display',serif; font-size:0.85rem; opacity:1; color:var(--gold);">Demo</button>
    <input type="range" id="toolbar-demo-speed" min="0.5" max="5" step="0.5" value="1" title="Speed">
    <a href="{product_url}" target="_blank" class="tool-btn" style="text-decoration:none; color:var(--muted); font-size:0.85rem; margin-left:8px;">Details</a>
  </div>"""
    html = html.replace('</div>\n\n<script>', demo_ui + '\n</div>\n\n<script>')

    # 3. Fix JS references for production Demo
    html = html.replace("document.getElementById('sweep-btn')", "document.getElementById('toolbar-demo-btn')")
    html = html.replace("document.getElementById('sl-speed')", "document.getElementById('toolbar-demo-speed')")
    
    return html

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sku')
    ap.add_argument('--calibrate', action='store_true')
    args = ap.parse_args()
    
    sku = args.sku.upper()
    product = find_product_by_sku(sku)
    if not product: sys.exit(f"SKU {sku} not found")
    
    fname = get_friendly_name(product['title'])
    cal_json = f'{sku}-cal.json'

    # Assets
    f_cache, p_cache = f'{sku}_face.png', f'{sku}_ptr.png'
    if not os.path.exists(f_cache):
        img = Image.open(io.BytesIO(_requests.get(product['images'][5]).content)).convert('RGB')
        straightening_process(img).save(f_cache)
    if not os.path.exists(p_cache):
        Image.open(io.BytesIO(_requests.get(product['images'][4]).content)).convert('RGBA').save(p_cache)

    f_b64 = base64.b64encode(open(f_cache, 'rb').read()).decode()
    p_b64 = base64.b64encode(open(p_cache, 'rb').read()).decode()

    with open(TEMPLATE, 'r') as f: html = f.read()
    html = html.replace('FACE_DATA_URI', f'data:image/png;base64,{f_b64}').replace('PTR_DATA_URI', f'data:image/png;base64,{p_b64}')
    
    cal = DEFAULTS.copy()
    if os.path.exists(cal_json):
        with open(cal_json, 'r') as j: cal.update(json.load(j))

    html = re.sub(r'let PTR_H_RATIO = [\d.]+;', f'let PTR_H_RATIO = {cal.get("ptrRatio", 0.28):.4f};', html)
    html = re.sub(r'const DEFAULTS = \{[^}]+\};', f"const DEFAULTS = {json.dumps(cal)};", html)

    if args.calibrate:
        cal_out = f'{sku}_calibrate.html'
        done_bar = f'<div id="cal-bar" style="position:fixed;top:0;left:0;right:0;background:#111;color:#fff;padding:10px;z-index:10000;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #c8a96e;font-family:sans-serif;"><span>Calibrating: {sku}</span><button onclick="sendDone()" style="background:#c8a96e;border:none;padding:5px 15px;cursor:pointer;font-weight:bold;color:#000;">DONE → SAVE & REBUILD</button></div>'
        done_bar += '<script>function sendDone(){ fetch("/done",{method:"POST",body:localStorage.getItem("lc_cal")}).then(()=>window.close()); }</script>'
        html = html.replace('</body>', done_bar + '</body>')
        with open(cal_out, 'w') as f: f.write(html)
        
        cal_result = []
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(open(cal_out, 'rb').read())
            def do_POST(self):
                cal_result.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.end_headers()
            def log_message(self, *a): pass

        class ThreadedHTTPServer(HTTPServer):
            allow_reuse_address = True

        server = ThreadedHTTPServer(('localhost', 19888), H)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        
        webbrowser.open('http://localhost:19888')
        while not cal_result: time.sleep(0.5)
        
        with open(cal_json, 'w') as j: json.dump(cal_result[0], j, indent=2)
        
        server.shutdown()
        server.server_close()
        
        print(f"Calibration saved. Re-running build for production...")
        os.system(f"python3 {sys.argv[0]} {args.sku}") 
    else:
        # Production PWA Build
        html = strip_calibration(html, f"https://{SHOPIFY_SHOP}/products/{product['handle']}")
        cv = int(time.time())
        sw_name, mn_name = f"{fname}-sw.js", f"{fname}.webmanifest"
        
        pwa_head = f"""
  <link rel="manifest" href="./{mn_name}">
  <meta name="theme-color" content="#0d0b08">
  <link rel="apple-touch-icon" href="data:image/png;base64,{f_b64}">
  <script>if ('serviceWorker' in navigator) {{ window.addEventListener('load', () => {{ navigator.serviceWorker.register('./{sw_name}'); }}); }}</script>
"""
        html = html.replace('</head>', pwa_head + '</head>')
        
        sw_content = f"const CACHE = 'lc-{fname}-{cv}';\nself.addEventListener('install', e => {{ e.waitUntil(caches.open(CACHE).then(c => c.add(new Request('./{fname}.html', {{cache: 'reload'}})))); self.skipWaiting(); }});\nself.addEventListener('activate', e => {{ e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith('lc-{fname}-') && k !== CACHE).map(k => caches.delete(k))))); self.clients.claim(); }});\nself.addEventListener('fetch', e => {{ if (e.request.url.endsWith('.html') || e.request.url.endsWith('/')) {{ e.respondWith(caches.match(e.request).then(r => r || fetch(e.request))); }} }});"
        
        manifest = { "name": f"Linear Clock {fname}", "short_name": fname, "start_url": f"./{fname}.html", "display": "standalone", "background_color": "#0d0b08", "theme_color": "#c8a96e", "icons": [{"src": f"data:image/png;base64,{f_b64}", "sizes": "512x512", "type": "image/png"}] }

        with open(f"{fname}.html", 'w') as f: f.write(html)
        with open(sw_name, 'w') as f: f.write(sw_content)
        with open(mn_name, 'w') as f: json.dump(manifest, f, indent=2)

        print(f"\n[Success] PWA Build Complete: {fname}")
        print("-" * 45)
        print(f"git add {fname}.html {sw_name} {mn_name}")
        print(f"git commit -m \"{fname} build\"")
        print(f"git push")
        print("-" * 45)

if __name__ == '__main__':
    main()
#!/usr/bin/env python3
import argparse, base64, io, os, sys, json, re, threading, webbrowser, time

# Move heavy imports here so logging can start immediately
print("--- Initializing Industrial Clockworks Build System ---")
import requests as _requests
from PIL import Image
import numpy as np
import cv2
from http.server import HTTPServer, BaseHTTPRequestHandler

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
    print("  [Build] Finalizing production code...")
    # 1. Hide Setup UI via CSS (Preserves DOM for Pointer Anchoring)
    hide_css = """
<style>
  #setup-overlay, #cal-bar, #sliders, .setup-row { display: none !important; }
  .tool-btn[onclick*='showSetup'] { display: none !important; }
  #toolbar-demo-speed { width: 60px; accent-color: var(--gold); cursor: pointer; margin-left: 5px; }
  .demo-group { display: flex; align-items: center; gap: 4px; margin-left: 10px; border-left: 1px solid var(--muted); padding-left: 10px; }
</style>
"""
    html = html.replace('</head>', hide_css + '</head>')

    # 2. Repair loadCal (Marker-to-Marker replacement)
    marker_start = 'function loadCal()'
    marker_end = 'function saveCal()'
    s_idx = html.find(marker_start)
    e_idx = html.find(marker_end)
    if s_idx != -1 and e_idx != -1:
        new_func = "function loadCal() { return Object.assign({}, DEFAULTS); }\n"
        html = html[:s_idx] + new_func + html[e_idx:]

    # 3. Demo Controls injection (Literal "Demo" text + Speed Slider)
    demo_controls = """
  <div class="demo-group">
    <button class="tool-btn" id="toolbar-demo-btn" onclick="startSweep()" style="font-family:'Playfair Display',serif; font-size:0.85rem; opacity:1; color:var(--gold);">Demo</button>
    <input type="range" id="toolbar-demo-speed" min="0.5" max="5" step="0.5" value="1">
  </div>"""

    # 4. Details Link & Toolbar injection
    link = f'  <a href="{product_url}" target="_blank" class="tool-btn" style="text-decoration:none; color:var(--muted); font-size:0.85rem; margin-left:8px;">Details</a>'
    html = html.replace('</div>\n\n<script>', demo_controls + "\n" + link + '\n</div>\n\n<script>')

    # 5. Production JS Adjustments
    html = html.replace("btn.textContent = 'Stop';", "btn.textContent = 'Stop';")
    html = html.replace("btn.textContent = 'Demo';", "btn.textContent = 'Demo';")
    html = html.replace("document.getElementById('sweep-btn')", "document.getElementById('toolbar-demo-btn')")
    html = html.replace(
        "const s = parseFloat(document.getElementById('sl-speed').value) || 1;",
        "const s = parseFloat(document.getElementById('toolbar-demo-speed').value) || 1;"
    )
    return html

def main(override_args=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('sku')
    ap.add_argument('--calibrate', action='store_true')
    args = ap.parse_args(override_args)
    
    sku = args.sku.upper()
    cal_json = f'{sku}-cal.json'
    
    product = find_product_by_sku(sku)
    if not product: sys.exit(f"SKU {sku} not found")

    # Image processing and Base64 encoding
    face_cache, ptr_cache = f'{sku}_face.png', f'{sku}_ptr.png'
    if not os.path.exists(face_cache):
        img = Image.open(io.BytesIO(_requests.get(product['images'][5]).content)).convert('RGB')
        straightening_process(img).save(face_cache)
    
    if not os.path.exists(ptr_cache):
        img = Image.open(io.BytesIO(_requests.get(product['images'][4]).content)).convert('RGBA')
        img.save(ptr_cache)

    face_b64 = base64.b64encode(open(face_cache, 'rb').read()).decode()
    ptr_b64 = base64.b64encode(open(ptr_cache, 'rb').read()).decode()

    # Load Template
    with open(TEMPLATE, 'r') as f: html = f.read()
    html = html.replace('FACE_DATA_URI', f'data:image/png;base64,{face_b64}').replace('PTR_DATA_URI', f'data:image/png;base64,{ptr_b64}')
    
    # Range expansion for 6AM calibration
    html = html.replace('id="sl-6am" min="-4" max="4"', 'id="sl-6am" min="-10" max="10"')

    # Bake sidecar calibration data
    cal = DEFAULTS.copy()
    if os.path.exists(cal_json):
        with open(cal_json, 'r') as j:
            cal.update(json.load(j))
            print(f"  [Build] Applying sidecar calibration: {cal_json}")

    # Static Geometry Baking
    html = re.sub(r'let PTR_H_RATIO = [\d.]+;', f'let PTR_H_RATIO = {cal.get("ptrRatio", 0.28):.4f};', html)
    html = re.sub(r'const DEFAULTS = \{[^}]+\};', f"const DEFAULTS = {json.dumps(cal)};", html)

    if args.calibrate:
        print(f"  [Action] Launching calibration UI...")
        cal_out = f'{sku}_calibrate.html'
        done_bar = f'<div id="cal-bar" style="position:fixed;top:0;left:0;right:0;background:#000;color:#fff;padding:10px;z-index:9999;display:flex;justify-content:space-between;align-items:center;font-family:monospace;border-bottom:1px solid #c8a96e;"><span>{sku}</span><button onclick="sendDone()" style="background:#c8a96e;border:none;padding:8px 20px;cursor:pointer;font-weight:bold;color:#000;">DONE → SAVE & REBUILD</button></div>'
        done_bar += '<script>function sendDone(){ fetch("/done",{method:"POST",body:localStorage.getItem("lc_cal")}).then(()=>window.close()); }</script>'
        html = html.replace('</body>', done_bar + '</body>')
        with open(cal_out, 'w') as f: f.write(html)
        
        cal_result = []
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers()
                self.wfile.write(open(cal_out, 'rb').read())
            def do_POST(self):
                cal_result.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.end_headers()
            def log_message(self, *a): pass

        server = HTTPServer(('localhost', 19888), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        webbrowser.open('http://localhost:19888')
        
        while not cal_result: time.sleep(0.5)
        with open(cal_json, 'w') as j: json.dump(cal_result[0], j, indent=2)
        server.shutdown()
        print(f"  [Success] Calibration saved. Finalizing build pass...")
        main([sku])
    else:
        # ── PRODUCTION PWA BUILD ──
        html = strip_calibration(html, f"https://{SHOPIFY_SHOP}/products/{product.get('handle', '')}")
        
        cache_ver = int(time.time())
        sw_name = f"{sku}-sw.js"
        manifest_name = f"{sku}.webmanifest"

        # 1. Inject PWA Metadata and SW Registration
        pwa_head = f"""
  <link rel="manifest" href="./{manifest_name}">
  <meta name="theme-color" content="#0d0b08">
  <link rel="apple-touch-icon" href="data:image/png;base64,{face_b64}">
  <script>
    if ('serviceWorker' in navigator) {{
      window.addEventListener('load', () => {{
        navigator.serviceWorker.register('./{sw_name}');
      }});
    }}
  </script>
"""
        html = html.replace('</head>', pwa_head + '</head>')

        # 2. Generate Service Worker (Using relative request for offline caching)
        sw_content = f"""// Cache version: {cache_ver}
const CACHE = 'lc-{sku}-{cache_ver}';
self.addEventListener('install', e => {{
  e.waitUntil(caches.open(CACHE).then(c => c.add(new Request('./{sku}.html', {{cache: 'reload'}}))));
  self.skipWaiting();
}});
self.addEventListener('activate', e => {{
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => k.startsWith('lc-{sku}-') && k !== CACHE).map(k => caches.delete(k))
  )));
  self.clients.claim();
}});
self.addEventListener('fetch', e => {{
  if (e.request.url.endsWith('.html') || e.request.url.endsWith('/')) {{
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }}
}});"""

        # 3. Generate Manifest
        manifest = {
            "name": f"Linear Clock {sku}",
            "short_name": sku,
            "start_url": f"./{sku}.html",
            "display": "standalone",
            "background_color": "#0d0b08",
            "theme_color": "#c8a96e",
            "icons": [{"src": f"data:image/png;base64,{face_b64}", "sizes": "512x512", "type": "image/png"}]
        }

        # Write files
        with open(f"{sku}.html", 'w') as f: f.write(html)
        with open(sw_name, 'w') as f: f.write(sw_content)
        with open(manifest_name, 'w') as f: json.dump(manifest, f, indent=2)
        
        print(f"\n[Success] PWA Build Complete:")
        print(f"  - {sku}.html")
        print(f"  - {sw_name}")
        print(f"  - {manifest_name}")

if __name__ == '__main__':
    main()
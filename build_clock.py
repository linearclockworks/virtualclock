#!/usr/bin/env python3
import argparse, base64, io, os, sys, json, re, threading, webbrowser, time
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
    print("  [OpenCV] Detecting skew and straightening image...")
    cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, 100, minLineLength=100, maxLineGap=10)
    if lines is None: return img
    angles = [np.degrees(np.arctan2(l[0][3]-l[0][1], l[0][2]-l[0][0])) for l in lines if -45 < np.degrees(np.arctan2(l[0][3]-l[0][1], l[0][2]-l[0][0])) < 45]
    if not angles: return img
    return img.rotate(np.median(angles), resample=Image.BICUBIC, expand=True)

def strip_calibration(html, product_url="#"):
    """Surgical strip: Hides UI, bakes calibration, and adds Demo + Speed controls."""
    print("  [Build] Finalizing production code...")

    # 1. Hide the setup UI logic
    hide_css = """
<style>
  #setup-overlay, #cal-bar, #sliders, .setup-row { display: none !important; }
  .tool-btn[onclick*='showSetup'] { display: none !important; }
  #toolbar-demo-speed { width: 70px; accent-color: var(--gold); cursor: pointer; margin: 0 5px; }
  .demo-label { font-size: 0.8rem; color: var(--muted); font-family: monospace; }
</style>
"""
    html = html.replace('</head>', hide_css + '</head>')

    # 2. Fix the loadCal function
    marker_start = 'function loadCal()'
    marker_end = 'function saveCal()'
    s_idx = html.find(marker_start)
    e_idx = html.find(marker_end)
    if s_idx != -1 and e_idx != -1:
        new_func = "function loadCal() { return Object.assign({}, DEFAULTS); }\n"
        html = html[:s_idx] + new_func + html[e_idx:]

    # 3. Create the Demo Controls
    # Includes a speed slider that defaults to 1x and goes to 5x
    demo_controls = """
  <div style="display:flex; align-items:center; gap:5px; margin-left:10px; border-left:1px solid var(--muted); padding-left:10px;">
    <button class="tool-btn" id="toolbar-demo-btn" title="Demo Mode" onclick="startSweep()" style="font-family:'Playfair Display',serif; font-weight:600; font-size:0.9rem;">Demo</button>
    <input type="range" id="toolbar-demo-speed" min="0.5" max="5" step="0.5" value="1" title="Demo Speed">
  </div>"""

    # 4. Inject Details link and Demo controls
    details_link = f'  <a href="{product_url}" target="_blank" class="tool-btn" style="text-decoration:none; color:var(--muted); font-size:0.9rem; margin-left:8px;">Details</a>'
    html = html.replace('</div>\n\n<script>', demo_controls + "\n" + details_link + '\n</div>\n\n<script>')

    # 5. Update the JS to use the toolbar speed slider
    # This modifies the glide speed and the pause (800ms) to scale by the slider value
    html = html.replace("btn.textContent = '◼ Stop';", "btn.textContent = 'Stop';")
    html = html.replace("btn.textContent = '▶ Sweep';", "btn.textContent = 'Demo';")
    
    # Target the new toolbar elements
    html = html.replace("document.getElementById('sweep-btn')", "document.getElementById('toolbar-demo-btn')")
    
    # Logic to pull speed from the new toolbar slider
    html = html.replace(
        "const s = parseFloat(document.getElementById('sl-speed').value) || 1;",
        "const s = parseFloat(document.getElementById('toolbar-demo-speed').value) || 1;"
    )
    
    # Ensure the stop button logic targets the correct ID
    html = html.replace("btn.onclick = stopSweep;", "document.getElementById('toolbar-demo-btn').onclick = stopSweep;")

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

    # Image caching
    face_cache, ptr_cache = f'{sku}_face.png', f'{sku}_ptr.png'
    if not os.path.exists(face_cache):
        img = Image.open(io.BytesIO(_requests.get(product['images'][5]).content)).convert('RGB')
        straightening_process(img).save(face_cache)
    
    if not os.path.exists(ptr_cache):
        img = Image.open(io.BytesIO(_requests.get(product['images'][4]).content)).convert('RGBA')
        img.save(ptr_cache)

    face_b64 = base64.b64encode(open(face_cache, 'rb').read()).decode()
    ptr_b64 = base64.b64encode(open(ptr_cache, 'rb').read()).decode()

    with open(TEMPLATE, 'r') as f: html = f.read()
    html = html.replace('FACE_DATA_URI', f'data:image/png;base64,{face_b64}').replace('PTR_DATA_URI', f'data:image/png;base64,{ptr_b64}')
    
    # Set the 6AM nudge range
    html = html.replace('id="sl-6am" min="-4" max="4"', 'id="sl-6am" min="-10" max="10"')

    # Apply calibration from sidecar JSON
    cal = DEFAULTS.copy()
    if os.path.exists(cal_json):
        with open(cal_json, 'r') as j:
            cal.update(json.load(j))
            print(f"  [Build] Applying calibration: {cal_json}")

    # BAKE GEOMETRY
    html = re.sub(r'let PTR_H_RATIO = [\d.]+;', f'let PTR_H_RATIO = {cal.get("ptrRatio", 0.28):.4f};', html)
    html = re.sub(r'const DEFAULTS = \{[^}]+\};', f"const DEFAULTS = {json.dumps(cal)};", html)

    if args.calibrate:
        cal_out = f'{sku}_calibrate.html'
        done_bar = f'<div id="cal-bar" style="position:fixed;top:0;left:0;right:0;background:#111;color:#fff;padding:12px;z-index:10000;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #c8a96e;font-family:monospace;"><span>BUILDING: {sku}</span><button onclick="sendDone()" style="background:#c8a96e;border:none;padding:8px 24px;cursor:pointer;font-weight:bold;">DONE → SAVE & REBUILD</button></div>'
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
        print(f"  [Build] Settings saved. Triggering final pass...")
        main([sku])
    else:
        html = strip_calibration(html, f"https://{SHOPIFY_SHOP}/products/{product.get('handle', '')}")
        with open(f'{sku}.html', 'w') as f: f.write(html)
        print(f"  [Success] Build Complete: {sku}.html")

if __name__ == '__main__':
    main()
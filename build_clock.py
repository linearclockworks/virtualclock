#!/usr/bin/env python3
"""
build_clock.py — generate a calibrated virtual clock HTML from a Shopify serial number.

Fetches product images 6 & 7 (1-indexed) from the Shopify product matching the
given serial number (SKU), then bakes them + calibration into a self-contained HTML.

Usage:
    python3 build_clock.py LCK-1021
    python3 build_clock.py LCK-1021 --left 0.145 --right 0.852 --track 0.554 --nudge3pm 0.0

Requires env vars (or .env file):
    SHOPIFY_SHOP     e.g. linear-clockworks.myshopify.com
    SHOPIFY_TOKEN    Admin API access token

Output: LCK-1021.html  (or --out override)
"""

import argparse, base64, io, os, sys, json, re
import requests as _requests
from PIL import Image
import numpy as np

# ── Shopify config ──────────────────────────────────────────────────────────
SHOPIFY_SHOP    = os.environ.get('SHOPIFY_SHOP_NAME', 'linear-clockworks.myshopify.com')
SHOPIFY_TOKEN   = os.environ.get('SHOPIFY_ACCESS_TOKEN', '')
SHOPIFY_API_VER = os.environ.get('SHOPIFY_API_VERSION', '2023-01')

# ── Clock model defaults (baked in; user can still nudge via ⚙) ─────────────
DEFAULTS = dict(
    leftFrac   = 0.145,
    rightFrac  = 0.852,
    trackYFrac = 0.554,
    nudge3pm   = 0.0,
    sweepSpeed = 1.0,
)

TEMPLATE = os.path.join(os.path.dirname(__file__), 'clock_template.html')

# ── Shopify helpers ──────────────────────────────────────────────────────────

def shopify_graphql(query, variables=None):
    url = f'https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VER}/graphql.json'
    r = _requests.post(url, headers={
        'X-Shopify-Access-Token': SHOPIFY_TOKEN,
        'Content-Type': 'application/json',
    }, json={'query': query, 'variables': variables or {}}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if 'errors' in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data['data']

def find_product_by_sku(sku):
    """Find product by SKU via GraphQL — single query, works across 1000s of products."""
    query = """
    query($query: String!) {
      productVariants(first: 1, query: $query) {
        edges {
          node {
            sku
            product {
              title
              images(first: 20) {
                edges { node { url } }
              }
            }
          }
        }
      }
    }
    """
    data = shopify_graphql(query, {'query': f'sku:{sku}'})
    edges = data.get('productVariants', {}).get('edges', [])
    if not edges:
        return None
    product = edges[0]['node']['product']
    return {
        'title':  product['title'],
        'images': [{'src': e['node']['url']}
                   for e in product['images']['edges']],
    }

def fetch_image(url):
    """Download image from URL, return PIL Image."""
    r = _requests.get(url, headers={'User-Agent': 'LinearClockworks/1.0'}, timeout=30)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert('RGB')

# ── Image processing ─────────────────────────────────────────────────────────

def make_transparent(img, threshold=15):
    arr = np.array(img)
    h, w = arr.shape[:2]
    alpha = np.ones((h, w), dtype=np.uint8) * 255
    from collections import deque
    visited = np.zeros((h, w), dtype=bool)
    def flood(seeds):
        q = deque(seeds)
        for p in seeds:
            visited[p[0], p[1]] = True
            alpha[p[0], p[1]] = 0
        while q:
            r, c = q.popleft()
            for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]:
                nr, nc = r+dr, c+dc
                if 0<=nr<h and 0<=nc<w and not visited[nr,nc]:
                    if arr[nr,nc].max() <= threshold:
                        visited[nr,nc] = True
                        alpha[nr,nc] = 0
                        q.append((nr,nc))
    seeds = set()
    for c in range(w):
        if arr[0,c].max() <= threshold: seeds.add((0,c))
        if arr[h-1,c].max() <= threshold: seeds.add((h-1,c))
    for r in range(h):
        if arr[r,0].max() <= threshold: seeds.add((r,0))
        if arr[r,w-1].max() <= threshold: seeds.add((r,w-1))
    flood(list(seeds))
    return Image.fromarray(np.dstack([arr, alpha]), 'RGBA')

def encode_png(img, max_width):
    w, h = img.size
    if w > max_width:
        img = img.resize((max_width, int(h * max_width / w)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode(), img  # return resized image too

def compute_ptr_ratio(ptr_rgba, face_rgba, track_y_frac):
    """
    Compute PTR_TIP_Y from the pointer image geometry.
    PTR_TIP_Y = tip row / pointer image height (fraction of image height where tip sits).

    PTR_H_RATIO is NOT auto-computed here — it is always set to a safe default
    (0.28) so the pointer visually peeks above the face without dominating it.
    The calibrate UI pointer-size slider is the right way to dial it in per clock.
    """
    import numpy as np

    pa = np.array(ptr_rgba)
    ph, pw = pa.shape[:2]
    alpha = pa[:,:,3]

    # Content top row
    p_rows = np.any(alpha > 10, axis=1)
    ct_row = int(np.where(p_rows)[0][0]) if p_rows.any() else 0
    ct_frac = ct_row / ph

    # Tip = bottommost non-transparent pixel near horizontal centre
    cols = np.any(alpha > 10, axis=0)
    if cols.any():
        cmin, cmax = int(np.where(cols)[0][0]), int(np.where(cols)[0][-1])
        cx = (cmin + cmax) // 2
    else:
        cx = pw // 2
    tf_row = ct_row
    for row in range(ph - 1, -1, -1):
        if alpha[row, cx] > 10:
            tf_row = row
            break
    tf_frac = tf_row / ph   # PTR_TIP_Y

    # Safe default ratio: pointer height = 28% of face height.
    # This is a conservative starting point — use the pointer-size slider
    # in --calibrate mode to fine-tune for each individual clock.
    R = 0.28

    print(f"  PTR_TIP_Y={tf_frac:.4f}  content_top={ct_frac:.4f}")
    print(f"  PTR_H_RATIO={R:.4f}  (default — adjust with pointer-size slider in calibrate mode)")
    return R, tf_frac, ct_frac

# ── Main ─────────────────────────────────────────────────────────────────────


import re

def strip_calibration(html):
    """Remove setup overlay, gear/clock buttons, calibration JS from customer build."""

    # 1. Remove setup overlay block entirely
    html = re.sub(
        r'<!-- ── Setup overlay ── -->.*?</div>\n(?=\n*<!-- ── Toolbar)',
        '', html, flags=re.DOTALL)

    # 2. Replace full toolbar with sweep-only controls
    old_toolbar = re.search(
        r'<!-- ── Toolbar ── -->\n<div id="toolbar">.*?</div>\n',
        html, flags=re.DOTALL)
    if old_toolbar:
        html = html[:old_toolbar.start()] + \
            '<!-- ── Toolbar ── -->\n' \
            '<div id="toolbar" style="position:fixed;bottom:12px;right:14px;display:flex;gap:8px;align-items:center;z-index:20;">\n' \
            '  <button class="tool-btn" id="toggle-time-btn" title="Toggle time" onclick="toggleTime()">&#128336;</button>\n' \
            '  <button class="tool-btn" id="sweep-btn" onclick="startSweep()">&#9654; Sweep</button>\n' \
            '  <input type="range" id="sl-speed" min="0.25" max="4" step="0.25" value="1"\n' \
            '         title="Sweep speed" oninput="onSl(\'speed\',this.value)"\n' \
            '         style="width:90px;accent-color:var(--gold);cursor:pointer;vertical-align:middle;">\n' \
            '  <span id="val-speed" style="color:var(--gold);font-size:0.82rem;font-family:monospace;">1×</span>\n' \
            '</div>\n' \
            + html[old_toolbar.end():]

    # 3. Remove calibration-only JS functions
    for pattern in [

        r'function livePreview\(frac\).*?\n\}\n',
        r'function showSetup\(\).*?\n\}\n',
        r'function hideSetup\(\).*?\n\}\n',
        r'function resetSetup\(\).*?\n\}\n',
        r'function finishSetup\(\).*?\n\}\n',
        r'function syncSlidersFromWip\(\).*?\n\}\n',
        r'function onAnchor\(.*?\n\}\n',
        r'function goAnchor\(.*?\}\n',
    ]:
        html = re.sub(pattern, '', html, flags=re.DOTALL)

    # In customer build: loadCal just returns DEFAULTS (cal is baked in, ignore localStorage)
    # Replace loadCal — match full multi-line function by counting braces
    lc_start = html.find('function loadCal()')
    if lc_start >= 0:
        depth, i = 0, lc_start
        while i < len(html):
            if html[i] == '{': depth += 1
            elif html[i] == '}':
                depth -= 1
                if depth == 0:
                    html = html[:lc_start] + 'function loadCal() { return Object.assign({}, DEFAULTS); }' + html[i+1:]
                    break
            i += 1
    # Also remove saveCal since localStorage unused in customer build
    html = re.sub(r'function saveCal\(\)\s*\{[^}]*\}\n?', '', html)

    return html


def main():
    import re as _re
    ap = argparse.ArgumentParser(description='Build calibrated clock HTML from Shopify SKU')
    ap.add_argument('sku',          help='Serial number / SKU e.g. LCK-1021')
    ap.add_argument('--left',       type=float, help='6AM position fraction')
    ap.add_argument('--right',      type=float, help='Midnight position fraction')
    ap.add_argument('--track',      type=float, help='Track Y fraction')
    ap.add_argument('--nudge3pm',   type=float, help='3PM parallax nudge %%')
    ap.add_argument('--speed',      type=float, default=1.0)
    ap.add_argument('--face-index', type=int,   default=6, help='1-indexed image position for face (default 6)')
    ap.add_argument('--ptr-index',  type=int,   default=7, help='1-indexed image position for pointer (default 7)')
    ap.add_argument('--out',        default=None)
    ap.add_argument('--calibrate',  action='store_true',
                    help='Generate calibration HTML (with full setup UI) instead of customer HTML')
    ap.add_argument('--template',   default=TEMPLATE)
    args = ap.parse_args()

    if not SHOPIFY_TOKEN:
        print('ERROR: Set SHOPIFY_ACCESS_TOKEN env var (Admin API access token)')
        sys.exit(1)

    sku = args.sku.strip().upper()
    out = args.out or f'{sku}.html'

    # Calibration values — prefer sidecar JSON if it exists and no CLI overrides given
    cal_json_path = f'{sku}-cal.json'
    saved_cal = {}
    if os.path.exists(cal_json_path):
        with open(cal_json_path) as _cj:
            saved_cal = json.load(_cj)
        print(f'  Loaded calibration from {cal_json_path}')

    def _cv(cli_val, key, default):
        """CLI override > saved cal > DEFAULTS."""
        if cli_val is not None: return cli_val
        if key in saved_cal:    return saved_cal[key]
        return default

    left     = _cv(args.left,     'leftFrac',   DEFAULTS['leftFrac'])
    right    = _cv(args.right,    'rightFrac',  DEFAULTS['rightFrac'])
    track    = _cv(args.track,    'trackYFrac', DEFAULTS['trackYFrac'])
    n3pm     = _cv(args.nudge3pm, 'nudge3pm',   DEFAULTS['nudge3pm'])
    n6am     = saved_cal.get('nudge6am',  0)
    nmid     = saved_cal.get('nudgeMid',  0)
    speed    = args.speed

    # ── Fetch product from Shopify ──
    print(f'Looking up SKU: {sku} …')
    product = find_product_by_sku(sku)
    if not product:
        print(f'ERROR: No product found with SKU {sku}')
        sys.exit(1)

    title  = product['title']
    images = product.get('images', [])
    print(f'Found: "{title}" with {len(images)} image(s)')

    fi = args.face_index - 1   # 0-indexed
    pi = args.ptr_index  - 1
    if fi >= len(images):
        print(f'ERROR: Product only has {len(images)} images; face index {args.face_index} out of range')
        sys.exit(1)
    if pi >= len(images):
        print(f'ERROR: Product only has {len(images)} images; pointer index {args.ptr_index} out of range')
        sys.exit(1)

    face_url = images[fi]['src']
    ptr_url  = images[pi]['src']
    print(f'Face image  (#{args.face_index}): {face_url}')
    print(f'Pointer image (#{args.ptr_index}): {ptr_url}')

    # ── Download & process images (cache as SKU_face.png / SKU_ptr.png) ──
    face_cache = f'{sku}_face.png'
    ptr_cache  = f'{sku}_ptr.png'
    if os.path.exists(face_cache):
        print(f'Using cached {face_cache}')
        face_img = Image.open(face_cache)
    else:
        print('Downloading face …')
        face_img = make_transparent(fetch_image(face_url))
        face_img.save(face_cache)
        print(f'  Saved {face_cache}')
    if os.path.exists(ptr_cache):
        print(f'Using cached {ptr_cache}')
        ptr_img = Image.open(ptr_cache)
    else:
        print('Downloading pointer …')
        ptr_img = make_transparent(fetch_image(ptr_url))
        ptr_img.save(ptr_cache)
        print(f'  Saved {ptr_cache}')
    face_b64, face_web = encode_png(face_img, 1200)
    ptr_b64,  ptr_web  = encode_png(ptr_img, 338)
    print(f'  face:{len(face_b64)//1024}KB  ptr:{len(ptr_b64)//1024}KB')

    # Compute PTR_TIP_Y from pointer image geometry; load ptrRatio from sidecar if available
    try:
        ptr_ratio_computed, ptr_tip_y, ptr_ct_y = compute_ptr_ratio(ptr_web, face_web, track)
        ptr_ratio = saved_cal.get('ptrRatio', ptr_ratio_computed)
        print(f'  PTR_H_RATIO={ptr_ratio:.4f}  PTR_TIP_Y={ptr_tip_y:.4f}')
        if 'ptrRatio' in saved_cal:
            print(f'  (ptrRatio from saved calibration)')
    except Exception as e:
        print(f'  Warning: ptr_ratio failed ({e}), using defaults')
        ptr_ratio = saved_cal.get('ptrRatio', 0.28)
        ptr_tip_y, ptr_ct_y = 0.9217, 0.0483
    print(f'  Tip X frac: 0.5207 (default)')

    # ── Build HTML ──
    with open(args.template, 'r') as f:
        html = f.read()

    html = html.replace('FACE_DATA_URI', f'data:image/png;base64,{face_b64}')
    html = html.replace('PTR_DATA_URI',  f'data:image/png;base64,{ptr_b64}')

    # Patch PTR_H_RATIO and PTR_TIP_Y with computed values
    html = _re.sub(r'(?:const|let) PTR_H_RATIO\s*=\s*[\d.]+;',
                   f'let PTR_H_RATIO = {ptr_ratio:.4f};   // ptr height = this × face image height (corrected for wood extent)', html)
    html = _re.sub(r'const PTR_TIP_Y\s*=\s*[\d.]+;',
                   f'const PTR_TIP_Y   = {ptr_tip_y:.4f};', html)

    # Replace DEFAULTS const
    _def = (f"const DEFAULTS = {{ leftFrac:{left}, rightFrac:{right}, "
            f"trackYFrac:{track}, nudge6am:{n6am}, nudge3pm:{n3pm}, "
            f"nudgeMid:{nmid}, sweepSpeed:{speed}, ptrRatio:{ptr_ratio:.4f} }};")
    html = _re.sub(r'const DEFAULTS = \{[^}]+\};', _def, html, flags=_re.DOTALL)

    # Title + manifest
    safe = re.sub(r'[^a-zA-Z0-9 \-]', '', title)
    html = html.replace('<title>Linear Clockworks</title>',
                        f'<title>Linear Clockworks — {safe}</title>')
    # Update manifest start_url and name to match this clock's URL
    import base64 as _b64
    mf_m = _re.search(r'href="data:application/manifest\+json;base64,([^"]+)"', html)
    if mf_m:
        import json as _json
        mf = _json.loads(_b64.b64decode(mf_m.group(1)).decode())
        mf['start_url'] = f'/{out}'
        mf['name'] = f'Linear Clockworks — {safe}'
        mf['short_name'] = sku
        new_mf_b64 = _b64.b64encode(_json.dumps(mf).encode()).decode()
        html = html[:mf_m.start()] + f'href="data:application/manifest+json;base64,{new_mf_b64}"' + html[mf_m.end():]

    if not args.calibrate:
        html = strip_calibration(html)
        # Inject PWA service worker registration
        sw_js_name = out.replace('.html', '-sw.js')
        sw_js_url  = sw_js_name  # same directory
        html = html.replace('</body>',
            f'<script>\nif ("serviceWorker" in navigator) {{\n'
            f'  window.addEventListener("load", () => {{\n'
            f'    navigator.serviceWorker.register("{sw_js_url}")\n'
            f'      .catch(e => console.log("SW reg failed:", e));\n'
            f'  }});\n'
            f'}}\n'
            f'</script>\n</body>')
        with open(out, 'w') as f:
            f.write(html)
        # Write companion service worker
        cache_key = out.replace('.html', '').replace('/', '-')
        sw_content = (
            f"""const CACHE = 'lc-{cache_key}-v1';
self.addEventListener('install', e => {{
  e.waitUntil(caches.open(CACHE).then(c => c.add('/{out}')));
  self.skipWaiting();
}});
self.addEventListener('activate', e => {{
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
}});
self.addEventListener('fetch', e => {{
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
}});
""")
        with open(sw_js_name, 'w') as f:
            f.write(sw_content)
        print(f'\n✓ {out}  ({os.path.getsize(out)//1024} KB)')
        print(f'  {sw_js_name}  (service worker — deploy alongside HTML)')
        print(f'  6AM={left}  Midnight={right}  TrackY={track}  Nudge3PM={n3pm}')
        print(f'  Host at: https://linearclockworks.github.io/CTlight/{out}')
    else:
        # Calibration mode: inject Done bar, open browser, local server waits,
        # then generates final customer HTML automatically.
        cal_out = args.out or f'{sku}_calibrate.html'
        done_bar = (
            '<div id="cal-bar" style="position:fixed;top:0;left:0;right:0;'
            'background:rgba(13,11,8,0.95);border-bottom:1px solid #c8a96e;'
            'padding:10px 20px;display:flex;align-items:center;gap:16px;z-index:100;">'
            '<span style="font-family:monospace;font-size:0.85rem;color:#c8a96e;opacity:0.7;">'
            f'Calibrating: <strong style="color:#e8c98e;">{sku}</strong></span>'
            '<span style="font-family:monospace;font-size:0.78rem;color:#7a6a52;">'
            'Use &#9881; then:</span>'
            '<button onclick="sendDone()" style="background:transparent;border:1px solid #c8a96e;'
            'color:#c8a96e;padding:6px 20px;font-family:monospace;font-size:0.85rem;'
            'cursor:pointer;border-radius:2px;" id="done-cal-btn">'
            'Done &#8594; Generate clock</button>'
            '<span id="cal-status" style="color:#8c8;font-size:0.8rem;'
            'font-family:monospace;margin-left:8px;"></span>'
            '</div>'
            '<script>'
            'const CALIBRATE_MODE=true;'
            'function sendDone(){'
            '  const c=JSON.parse(localStorage.getItem("lc_cal")||"{}");'
            '  document.getElementById("done-cal-btn").textContent="Generating...";'
            '  fetch("/done",{'
            '    method:"POST",'
            '    headers:{"Content-Type":"application/json"},'
            '    body:JSON.stringify(c)'
            '  }).then(r=>r.text()).then(msg=>{'
            '    document.getElementById("cal-status").textContent=msg;'
            '    document.getElementById("done-cal-btn").textContent="Done";'
            '  }).catch(e=>{'
            '    document.getElementById("cal-status").textContent="Error: "+e;'
            '    document.getElementById("done-cal-btn").textContent="Done -> Generate clock";'
            '  });'
            '}'
            '</script>'
        )
        # Raise setup overlay above cal bar
        html = html.replace('z-index:50;padding:20px;', 'z-index:200;padding:76px 20px 20px;')
        html = html.replace('</body>', done_bar + '\n</body>')
        with open(cal_out, 'w') as f:
            f.write(html)
        print(f'\n✓ {cal_out}  — opening in browser...')

        import threading, webbrowser, time
        from http.server import HTTPServer, BaseHTTPRequestHandler

        cal_result = {}

        # Serve the calibrate HTML via HTTP so fetch() to /done works (file:// blocks it)
        import os as _os
        cal_dir  = _os.path.dirname(_os.path.abspath(cal_out))
        cal_name = _os.path.basename(cal_out)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/' or self.path == '/' + cal_name:
                    with open(_os.path.join(cal_dir, cal_name), 'rb') as _f:
                        data = _f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_response(404); self.end_headers()
            def do_POST(self):
                if self.path == '/done':
                    data = json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
                    cal_result.update(data)
                    self.send_response(200)
                    self.send_header('Access-Control-Allow-Origin','*')
                    self.end_headers()
                    self.wfile.write(b'Generating final HTML...')
                else:
                    self.send_response(404); self.end_headers()
            def do_OPTIONS(self):
                self.send_response(200)
                for h,v in [('Access-Control-Allow-Origin','*'),
                             ('Access-Control-Allow-Methods','GET,POST,OPTIONS'),
                             ('Access-Control-Allow-Headers','Content-Type')]:
                    self.send_header(h,v)
                self.end_headers()
            def log_message(self,*a): pass

        server = HTTPServer(('localhost', 19888), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://localhost:19888/{cal_name}'
        webbrowser.open(url)
        print(f'Serving at {url}')
        print('Waiting for Done click...')

        while not cal_result:
            time.sleep(0.3)
        server.shutdown()

        # Save calibration to sidecar so plain build can reuse it
        cal_json_path = f'{sku}-cal.json'
        with open(cal_json_path, 'w') as _cj:
            json.dump(cal_result, _cj, indent=2)
        print(f'  Saved calibration → {cal_json_path}')

        # Use returned cal values
        left  = cal_result.get('leftFrac',   left)
        right = cal_result.get('rightFrac',  right)
        track = cal_result.get('trackYFrac', track)
        n3pm  = cal_result.get('nudge3pm',   n3pm)
        n6am  = cal_result.get('nudge6am',   0)
        nmid  = cal_result.get('nudgeMid',   0)
        ptr_ratio = cal_result.get('ptrRatio', ptr_ratio)
        print(f'  6AM={left:.4f}  Midnight={right:.4f}  TrackY={track:.4f}  Nudge3PM={n3pm:.2f}  Nudge6AM={n6am:.2f}  NudgeMid={nmid:.2f}')

        # Build final customer HTML
        with open(args.template, 'r') as f:
            html2 = f.read()
        html2 = html2.replace('FACE_DATA_URI', f'data:image/png;base64,{face_b64}')
        html2 = html2.replace('PTR_DATA_URI',  f'data:image/png;base64,{ptr_b64}')
        html2 = _re.sub(r'(?:const|let) PTR_H_RATIO\s*=\s*[\d.]+;',
                        f'let PTR_H_RATIO = {ptr_ratio:.4f};   // ptr height = this × face image height (corrected for wood extent)', html2)
        html2 = _re.sub(r'const PTR_TIP_Y\s*=\s*[\d.]+;',
                        f'const PTR_TIP_Y   = {ptr_tip_y:.4f};', html2)
        _def2 = (f"const DEFAULTS = {{ leftFrac:{left:.4f}, rightFrac:{right:.4f}, "
                 f"trackYFrac:{track:.4f}, nudge6am:{n6am:.2f}, nudge3pm:{n3pm:.2f}, "
                 f"nudgeMid:{nmid:.2f}, sweepSpeed:1, ptrRatio:{ptr_ratio:.4f} }};")
        html2 = _re.sub(r'const DEFAULTS = \{[^}]+\};', _def2, html2, flags=_re.DOTALL)
        html2 = html2.replace('<title>Linear Clockworks</title>',
                              f'<title>Linear Clockworks — {safe}</title>')
        html2 = strip_calibration(html2)
        final_out = f'{sku}.html'
        with open(final_out, 'w') as f:
            f.write(html2)
        print(f'\n✓ {final_out}  ({os.path.getsize(final_out)//1024} KB)  — ready to ship')

if __name__ == '__main__':
    main()

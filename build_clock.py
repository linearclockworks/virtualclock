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

def shopify_get(path):
    url = f'https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VER}/{path}'
    r = _requests.get(url, headers={
        'X-Shopify-Access-Token': SHOPIFY_TOKEN,
        'Content-Type': 'application/json',
    }, timeout=30)
    r.raise_for_status()
    return r.json()

def find_product_by_sku(sku):
    """Search variants by SKU, return the parent product."""
    page = shopify_get(f'products.json?limit=250&fields=id,title,variants,images')
    for product in page['products']:
        for variant in product.get('variants', []):
            if (variant.get('sku') or '').strip().upper() == sku.upper():
                return product
    return None

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
    Compute PTR_H_RATIO so pointer top sits 1/10 wood-height above wood top,
    and tip lands at track_y_frac of face image height.
    ptr_rgba, face_rgba: RGBA PIL Images at web resolution.
    """
    import numpy as np

    # Measure wood extent in face image
    fa = np.array(face_rgba)
    fh, fw = fa.shape[:2]
    rows = np.any(fa[:,:,3] > 10, axis=1)
    rmin = int(np.where(rows)[0][0])
    wood_h_frac = (int(np.where(rows)[0][-1]) - rmin + 1) / fh
    wood_top_frac = rmin / fh

    # Measure pointer content extent
    pa = np.array(ptr_rgba)
    ph, pw = pa.shape[:2]
    alpha = pa[:,:,3]
    p_rows = np.any(alpha > 10, axis=1)
    ct_frac = int(np.where(p_rows)[0][0]) / ph   # content top

    # Find tip: bottommost non-transparent pixel near horizontal center
    cols = np.any(alpha > 10, axis=0)
    cmin, cmax = int(np.where(cols)[0][0]), int(np.where(cols)[0][-1])
    cx = (cmin + cmax) // 2
    for row in range(ph-1, -1, -1):
        if alpha[row, cx] > 10:
            tf_frac = row / ph
            break

    # Solve: ptr_H * (tf - ct) = (track_y_frac - (wood_top_frac - 0.1*wood_h_frac))
    tip_target = track_y_frac
    top_target = wood_top_frac - 0.10 * wood_h_frac
    R = (tip_target - top_target) / (tf_frac - ct_frac)
    print(f"  Pointer ratio: {R:.4f}  (tip@{tf_frac:.3f} content_top@{ct_frac:.3f})")
    print(f"  Wood: top={wood_top_frac:.3f} height={wood_h_frac:.3f}")
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

    # Calibration values
    left     = args.left     if args.left     is not None else DEFAULTS['leftFrac']
    right    = args.right    if args.right    is not None else DEFAULTS['rightFrac']
    track    = args.track    if args.track    is not None else DEFAULTS['trackYFrac']
    n3pm     = args.nudge3pm if args.nudge3pm is not None else DEFAULTS['nudge3pm']
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

    # Compute pointer size ratio from actual images
    try:
        ptr_ratio, ptr_tip_y, ptr_ct_y = compute_ptr_ratio(ptr_web, face_web, track)
        if not (0.2 < ptr_ratio < 1.5):
            raise ValueError(f'ptr_ratio {ptr_ratio:.4f} out of range')
        print(f'  PTR_H_RATIO={ptr_ratio:.4f}  tip_y={ptr_tip_y:.4f}')
    except Exception as e:
        print(f'  Warning: ptr_ratio failed ({e}), using 0.5288')
        ptr_ratio, ptr_tip_y, ptr_ct_y = 0.5288, 0.9217, 0.0483
    print(f'  Tip X frac: 0.5207 (default)')

    # ── Build HTML ──
    with open(args.template, 'r') as f:
        html = f.read()

    html = html.replace('FACE_DATA_URI', f'data:image/png;base64,{face_b64}')
    html = html.replace('PTR_DATA_URI',  f'data:image/png;base64,{ptr_b64}')

    # Patch PTR_H_RATIO and PTR_TIP_Y with computed values
    html = _re.sub(r'const PTR_H_RATIO\s*=\s*[\d.]+;',
                   f'const PTR_H_RATIO = {ptr_ratio:.4f};', html)
    html = _re.sub(r'const PTR_TIP_Y\s*=\s*[\d.]+;',
                   f'const PTR_TIP_Y   = {ptr_tip_y:.4f};', html)

    # Replace DEFAULTS const
    _def = (f"const DEFAULTS = {{ leftFrac:{left}, rightFrac:{right}, "
            f"trackYFrac:{track}, nudge6am:0, nudge3pm:{n3pm}, "
            f"nudgeMid:0, sweepSpeed:{speed} }};")
    html = _re.sub(r'const DEFAULTS = \{[^}]+\};', _def, html)

    # Title
    safe = re.sub(r'[^a-zA-Z0-9 \-]', '', title)
    html = html.replace('<title>Linear Clockworks</title>',
                        f'<title>Linear Clockworks — {safe}</title>')

    if not args.calibrate:
        html = strip_calibration(html)
        with open(out, 'w') as f:
            f.write(html)
        print(f'\n✓ {out}  ({os.path.getsize(out)//1024} KB)')
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
            'function sendDone(){'
            '  const c=JSON.parse(localStorage.getItem("lc_cal")||"{}");'
            '  document.getElementById("done-cal-btn").textContent="Generating...";'
            '  fetch("http://localhost:19888/done",{'
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

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path == '/done':
                    data = json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
                    cal_result.update(data)
                    self.send_response(200)
                    self.send_header('Access-Control-Allow-Origin','*')
                    self.end_headers()
                    self.wfile.write(b'Generating final HTML...')
            def do_OPTIONS(self):
                self.send_response(200)
                for h,v in [('Access-Control-Allow-Origin','*'),
                             ('Access-Control-Allow-Methods','POST,OPTIONS'),
                             ('Access-Control-Allow-Headers','Content-Type')]:
                    self.send_header(h,v)
                self.end_headers()
            def log_message(self,*a): pass

        server = HTTPServer(('localhost', 19888), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        webbrowser.open('file://' + os.path.abspath(cal_out))
        print('Waiting for Done click...')

        while not cal_result:
            time.sleep(0.3)
        server.shutdown()

        # Use returned cal values
        left  = cal_result.get('leftFrac',   left)
        right = cal_result.get('rightFrac',  right)
        track = cal_result.get('trackYFrac', track)
        n3pm  = cal_result.get('nudge3pm',   n3pm)
        print(f'  6AM={left:.4f}  Midnight={right:.4f}  TrackY={track:.4f}  Nudge3PM={n3pm:.2f}')

        # Build final customer HTML
        with open(args.template, 'r') as f:
            html2 = f.read()
        html2 = html2.replace('FACE_DATA_URI', f'data:image/png;base64,{face_b64}')
        html2 = html2.replace('PTR_DATA_URI',  f'data:image/png;base64,{ptr_b64}')
        html2 = _re.sub(r'const PTR_H_RATIO\s*=\s*[\d.]+;',
                        f'const PTR_H_RATIO = {ptr_ratio:.4f};', html2)
        html2 = _re.sub(r'const PTR_TIP_Y\s*=\s*[\d.]+;',
                        f'const PTR_TIP_Y   = {ptr_tip_y:.4f};', html2)
        _def2 = (f"const DEFAULTS = {{ leftFrac:{left:.4f}, rightFrac:{right:.4f}, "
                 f"trackYFrac:{track:.4f}, nudge6am:0, nudge3pm:{n3pm:.2f}, "
                 f"nudgeMid:0, sweepSpeed:1 }};")
        html2 = _re.sub(r'const DEFAULTS = \{{[^}}]+\}};', _def2, html2)
        html2 = html2.replace('<title>Linear Clockworks</title>',
                              f'<title>Linear Clockworks — {safe}</title>')
        html2 = strip_calibration(html2)
        final_out = f'{sku}.html'
        with open(final_out, 'w') as f:
            f.write(html2)
        print(f'\n✓ {final_out}  ({os.path.getsize(final_out)//1024} KB)  — ready to ship')

if __name__ == '__main__':
    main()

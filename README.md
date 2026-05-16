# virtualclock

Generates self-contained calibrated virtual clock HTML files from Shopify product images.

## Files

| File | Purpose |
|------|---------|
| `clock_template.html` | Base template — baked into every output HTML |
| `build_clock.py` | Main builder: fetches Shopify product, processes images, generates HTML |
| `separate_pointer.py` | Image prep: splits product photo into face PNG + pointer PNG |
| `Makefile` | Convenience workflows |

## Setup

```bash
pip install -r requirements.txt   # opencv-python only needed for separate_pointer.py
export SHOPIFY_ACCESS_TOKEN=...
export SHOPIFY_SHOP_NAME=linear-clockworks.myshopify.com
```

## Workflow

### 1. Prep images (if starting from product photos)
```bash
python3 separate_pointer.py LCK-1051-front.png --pointer LCK-1051-pointer.png
# → LCK-1051-face.png  LCK-1051-ptr.png
```
Upload both PNGs to the Shopify product as images 6 and 7.

### 2. Calibrate (interactive)
```bash
python3 build_clock.py LCK-1051 --calibrate
# Opens browser → use ⚙ sliders → click Done → Generates LCK-1051.html
```
**Note:** Uses `file://` URL + localhost:19888 server. Works in Chrome; Safari may block the fetch (use Chrome).

### 3. Build customer HTML (with known cal values)
```bash
python3 build_clock.py LCK-1051 --left 0.145 --right 0.852 --track 0.554
# → LCK-1051.html
```

### 4. Ship
Host the output HTML on GitHub Pages or Shopify:
```
https://linearclockworks.github.io/CTlight/LCK-1051.html
```

## Calibration sliders

| Slider | What it does |
|--------|-------------|
| **6 AM** | Nudge left anchor (6AM position) left/right |
| **3 PM** | Nudge midpoint (applies quadratic bow correction) |
| **Midnight** | Nudge right anchor |
| **Pointer height** | Vertical track position as % of face height |
| **Pointer size** | PTR_H_RATIO — pointer height as fraction of face height |
| **▶ Sweep** | Animate pointer across full 18-hour day + night return |

## Image slots (Shopify product, 1-indexed)

- Image 6: clock face (wood board, no pointer)
- Image 7: pointer only (transparent or black background)

Override with `--face-index N --ptr-index N`.

## Known issues / tips

- **Pointer too big on first calibrate open:** Fixed in current template — image load timing was the cause.
- **Sliders not showing:** Setup overlay auto-opens on calibrate builds. Requires `CALIBRATE_MODE=true` injected by build_clock.py.
- **Safari blocks localhost fetch:** Use Chrome for `--calibrate` mode.
- **Black background removal:** `build_clock.py` flood-fills connected black pixels from image edges. Works best when the pointer/face has no black interior regions touching the border.

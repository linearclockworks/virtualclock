# Master Feature & Logic Manifest (v13.0)

## 1. Coordinate & Calibration Math

- **Unified Math Engine:** All horizontal movements (Pointer and Lume Dots) pass through the `getCorrectedX` quadratic regression function using the identical calibration object, ensuring zero drift between pointer and lume positions.
- **Percentage Coordinate System:** All calibration values are stored as true percentages (0–100) of the image width/height. A `nudge6am` of `5.5` means 5.5% from the left edge. There are no hidden offsets or unit conversions at render time. Old v12 decimal (0–1) values are auto-migrated to percentages by `build_clock.py` at build time.
- **Three-Anchor Curve:** The horizontal track is a quadratic curve fitted to three anchor points: `nudge6am` (left), `nudge3pm` (center), `nudgeMid` (right). This corrects for lens distortion and non-linear wood grain photography.
- **Independent Pointer Nudge:** `ptrNudgeX` shifts the pointer assembly independently of the lume dots, allowing the tip to be zeroed against a specific hour marker without affecting lume alignment.
- **Tip-to-Track Alignment:** The pointer image uses `bottom: 0` inside a zero-height absolute wrapper, with `left: 50%; transform: translateX(-50%)` centering the image on the track coordinate. The physical tip is always on the mathematical track line.
- **Vertical Lume Lock:** When the Pointer Height (`trackYFrac`) slider changes, `nudgePtrLumeY` shifts by the same delta automatically, keeping the pointer lume pinned to the pointer image without manual re-adjustment.
- **Lume Dot Centering:** The pointer lume dot uses `left: -5px` (half its 10px width) set in JS, with `transform: translateY(-50%)` only — no `translateX` — so its center aligns exactly with the zero-width wrapper origin (= pointer image center).

## 2. Animation & State Logic

- **Anti-Jerk Protocol (The Glide Rule):** Direct position jumps are forbidden. All transitions — stopping a demo, finishing a sweep, returning to system time — use `animateToFrac` for smooth easing.
- **Stateless Reset (Anti-Freeze):** Every Start/Stop command performs a hard reset of the animation queue (`cancelAnimationFrame` + `clearTimeout`) to prevent the second-click freeze bug.
- **Preview Anchor Lock:** During calibration, the last anchor touched (6 AM, 3 PM, Midnight) is remembered via `previewLockFrac`. Adjusting secondary sliders maintains that anchor position rather than jumping to system time.
- **Mouse-Locked Dimming:** The Night Dim slider forces a `night-mode` preview that persists while the mouse/touch is active, allowing immediate visual feedback of the dimming range.
- **Forward-Only Clock Display:** The analog and digital clocks always show time moving forward. During the night return sweep (linear pointer moving right→left), `isReturning=true` maps the backward frac to post-midnight time (`1.0 + (1.0 - frac)`), so clocks continue 12:00 AM → 1:00 AM → 6:00 AM rather than running backward.


## 3. Visual & Teaching Features

- **Night Dimming:** A full-screen black overlay dims the wood face. Analog/digital clocks are additionally filtered at `brightness((1 - nightDim) * 0.5)` — approximately 2× brighter than the wood face — so the clock faces remain readable at night without excessive glow.
- **Lume Activation:** Lume dots and pointer lume are invisible in daylight (`opacity: 0`) and glow green (`#9dff9d`) in night mode or during calibration when the lume sliders are active.
- **Teaching Face:** The analog clock displays all 12 digits in bold Arial for maximum readability. The minute hand includes a seconds contribution (`fs * 0.1`) for smooth continuous sweep.
- **SKU-Specific Linking:** The Details button links to `https://linearclockworks.com/search?q={productSku}`. `productSku` is baked into DEFAULTS by `build_clock.py` from the SKU argument.
- **Calibration Panel:** Labels are `0.85rem` in `--text` color (warm cream). Sliders are ordered: Level, Ptr Height, Ptr Lume Height, Ptr Size, Lume Height, 6 AM, 3 PM, Midnight, Ptr Align. Anchor sliders (6 AM / 3 PM) have a ±1.5 window at step 0.006 (500 positions). Midnight has a ±5 window for extended range.

## 4. Build System & Single Source of Truth

- **Single DEFAULTS Source:** `build_clock.py`'s Python `DEFAULTS` dict is the only place calibration defaults are defined. `clock_template.html` contains only a `BUILD_DEFAULTS_PLACEHOLDER` comment block that is regex-replaced at build time. There is no duplicate JS DEFAULTS to keep in sync.
- **Build Pipeline:**
  1. `build_clock.py {SKU}` — reads `{SKU}-cal.json` if present, merges over DEFAULTS, migrates any v12 decimal values, injects into template, generates PWA files.
  2. `build_clock.py {SKU} --calibrate` — serves calibration HTML on `localhost:19888`, opens browser, waits for DONE, writes `{SKU}-cal.json`, then re-runs the plain build automatically.
- **JSON Override:** `{SKU}-cal.json` only affects the baked-in DEFAULTS at build time. To reset to Python DEFAULTS, delete the JSON and rebuild. To set a starting estimate without calibrating, create a minimal JSON with only the keys you want to override (e.g. `{"nudgeMid": 91.0}`).
- **localStorage Behavior:** At runtime, `loadCal()` reads localStorage. If `saved.version === DEFAULTS.version`, localStorage wins over baked-in DEFAULTS. On version mismatch, localStorage is wiped and DEFAULTS takes over. The page also unregisters stale service workers on version mismatch to force fresh HTML delivery.
- **v12 → v13 Migration:** `trackYFrac`, `ptrRatio`, `nudgeDotsY`, `nudgePtrLumeY` changed from 0–1 decimal to 0–100 percentage. `build_clock.py` detects values `< 1.0` for these keys in the JSON and multiplies by 100 with a console warning.
- **Version:** Currently `13.0`. Bump to force localStorage wipe across all deployed PWAs.
- **Lazy Imports:** `cv2` and `numpy` are imported only inside `straightening_process()` (runs only when face PNG cache is missing). `requests` is imported only inside `shopify_graphql()` and the image download block. Only `PIL` and stdlib load at startup, eliminating the 10–20 second macOS code-signing delay on cold starts.
- **Image Cache:** `{SKU}_face.png` and `{SKU}_ptr.png` are cached locally. On rebuild, if both exist, cv2/numpy never import at all.

## 5. Deployment & Git

- **PWA Assets:** Each build produces three files: `{FriendlyName}.html`, `{FriendlyName}-sw.js`, `{FriendlyName}.webmanifest`.
- **Service Worker:** Caches the HTML for offline use. Cache key includes a timestamp `cv` so each rebuild invalidates the previous SW cache automatically.
- **Git Commands** (printed by `build_clock.py` after every successful PWA build):
  ```
  git add {fname}.html {fname}-sw.js {fname}.webmanifest
  git commit -m "{fname} teaching clock build"
  git push
  ```
- **Hosted URL:** `https://linearclockworks.github.io/virtualclock/{FriendlyName}.html`


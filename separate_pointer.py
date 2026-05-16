#!/usr/bin/env python3
"""
separate_pointer.py  —  split clock product photo into face + pointer PNGs.

REQUIRED inputs (name them consistently):
    name-front.png    straight-on product photo
    name-pointer.png  pointer-only photo (black bg preferred, clock face bg ok)

Output:
    name-face.png     face with pointer region inpainted, rotation-corrected
    name-ptr.png      pointer, background removed, cropped

Usage:
    python3 separate_pointer.py sergio-front.png --pointer sergio-pointer.png
    python3 separate_pointer.py alvaro-front.png --pointer alvaro-pointer.png

Optional:
    --angle DEGREES   override auto-detected rotation (from engraved lines)
    --inpaint-radius N  (default 16)
"""

import argparse, os, re, sys
import numpy as np
import cv2
from PIL import Image
from collections import deque


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def get_stem(path):
    base = os.path.basename(path)
    stem = re.sub(r'[-_]?(front|back|face|ptr|pointer)\.(png|jpg)$', '', base, flags=re.IGNORECASE)
    stem = re.sub(r'\.(png|jpg)$', '', stem, flags=re.IGNORECASE)
    return stem


def remove_black_bg(arr, threshold=20):
    """Flood-fill connected black background pixels to alpha=0."""
    h, w = arr.shape[:2]
    result = arr.copy()
    is_dark = (arr[:,:,0]<threshold) & (arr[:,:,1]<threshold) & (arr[:,:,2]<threshold)
    visited = np.zeros((h,w), dtype=bool)
    q = deque()
    for c in range(w):
        for r in [0, h-1]:
            if is_dark[r,c] and not visited[r,c]:
                visited[r,c] = True; q.append((r,c))
    for r in range(h):
        for c in [0, w-1]:
            if is_dark[r,c] and not visited[r,c]:
                visited[r,c] = True; q.append((r,c))
    while q:
        ri, ci = q.popleft()
        result[ri, ci, 3] = 0
        for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]:
            nr, nc = ri+dr, ci+dc
            if 0<=nr<h and 0<=nc<w and not visited[nr,nc] and is_dark[nr,nc]:
                visited[nr,nc] = True; q.append((nr,nc))
    return result


def feather_alpha(arr, erode_px=1, blur_px=5):
    """Smooth hard alpha edges."""
    alpha = arr[:,:,3].copy()
    k = np.ones((3,3), np.uint8)
    interior = cv2.erode(alpha, k, iterations=max(1,erode_px))
    blurred  = cv2.GaussianBlur(cv2.erode(alpha, k, iterations=1), (blur_px,blur_px), 0)
    arr[:,:,3] = np.where(interior>0, 255, blurred).astype(np.uint8)
    return arr


def crop_to_content(arr, pad=20):
    """Crop array to non-transparent bounding box + pad."""
    alpha = arr[:,:,3]
    rows = np.where(np.any(alpha>0, axis=1))[0]
    cols = np.where(np.any(alpha>0, axis=0))[0]
    if not len(rows): return arr
    h, w = arr.shape[:2]
    y1,y2 = max(0,rows[0]-pad), min(h,rows[-1]+pad)
    x1,x2 = max(0,cols[0]-pad), min(w,cols[-1]+pad)
    return arr[y1:y2, x1:x2]


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Extract pointer from standalone photo
# ─────────────────────────────────────────────────────────────────────────────

def extract_pointer_standalone(pointer_path):
    """
    Extract pointer from standalone photo (black bg or clock-face bg).
    Returns clean feathered RGBA PIL Image.
    """
    img = Image.open(pointer_path).convert('RGBA')
    arr = np.array(img)
    h, w = arr.shape[:2]
    brt = (arr[:,:,0].astype(int)+arr[:,:,1].astype(int)+arr[:,:,2].astype(int))/3.0

    # Check corner brightness to classify background type
    corners = np.concatenate([brt[:40,:40].ravel(), brt[:40,-40:].ravel(),
                               brt[-40:,:40].ravel(), brt[-40:,-40:].ravel()])
    corner_brt = float(np.percentile(corners, 80))
    print(f'  Pointer photo bg brightness: {corner_brt:.1f}')

    # Always flood-fill black bg first
    result = remove_black_bg(arr.copy(), threshold=20)

    if corner_brt > 15:
        # Mixed background (pointer on clock face photo)
        print('  Mixed background detected — extracting dark pointer body')
        result = _extract_dark_pointer_from_wood(arr, result)

    result = feather_alpha(result)
    result = crop_to_content(result, pad=20)
    return Image.fromarray(result)


def _extract_dark_pointer_from_wood(arr_orig, arr_black_removed):
    """
    For pointer-on-wood-face photos: after removing black bg,
    flood-fill the dark pointer body from just below the wood top edge.
    """
    h, w = arr_orig.shape[:2]
    brt = (arr_orig[:,:,0].astype(int)+arr_orig[:,:,1].astype(int)+arr_orig[:,:,2].astype(int))/3.0

    # Find wood top edge from the black-removed image
    alpha_vis = arr_black_removed[:,:,3] > 0
    wood_top_cols = np.full(w, h, dtype=int)
    for col in range(w):
        vis = np.where(alpha_vis[:,col])[0]
        if len(vis): wood_top_cols[col] = vis[0]
    valid = wood_top_cols[wood_top_cols < h]
    if not len(valid):
        return arr_black_removed
    wood_top = int(np.median(valid))

    # Pointer center x from above-wood pixels
    above = alpha_vis.copy(); above[wood_top:,:] = False
    above_cols = np.where(np.any(above, axis=0))[0]
    if not len(above_cols):
        return arr_black_removed
    ptr_cx  = int(above_cols.mean())
    x_lo    = max(0, ptr_cx - 130)
    x_hi    = min(w, ptr_cx + 130)

    # Sample wood background brightness well away from pointer
    sample = brt[wood_top+20:wood_top+150, min(w-1,x_hi+80):]
    bg_brt  = float(np.median(sample[sample>30])) if sample.size else 170
    dark_thr = bg_brt * 0.72
    print(f'  Wood bg: {bg_brt:.1f}, dark threshold: {dark_thr:.1f}')

    # Start with above-wood pixels, add dark-connected body below
    ptr_mask = above.copy()
    visited  = np.zeros((h,w), dtype=bool)
    seeds    = []
    for y in range(wood_top, min(h, wood_top+30)):
        for x in range(x_lo, x_hi):
            if brt[y,x] < dark_thr and alpha_vis[y,x]:
                seeds.append((y,x)); visited[y,x] = True

    q = deque(seeds)
    while q:
        ri,ci = q.popleft()
        ptr_mask[ri,ci] = True
        for dr,dc in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)]:
            nr,nc = ri+dr,ci+dc
            if 0<=nr<h and 0<=nc<w and not visited[nr,nc] and x_lo<=nc<=x_hi:
                visited[nr,nc] = True
                if brt[nr,nc] < dark_thr and alpha_vis[nr,nc]:
                    q.append((nr,nc))

    # Morphological close to fill gaps in stem
    kernel   = np.ones((7,7), np.uint8)
    ptr_mask = cv2.morphologyEx(ptr_mask.astype(np.uint8),
                                cv2.MORPH_CLOSE, kernel, iterations=3).astype(bool)

    result = arr_orig.copy()
    result[~ptr_mask, 3] = 0
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Detect rotation from front photo
# ─────────────────────────────────────────────────────────────────────────────

def detect_angle(arr):
    """
    Detect clock tilt from engraved lines under MORNING/AFTERNOON/EVENING.
    Returns degrees (positive = clockwise tilt to correct).
    """
    h, w = arr.shape[:2]
    gray = cv2.cvtColor(arr[:,:,:3], cv2.COLOR_RGB2GRAY)
    y1, y2 = int(h*0.12), int(h*0.50)
    edges = cv2.Canny(gray[y1:y2, :], 30, 120)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180,
                             threshold=80, minLineLength=w//8, maxLineGap=30)
    if lines is None: return 0.0
    angles = [np.degrees(np.arctan2(y2_-y1_, x2-x1))
              for x1,y1_,x2,y2_ in lines[:,0]
              if abs(np.degrees(np.arctan2(y2_-y1_, x2-x1))) < 8]
    return float(np.median(angles)) if angles else 0.0


def rotate_image(arr, angle_deg):
    if abs(angle_deg) < 0.1: return arr
    h, w = arr.shape[:2]
    M = cv2.getRotationMatrix2D((w//2, h//2), -angle_deg, 1.0)
    return cv2.warpAffine(arr, M, (w,h),
                          flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT,
                          borderValue=(0,0,0,0))


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Find pointer location in front photo (for inpainting)
# ─────────────────────────────────────────────────────────────────────────────

def find_pointer_from_edge(arr):
    """
    The pointer always protrudes 1" above the wood top edge.
    Find the localized dip in the top-edge profile.
    Returns (ptr_cx, ptr_top, ptr_x1, ptr_x2, wood_top_y).
    """
    h, w = arr.shape[:2]
    is_bg = ((arr[:,:,0]<15)&(arr[:,:,1]<15)&(arr[:,:,2]<15)) | (arr[:,:,3]<10)

    wood_top = np.full(w, h, dtype=int)
    for col in range(w):
        non_bg = np.where(~is_bg[:,col])[0]
        if len(non_bg): wood_top[col] = non_bg[0]

    valid = wood_top[wood_top < h]
    if not len(valid): sys.exit('Cannot find wood edge in front photo')
    median_top = int(np.median(valid))

    # Pointer = localized dip > 20px above median
    dip_thresh = median_top - 20
    candidates = np.where(wood_top < dip_thresh)[0]
    if not len(candidates):
        dip_thresh = median_top - 10
        candidates = np.where(wood_top < dip_thresh)[0]
    if not len(candidates):
        sys.exit('No pointer protrusion found above wood edge. Is pointer visible?')

    # Find tightest contiguous group (pointer is one object)
    gaps   = np.where(np.diff(candidates) > 15)[0]
    groups = []
    prev   = 0
    for g in gaps:
        groups.append(candidates[prev:g+1]); prev = g+1
    groups.append(candidates[prev:])
    best = min(groups, key=lambda g: wood_top[g].min())

    ptr_cx  = int(best.mean())
    ptr_top = int(wood_top[best].min())
    ptr_x1  = int(best[0])
    ptr_x2  = int(best[-1])

    print(f'  Wood top (median): row {median_top}')
    print(f'  Pointer: x={ptr_x1}-{ptr_x2} (center {ptr_cx}), top row {ptr_top}')
    print(f'  Protrudes {median_top-ptr_top}px above wood')

    return ptr_cx, ptr_top, ptr_x1, ptr_x2, median_top


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Build inpaint mask for face
# ─────────────────────────────────────────────────────────────────────────────

def build_inpaint_mask(arr, ptr_cx, ptr_top, ptr_x1, ptr_x2, wood_top_y):
    """
    Build mask of pixels to inpaint in the face image.
    Above wood edge: all non-background pixels in pointer column band.
    Below wood edge: GrabCut on tight crop to get pointer body.
    """
    h, w = arr.shape[:2]
    is_bg = ((arr[:,:,0]<15)&(arr[:,:,1]<15)&(arr[:,:,2]<15)) | (arr[:,:,3]<10)
    mask  = np.zeros((h,w), dtype=bool)

    # Above wood: tight column band, exclude track line row
    half_w = max(15, (ptr_x2-ptr_x1)//2 + 10)
    col_lo = max(0, ptr_cx - half_w)
    col_hi = min(w, ptr_cx + half_w)
    mask[ptr_top:wood_top_y, col_lo:col_hi] = ~is_bg[ptr_top:wood_top_y, col_lo:col_hi]

    # Below wood: GrabCut on tight crop
    above_h  = max(10, wood_top_y - ptr_top)
    below_h  = int(above_h * 4)
    y_lo, y_hi = wood_top_y, min(h, wood_top_y + below_h)
    crop_bgr = cv2.cvtColor(arr[y_lo:y_hi, col_lo:col_hi, :3], cv2.COLOR_RGB2BGR)
    ch, cw   = crop_bgr.shape[:2]

    if ch > 10 and cw > 10:
        gc = np.full((ch,cw), cv2.GC_PR_BGD, dtype=np.uint8)
        cx_loc  = ptr_cx - col_lo
        hw      = max(10, (ptr_x2-ptr_x1)//2)
        gc[:, max(0,cx_loc-hw):min(cw,cx_loc+hw)] = cv2.GC_PR_FGD
        gc[:min(15,ch), max(0,cx_loc-hw):min(cw,cx_loc+hw)] = cv2.GC_FGD
        border  = 8
        gc[:border,:] = gc[-border:,:] = gc[:,:border] = gc[:,-border:] = cv2.GC_BGD
        bgd = np.zeros((1,65), np.float64)
        fgd = np.zeros((1,65), np.float64)
        try:
            cv2.grabCut(crop_bgr, gc, None, bgd, fgd, 6, cv2.GC_INIT_WITH_MASK)
            fg = (gc==cv2.GC_FGD)|(gc==cv2.GC_PR_FGD)
            k  = np.ones((5,5), np.uint8)
            fg = cv2.morphologyEx(fg.astype(np.uint8), cv2.MORPH_CLOSE, k).astype(bool)
            mask[y_lo:y_hi, col_lo:col_hi] |= fg
        except Exception as e:
            print(f'  GrabCut note: {e}')

    return mask


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Inpaint face
# ─────────────────────────────────────────────────────────────────────────────

def inpaint_face(arr, mask, radius=16):
    """
    Directional inpaint: prefer sampling from right/below to avoid
    bleeding from black background borders.
    """
    result    = arr.copy().astype(float)
    result[mask, :3] = 0
    result[mask, 3]  = 0
    remaining = mask.copy()
    h, w      = mask.shape

    offsets = []
    for dy in range(-radius, radius+1):
        for dx in range(-radius, radius+1):
            if (dy,dx) != (0,0) and abs(dy)+abs(dx) <= radius:
                base = 1.0 / (abs(dy)+abs(dx)+0.5)
                if dx > 0: base *= 3.0
                if dy > 0: base *= 2.0
                if dx < 0: base *= 0.4
                if dy < 0: base *= 0.6
                offsets.append((dy, dx, base))
    offsets.sort(key=lambda x: -x[2])

    for _ in range(80):
        if not remaining.any(): break
        ys, xs = np.where(remaining)
        newly  = []
        for y, x in zip(ys, xs):
            tot, px = 0.0, np.zeros(3)
            for dy, dx, wt in offsets:
                ny, nx = y+dy, x+dx
                if 0<=ny<h and 0<=nx<w and not remaining[ny,nx] and result[ny,nx,3]>50:
                    px  += result[ny,nx,:3] * wt
                    tot += wt
            if tot > 0:
                result[y,x,:3] = px / tot
                result[y,x,3]  = 255
                newly.append((y,x))
        for y,x in newly: remaining[y,x] = False
        if not newly: break

    return result.astype(np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input',     help='Front photo, e.g. sergio-front.png')
    ap.add_argument('--pointer', default=None,
                    help='Standalone pointer photo (e.g. sergio-pointer.png). '
                         'Required for best results.')
    ap.add_argument('--angle',   type=float, default=None,
                    help='Override auto-detected rotation in degrees')
    ap.add_argument('--inpaint-radius', type=int, default=16)
    args = ap.parse_args()

    stem     = get_stem(args.input)
    out_face = f'{stem}-face.png'
    out_ptr  = f'{stem}-ptr.png'

    print(f'Input:  {args.input}')
    print(f'Output: {out_face}  +  {out_ptr}')

    # ── Load and straighten front photo ──
    img = Image.open(args.input).convert('RGBA')
    arr = np.array(img)
    h, w = arr.shape[:2]
    print(f'Size:   {w}x{h}')

    angle = args.angle if args.angle is not None else detect_angle(arr)
    print(f'Rotation: {angle:.3f}° ({"manual" if args.angle else "auto"})')
    arr = rotate_image(arr, angle)

    # ── Find pointer location in front photo ──
    print('\nLocating pointer...')
    ptr_cx, ptr_top, ptr_x1, ptr_x2, wood_top_y = find_pointer_from_edge(arr)

    # ── Extract pointer ──
    if args.pointer:
        print(f'\nExtracting pointer from {args.pointer}...')
        ptr_img = extract_pointer_standalone(args.pointer)
    else:
        print('\nNo --pointer photo given; extracting from front photo (lower quality)')
        mask_ptr = build_inpaint_mask(arr, ptr_cx, ptr_top, ptr_x1, ptr_x2, wood_top_y)
        ptr_arr  = np.zeros_like(arr)
        ptr_arr[mask_ptr] = arr[mask_ptr]
        ptr_arr  = feather_alpha(ptr_arr)
        ptr_arr  = crop_to_content(ptr_arr)
        ptr_img  = Image.fromarray(ptr_arr)

    ptr_img.save(out_ptr)
    print(f'✓ {out_ptr}  ({ptr_img.width}x{ptr_img.height})')

    # ── Inpaint face ──
    print('\nBuilding inpaint mask...')
    mask = build_inpaint_mask(arr, ptr_cx, ptr_top, ptr_x1, ptr_x2, wood_top_y)
    print(f'  {mask.sum()} pixels to inpaint')

    print('Inpainting face...')
    face_inpainted = inpaint_face(arr, mask, radius=args.inpaint_radius)
    face_final     = remove_black_bg(face_inpainted)
    Image.fromarray(face_final).save(out_face)
    print(f'✓ {out_face}')


if __name__ == '__main__':
    main()

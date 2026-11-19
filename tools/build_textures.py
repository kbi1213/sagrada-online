"""Bake the Higgsfield source images into the textures the 3D page loads.

usage: python tools/build_textures.py <source-dir>
Source dir holds the full-size PNGs: stone, mosaic, glass_cool, glass_warm, vault,
and the reference photos (nave, aerial, nativity, passion, glory, apse, full, towers, columns, altar).
Writes public/textures/*.jpg and public/photos/*.webp.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

SRC = Path(sys.argv[1])
ROOT = Path(__file__).resolve().parent.parent
TEX = ROOT / "public" / "textures"
PHO = ROOT / "public" / "photos"
TEX.mkdir(parents=True, exist_ok=True)
PHO.mkdir(parents=True, exist_ok=True)


def load(name, size=None):
    im = Image.open(SRC / f"{name}.png").convert("RGB")
    return im.resize(size, Image.LANCZOS) if size else im


def arr(im):
    return np.asarray(im, dtype=np.float32) / 255.0


def save_rgb(a, name, q=86):
    Image.fromarray(np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)).save(TEX / name, quality=q, optimize=True)


def normal_from_height(h, strength):
    """Tileable normal map (OpenGL convention, +Y up) from a height field in 0..1."""
    dx = np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)
    dy = np.roll(h, 1, axis=0) - np.roll(h, -1, axis=0)  # image rows go down, texture v goes up
    n = np.dstack([-dx * strength, -dy * strength, np.ones_like(h)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return n * 0.5 + 0.5


def lum(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def blur(a, r):
    im = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(r)), dtype=np.float32) / 255.0


# ---- stone: seamless sandstone ashlar ----
stone = arr(load("stone", (2048, 2048)))
save_rgb(stone, "stone.jpg")
sh = blur(lum(stone), 1.2)
save_rgb(normal_from_height(sh, 6.0), "stone_n.jpg", 90)

# ---- trencadís mosaic for pinnacles ----
mosaic = arr(load("mosaic", (1024, 1024)))
save_rgb(mosaic, "mosaic.jpg")
save_rgb(normal_from_height(blur(lum(mosaic), 1.0), 5.0), "mosaic_n.jpg", 90)

# ---- vault ceiling (radial photo) + emissive skylights ----
vault = arr(load("vault", (2048, 2048)))
save_rgb(vault, "vault.jpg")
vl = lum(vault)
save_rgb(vault * np.clip((vl - 0.72) / 0.2, 0, 1)[..., None], "vault_e.jpg")

# ---- tower skin: stone tiled 1x4 with spiral slits (same layout as the page's procedural version) ----
W, H = 512, 2048
tile = arr(load("stone", (512, 512)))
base = np.tile(tile, (4, 1, 1))
ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
cols, rows = 4, 16
cw, rh = W / cols, H / rows
sw, shh = cw * 0.3, rh * 0.62
rad = sw / 2
r = np.floor(ys / rh)
ox = (r % 4) * 0.25 * cw
lx = np.mod(xs - ox, cw) - cw * 0.35
ly = ys - r * rh - rh * 0.19
qx = np.abs(lx - sw / 2) - (sw / 2 - rad)
qy = np.abs(ly - shh / 2) - (shh / 2 - rad)
sd = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - rad
slit = sd < 0
lip = (sd >= 0) & (sd < 8)
col = base.copy()
col[slit] = col[slit] * 0.12
col[lip] *= 1.05
save_rgb(col, "tower.jpg")
em = np.zeros_like(col)
em[slit] = [1.0, 0.78, 0.5]
save_rgb(em, "tower_e.jpg")
th = blur(lum(base), 1.2) * 0.35 + 0.45
th[lip] += ((8 - sd[lip]) / 8) * 0.35
th[slit] = 0
save_rgb(normal_from_height(th, 5.0), "tower_n.jpg", 90)

# ---- wall bays: stone + photographed stained glass inside a parabolic arch ----
BW, BH = 512, 1024
bstone = np.tile(arr(load("stone", (512, 512))), (2, 1, 1))
ys, xs = np.mgrid[0:BH, 0:BW].astype(np.float32)
cx, ww = BW / 2, BW * 0.46
y0, yb = BH * 0.2, BH * 0.86
ys_ = y0 + ww * 1.0


def in_arch(pad):
    t = (xs - cx) / (ww / 2 + pad)
    top = ys_ - (ys_ - (y0 - pad)) * (1 - t * t)
    return (np.abs(t) <= 1) & (ys <= yb + pad) & (ys >= top)


rose_c, rose_r = (cx, BH * 0.1), BW * 0.1
dr = np.hypot(xs - rose_c[0], ys - rose_c[1])
glass_m = in_arch(0) | (dr < rose_r)
frame_m = ~glass_m & (in_arch(12) | (dr < rose_r + 10))


CROPS = {  # fractions of each photo that hold glass: one tall lancet (east), the window wall (west)
    "glass_cool": (0.285, 0.02, 0.47, 0.77),
    "glass_warm": (0.13, 0.03, 0.87, 0.95),
}


def glass_fill(name):
    """Crop the panes from the photo and stretch them over the bay's window area."""
    im = load(name)
    w, h = im.size
    a, b, c, d = CROPS[name]
    crop = arr(im.crop((int(w * a), int(h * b), int(w * c), int(h * d))))
    top = Image.fromarray((crop[: crop.shape[0] // 4] * 255).astype(np.uint8))
    body = Image.fromarray((crop * 255).astype(np.uint8))
    # window area spans y0..yb; the rose takes the top quarter of the crop
    out = np.zeros((BH, BW, 3), dtype=np.float32)
    ay0, ay1 = int(y0 - 2), int(yb + 2)
    ax0, ax1 = int(cx - ww / 2 - 2), int(cx + ww / 2 + 2)
    out[ay0:ay1, ax0:ax1] = arr(body.resize((ax1 - ax0, ay1 - ay0), Image.LANCZOS))
    ry0, ry1 = int(rose_c[1] - rose_r - 2), int(rose_c[1] + rose_r + 2)
    rx0, rx1 = int(rose_c[0] - rose_r - 2), int(rose_c[0] + rose_r + 2)
    out[ry0:ry1, rx0:rx1] = arr(top.resize((rx1 - rx0, ry1 - ry0), Image.LANCZOS))
    return out


def glass_light(g):
    """Glow only where the photo shows lit, coloured glass (not stone or lead)."""
    mx, mn = g.max(axis=2), g.min(axis=2)
    m = np.clip((mx - mn - 0.12) / 0.25, 0, 1) * np.clip((mx - 0.2) / 0.3, 0, 1)
    return m


fills = {"cool": glass_fill("glass_cool"), "warm": glass_fill("glass_warm")}
fills["mix"] = np.concatenate([fills["cool"][:, : BW // 2], fills["warm"][:, BW // 2:]], axis=1)

bh = blur(lum(bstone), 1.2) * 0.35 + 0.5
bh[frame_m] = 0.95
bh[glass_m] = 0.2 + blur(lum(fills["mix"]), 0.8)[glass_m] * 0.08
save_rgb(normal_from_height(bh, 5.0), "bay_n.jpg", 90)
rough = np.full((BH, BW), 0.86, dtype=np.float32)
rough[glass_m] = 0.07
save_rgb(np.dstack([rough] * 3), "bay_r.jpg", 90)
for key, g in fills.items():
    c = bstone.copy()
    c[frame_m] *= 1.05
    c[glass_m] = g[glass_m] * 0.24  # from outside in daylight the glass reads dark
    save_rgb(c, f"bay_{key}.jpg")
    e = np.zeros_like(c)
    e[glass_m] = np.clip(g[glass_m] * 1.2 * glass_light(g)[glass_m][:, None], 0, 1)
    save_rgb(e, f"bay_{key}_e.jpg")

# ---- reference photo gallery ----
for name in ["aerial", "nativity", "passion", "glory", "apse", "full", "towers", "nave", "columns", "vault", "altar", "glass_cool", "glass_warm"]:
    im = load(name)
    im.thumbnail((1100, 1100), Image.LANCZOS)
    im.save(PHO / f"{name}.webp", quality=82, method=6)

print("textures:", sorted(p.name for p in TEX.iterdir()))
print("photos:", sorted(p.name for p in PHO.iterdir()))

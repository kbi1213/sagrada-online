"""Cut the character sheets into billboard sprites for the 3D page.

usage: python tools/build_avatars.py <kpop_character_sheets dir> <cutouts dir>
<cutouts dir>/<n>.png are the sheets with the backdrop removed (Higgsfield background remover):
panels left to right are front body (cropped at the neck), back view, face close-up.
The front sprite gets the head from the face panel, scaled to the body.
faces/<n>_<name>.png gives the picker thumbnail.
Writes public/avatars/<n>_front.webp, <n>_back.webp, <n>_face.webp.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SHEETS = Path(sys.argv[1])
CUTS = Path(sys.argv[2])
OUT = Path(__file__).resolve().parent.parent / "public" / "avatars"
OUT.mkdir(parents=True, exist_ok=True)
W, H = 440, 1000  # every sprite shares one frame: feet on the bottom edge, same scale


def alpha(im):
    return np.asarray(im.getchannel("A"), dtype=np.uint8)


def trim(im):
    box = im.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
    return im.crop(box) if box else im


def panels(cut):
    """Split the sheet on fully transparent column gaps; keep the three widest pieces in order."""
    cols = (alpha(cut) > 20).sum(axis=0)
    segs, start = [], None
    for x, v in enumerate(cols):
        if v > 2 and start is None:
            start = x
        elif v <= 2 and start is not None:
            segs.append((start, x)); start = None
    if start is not None:
        segs.append((start, len(cols)))
    segs = sorted(sorted(segs, key=lambda s: s[1] - s[0], reverse=True)[:3])
    return [trim(cut.crop((a, 0, b, cut.height))) for a, b in segs]


def width_at(im, frac):
    row = alpha(im)[int(im.height * frac)]
    xs = np.nonzero(row > 40)[0]
    return (xs[-1] - xs[0]) if len(xs) else im.width


def with_head(body, face):
    """Put the face panel's head on the neck-less front body."""
    head = face.crop((0, 0, face.width, int(face.height * 0.56)))
    head = trim(head)
    # head (crown to chin) ≈ 0.43 of the cropped head image height; a body from neck to feet ≈ 6.4 heads
    target_head = body.height / 6.4
    scale = target_head / (head.height * 0.82)
    head = head.resize((max(1, round(head.width * scale)), max(1, round(head.height * scale))), Image.LANCZOS)
    overlap = int(head.height * 0.14)
    canvas = Image.new("RGBA", (max(body.width, head.width), body.height + head.height - overlap), (0, 0, 0, 0))
    bx = (canvas.width - body.width) // 2
    canvas.paste(body, (bx, head.height - overlap), body)
    canvas.alpha_composite(head, ((canvas.width - head.width) // 2, 0))
    # the body (collar) sits over the bottom of the neck
    collar = body.crop((0, 0, body.width, overlap))
    canvas.alpha_composite(collar, (bx, head.height - overlap))
    return canvas


def frame(im):
    im = trim(im)
    im.thumbnail((W, H), Image.LANCZOS)
    c = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    c.paste(im, ((W - im.width) // 2, H - im.height), im)
    return c


done = []
for face_file in sorted((SHEETS / "faces").glob("*.png")):
    n = face_file.stem.split("_", 1)[0]
    cut = Image.open(CUTS / f"{n}.png").convert("RGBA")
    front, back, face = panels(cut)
    frame(with_head(front, face)).save(OUT / f"{n}_front.webp", quality=88, method=6)
    frame(back).save(OUT / f"{n}_back.webp", quality=88, method=6)
    f = Image.open(face_file).convert("RGB")
    fw, fh = f.size
    side = int(fw * 0.62)
    top = int(fh * 0.12)
    f.crop(((fw - side) // 2, top, (fw + side) // 2, top + side)).resize((160, 160), Image.LANCZOS).save(OUT / f"{n}_face.webp", quality=86)
    done.append(face_file.stem)
print("avatars:", done)

"""Shrink the embedded textures of a GLB (resize to max N px, re-encode) without touching geometry or animation.

usage: python tools/shrink_glb.py <in.glb> <out.glb> [max_px=1024]
"""
import io
import json
import struct
import sys

from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
MAX = int(sys.argv[3]) if len(sys.argv) > 3 else 1024

data = open(src, "rb").read()
magic, version, _ = struct.unpack("<4sII", data[:12])
assert magic == b"glTF" and version == 2, "not a glTF 2 binary"
off, chunks = 12, []
while off < len(data):
    length, ctype = struct.unpack("<II", data[off:off + 8])
    chunks.append((ctype, data[off + 8:off + 8 + length]))
    off += 8 + length
gltf = json.loads(next(c for t, c in chunks if t == 0x4E4F534A))
binbuf = next(c for t, c in chunks if t == 0x004E4942)

image_views = {img["bufferView"]: img for img in gltf.get("images", []) if "bufferView" in img}
new_parts, new_views, cursor = [], [], 0
for i, view in enumerate(gltf["bufferViews"]):
    start = view.get("byteOffset", 0)
    blob = binbuf[start:start + view["byteLength"]]
    if i in image_views:
        img = image_views[i]
        im = Image.open(io.BytesIO(blob))
        im.load()
        if max(im.size) > MAX:
            im.thumbnail((MAX, MAX), Image.LANCZOS)
        out = io.BytesIO()
        if im.mode in ("RGBA", "LA") or img.get("mimeType") == "image/png":
            im.save(out, "PNG", optimize=True)
            img["mimeType"] = "image/png"
        else:
            im.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
            img["mimeType"] = "image/jpeg"
        blob = out.getvalue()
    pad = (-cursor) % 4
    new_parts.append(b"\x00" * pad)
    cursor += pad
    nv = dict(view)
    nv["byteOffset"] = cursor
    nv["byteLength"] = len(blob)
    nv["buffer"] = 0
    new_views.append(nv)
    new_parts.append(blob)
    cursor += len(blob)
gltf["bufferViews"] = new_views
newbin = b"".join(new_parts)
newbin += b"\x00" * ((-len(newbin)) % 4)
gltf["buffers"] = [{"byteLength": len(newbin)}]
js = json.dumps(gltf, separators=(",", ":")).encode()
js += b" " * ((-len(js)) % 4)
total = 12 + 8 + len(js) + 8 + len(newbin)
with open(dst, "wb") as f:
    f.write(struct.pack("<4sII", b"glTF", 2, total))
    f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
    f.write(struct.pack("<II", len(newbin), 0x004E4942) + newbin)
print(f"{src}: {len(data)} -> {total} bytes")

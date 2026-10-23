# -*- coding: utf-8 -*-
"""
reconstruir.py  -  Reconstruye la imagen a RESOLUCION COMPLETA desde un .clip,
usando el render aplanado que Clip Studio ya dejo cacheado en el folder raiz.

Uso:
    python reconstruir.py "ruta\\al\\archivo.clip"  [salida.png]
"""
import sys, sqlite3, tempfile, zlib
import numpy as np
from PIL import Image
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"
TILE = 256

def carve(data):
    i = data.find(SQLITE_MAGIC)
    ps = int.from_bytes(data[i+16:i+18], 'big'); ps = 65536 if ps == 1 else ps
    pc = int.from_bytes(data[i+28:i+32], 'big')
    return data[i:i+ps*pc] if ps and pc else data[i:]

def ext_map(data):
    chunks = {}
    pos = data.find(b'CHNKExta')
    while pos != -1:
        idlen = int.from_bytes(data[pos+16:pos+24], 'big')
        extid = bytes(data[pos+24:pos+24+idlen]).decode('ascii', 'ignore')
        dlen = int.from_bytes(data[pos+24+idlen:pos+32+idlen], 'big')
        chunks[extid] = data[pos+32+idlen: pos+32+idlen+dlen]
        pos = data.find(b'CHNKExta', pos+8)
    return chunks

def parse_attr(attr):
    """Devuelve (width, height) del offscreen leyendo tras 'Parameter'."""
    m = attr.find("Parameter".encode('utf-16-be'))
    p = m + len("Parameter")*2
    w = int.from_bytes(attr[p:p+4], 'big')
    h = int.from_bytes(attr[p+4:p+8], 'big')
    return w, h

def decode_offscreen(blob, W, H):
    """Decodifica los tiles del blob externo a un array (H', W', C)."""
    gw = (W + TILE - 1)//TILE
    gh = (H + TILE - 1)//TILE
    full = None
    marker = b'\x00B\x00l\x00o\x00c\x00k\x00D\x00a\x00t\x00a\x00B\x00e\x00g\x00i\x00n'
    o = blob.find(marker)
    while o != -1:
        nlen = int.from_bytes(blob[o-4:o], 'big')     # 19
        after = o + nlen*2
        index = int.from_bytes(blob[after:after+4], 'big')
        zstart = after + 28
        if blob[zstart] == 0x78:                       # hay datos
            raw = zlib.decompressobj().decompress(blob[zstart:])
            C = len(raw)//(TILE*TILE)
            if full is None:
                full = np.zeros((gh*TILE, gw*TILE, C), np.uint8)
            tile = np.frombuffer(raw, np.uint8).reshape(TILE, TILE, C)
            r, c = index//gw, index % gw
            full[r*TILE:(r+1)*TILE, c*TILE:(c+1)*TILE] = tile
        o = blob.find(marker, o+1)
    return full[:H, :W] if full is not None else None

def main():
    clip = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else clip.with_name("dibujo_4k.png")
    data = clip.read_bytes()

    tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False); tmp.write(carve(data)); tmp.close()
    con = sqlite3.connect(tmp.name); cur = con.cursor()

    root = cur.execute("SELECT CanvasRootFolder FROM Canvas").fetchone()[0]
    rmip = cur.execute("SELECT LayerRenderMipmap FROM Layer WHERE MainId=?", (root,)).fetchone()[0]
    base = cur.execute("SELECT BaseMipmapInfo FROM Mipmap WHERE MainId=?", (rmip,)).fetchone()[0]
    offid = cur.execute("SELECT Offscreen FROM MipmapInfo WHERE MainId=?", (base,)).fetchone()[0]
    attr, bd = cur.execute("SELECT Attribute, BlockData FROM Offscreen WHERE MainId=?", (offid,)).fetchone()
    extid = bytes(bd).split(b'\x00')[0].decode('ascii', 'ignore')  # 'extrnlid....'
    W, H = parse_attr(bytes(attr))
    print("Folder raiz=%d  render full-res %dx%d  chunk=%s" % (root, W, H, extid))

    chunks = ext_map(data)
    full = decode_offscreen(chunks[extid], W, H)
    print("Decodificado:", full.shape, "canales=%d" % full.shape[2])

    # calibrar orden de canales contra el preview
    prev = np.asarray(Image.open(clip.with_name(clip.stem + "_extraido") / "CanvasPreview_ImageData_001.png").convert("RGB"))
    ph, pw = prev.shape[:2]
    small = [np.asarray(Image.fromarray(full[:, :, k]).resize((pw, ph), Image.LANCZOS)) for k in range(full.shape[2])]
    order = []
    for t in range(3):  # R,G,B del preview
        maes = [np.abs(small[k].astype(int) - prev[:, :, t].astype(int)).mean() for k in range(full.shape[2])]
        best = int(np.argmin(maes))
        order.append(best)
        print("  canal preview %d -> plano %d  (MAE=%.2f)  todos=%s"
              % (t, best, maes[best], ["%.1f" % m for m in maes]))

    rgb = np.dstack([full[:, :, order[0]], full[:, :, order[1]], full[:, :, order[2]]])
    # plano de alfa = el que queda y parece mascara (muchos 0/255); si no, opaco
    used = set(order)
    alpha_candidates = [k for k in range(full.shape[2]) if k not in used]
    img = Image.fromarray(rgb, "RGB")
    img.save(out)
    print("\nGuardado:", out, rgb.shape)

if __name__ == "__main__":
    main()

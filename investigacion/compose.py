# -*- coding: utf-8 -*-
"""
compose.py  -  Reconstruye el dibujo a RESOLUCION COMPLETA componiendo las capas
visibles de un .clip (decodifica tiles, calibra canales contra el preview y aplana).

Uso:
    python compose.py "archivo.clip" [salida.png]
"""
import sys, sqlite3, tempfile, zlib, itertools
import numpy as np
from PIL import Image
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"; TILE = 256
MARKER = b'\x00B\x00l\x00o\x00c\x00k\x00D\x00a\x00t\x00a\x00B\x00e\x00g\x00i\x00n'

def carve(data):
    i = data.find(SQLITE_MAGIC); ps = int.from_bytes(data[i+16:i+18],'big'); ps = 65536 if ps==1 else ps
    pc = int.from_bytes(data[i+28:i+32],'big'); return data[i:i+ps*pc] if ps and pc else data[i:]

def ext_map(data):
    ch = {}; pos = data.find(b'CHNKExta')
    while pos != -1:
        il = int.from_bytes(data[pos+16:pos+24],'big')
        eid = bytes(data[pos+24:pos+24+il]).decode('ascii','ignore')
        dl = int.from_bytes(data[pos+24+il:pos+32+il],'big')
        ch[eid] = data[pos+32+il:pos+32+il+dl]; pos = data.find(b'CHNKExta', pos+8)
    return ch

def pattr(attr):
    m = attr.find("Parameter".encode('utf-16-be')); p = m+18
    return int.from_bytes(attr[p:p+4],'big'), int.from_bytes(attr[p+4:p+8],'big')

def decode_offscreen(blob, W, H):
    gw = (W+TILE-1)//TILE; gh = (H+TILE-1)//TILE
    full = None; o = blob.find(MARKER)
    while o != -1:
        after = o+38; zs = after+28
        idx = int.from_bytes(blob[after:after+4],'big')
        if zs < len(blob) and blob[zs] == 0x78:
            raw = np.frombuffer(zlib.decompressobj().decompress(blob[zs:]), np.uint8)
            C = len(raw)//(TILE*TILE)
            if C == 5:   # plano de alfa + color intercalado de 4 bytes -> [c0,c1,c2,c3,alfa]
                alpha = raw[:TILE*TILE].reshape(TILE, TILE, 1)
                tile = np.concatenate([raw[TILE*TILE:].reshape(TILE, TILE, 4), alpha], axis=2)
            else:
                tile = raw.reshape(TILE, TILE, C)
            if full is None: full = np.zeros((gh*TILE, gw*TILE, C), np.uint8)
            r, c = idx//gw, idx % gw
            if r < gh and c < gw:
                full[r*TILE:(r+1)*TILE, c*TILE:(c+1)*TILE] = tile
        o = blob.find(MARKER, o+1)
    return full[:H, :W] if full is not None else None

def visible_layers(cur):
    """Lista (name, mainid, opacity, composite, offscreen_mainid) de abajo->arriba."""
    root = cur.execute("SELECT CanvasRootFolder FROM Canvas").fetchone()[0]
    info = {}
    for mid, nm, fol, nxt, vis, op, comp, rmip in cur.execute(
        """SELECT MainId, LayerName, LayerFolder, LayerNextIndex, LayerVisibility,
                  LayerOpacity, LayerComposite, LayerRenderMipmap FROM Layer"""):
        info[mid] = dict(name=nm, folder=fol, nxt=nxt, vis=vis, op=op, comp=comp, rmip=rmip)
    first = cur.execute("SELECT LayerFirstChildIndex FROM Layer WHERE MainId=?", (root,)).fetchone()[0]
    order = []
    mid = first
    while mid and mid in info:
        order.append(mid); mid = info[mid]['nxt']
    out = []
    for mid in order:
        L = info[mid]
        if L['vis'] != 1:  # solo visibles (bit0)
            continue
        r = cur.execute("SELECT BaseMipmapInfo FROM Mipmap WHERE MainId=?", (L['rmip'],)).fetchone()
        if not r: continue
        off = cur.execute("SELECT Offscreen FROM MipmapInfo WHERE MainId=?", (r[0],)).fetchone()[0]
        out.append((L['name'], mid, L['op'], L['comp'], off))
    return out

def main():
    clip = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else clip.with_name("dibujo_4k.png")
    data = clip.read_bytes()
    tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False); tmp.write(carve(data)); tmp.close()
    cur = sqlite3.connect(tmp.name).cursor()
    W, H = [int(x) for x in cur.execute("SELECT CanvasWidth, CanvasHeight FROM Canvas").fetchone()]
    chunks = ext_map(data)
    layers = visible_layers(cur)
    print("Lienzo %dx%d.  Capas visibles con raster:" % (W, H))

    # --- decodificar cada capa visible una vez; guardar planos a baja-res para calibrar ---
    prev = np.asarray(Image.open(clip.with_name(clip.stem + "_extraido")/"CanvasPreview_ImageData_001.png").convert("RGB")).astype(np.float32)
    PH, PW = prev.shape[:2]
    cache = []   # (planes_lowres[C,PH,PW] float32, op)
    for nm, mid, op, comp, off in layers:
        row = cur.execute("SELECT Attribute, BlockData FROM Offscreen WHERE MainId=?", (off,)).fetchone()
        if not row: continue
        ow, oh = pattr(bytes(row[0])); eid = bytes(row[1]).split(b'\x00')[0].decode('ascii','ignore')
        if eid not in chunks:
            print("  (salteo relleno sin raster: %s)" % nm); continue
        dec = decode_offscreen(chunks[eid], ow, oh)
        if dec is None: continue
        C = dec.shape[2]
        canv = np.zeros((H, W, C), np.uint8)
        canv[:min(H,oh), :min(W,ow)] = dec[:min(H,oh), :min(W,ow)]
        low = np.stack([np.asarray(Image.fromarray(canv[:,:,k]).resize((PW,PH), Image.BILINEAR)) for k in range(C)]).astype(np.float32)
        cache.append((low, op, nm))
        print("  %-12s op=%3d cmp=%2d C=%d" % (nm, op, comp, C))

    # --- calibrar: que planos son R,G,B,alfa y si el color esta premultiplicado ---
    def composite(planes_list, a_idx, rgb_idx, premult):
        dst = np.full((PH, PW, 3), 255.0, np.float32)
        for planes, op, nm in planes_list:
            a = planes[a_idx]/255.0 * (op/256.0)
            col = np.stack([planes[rgb_idx[0]], planes[rgb_idx[1]], planes[rgb_idx[2]]], axis=-1)
            a3 = a[:,:,None]
            if premult:
                dst = dst*(1-a3) + col*(op/256.0)
            else:
                dst = dst*(1-a3) + col*a3
        return dst

    best = None
    C = cache[0][0].shape[0]
    for a_idx in range(C):
        rest = [k for k in range(C) if k != a_idx]
        for rgb in itertools.permutations(rest, 3):
            for premult in (False, True):
                res = composite(cache, a_idx, rgb, premult)
                mae = np.abs(res - prev).mean()
                if best is None or mae < best[0]:
                    best = (mae, a_idx, rgb, premult)
    mae, a_idx, rgb, premult = best
    print("\nCalibracion -> alfa=plano%d  RGB=planos%s  premult=%s  MAE=%.2f"
          % (a_idx, rgb, premult, mae))

    # --- composicion final a resolucion completa (re-decodificando capa por capa) ---
    dst = np.full((H, W, 3), 255.0, np.float32)
    for nm, mid, op, comp, off in layers:
        row = cur.execute("SELECT Attribute, BlockData FROM Offscreen WHERE MainId=?", (off,)).fetchone()
        if not row: continue
        ow, oh = pattr(bytes(row[0])); eid = bytes(row[1]).split(b'\x00')[0].decode('ascii','ignore')
        if eid not in chunks: continue
        dec = decode_offscreen(chunks[eid], ow, oh)
        if dec is None: continue
        canv = np.zeros((H, W, dec.shape[2]), np.uint8)
        canv[:min(H,oh), :min(W,ow)] = dec[:min(H,oh), :min(W,ow)]
        a = canv[:,:,a_idx].astype(np.float32)/255.0 * (op/256.0)
        col = canv[:,:,list(rgb)].astype(np.float32)
        a3 = a[:,:,None]
        dst = dst*(1-a3) + (col*(op/256.0) if premult else col*a3)
    Image.fromarray(np.clip(dst,0,255).astype(np.uint8), "RGB").save(out)
    print("Guardado 4K:", out)

if __name__ == "__main__":
    main()

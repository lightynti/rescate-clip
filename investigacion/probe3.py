# -*- coding: utf-8 -*-
"""Decodifica una capa y muestra estadisticas por plano para deducir el formato."""
import sys, sqlite3, tempfile, zlib
import numpy as np
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"; TILE = 256
def carve(data):
    i = data.find(SQLITE_MAGIC)
    ps = int.from_bytes(data[i+16:i+18], 'big'); ps = 65536 if ps == 1 else ps
    pc = int.from_bytes(data[i+28:i+32], 'big')
    return data[i:i+ps*pc] if ps and pc else data[i:]
def ext_map(data):
    ch = {}; pos = data.find(b'CHNKExta')
    while pos != -1:
        il = int.from_bytes(data[pos+16:pos+24], 'big')
        eid = bytes(data[pos+24:pos+24+il]).decode('ascii', 'ignore')
        dl = int.from_bytes(data[pos+24+il:pos+32+il], 'big')
        ch[eid] = data[pos+32+il:pos+32+il+dl]; pos = data.find(b'CHNKExta', pos+8)
    return ch

data = Path(sys.argv[1]).read_bytes()
name = sys.argv[2] if len(sys.argv) > 2 else 'Papel'
tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False); tmp.write(carve(data)); tmp.close()
cur = sqlite3.connect(tmp.name).cursor()

mid = cur.execute("SELECT MainId, LayerRenderMipmap FROM Layer WHERE LayerName=?", (name,)).fetchone()
print("Capa '%s' MainId=%d RenderMipmap=%d" % (name, mid[0], mid[1]))
base = cur.execute("SELECT BaseMipmapInfo FROM Mipmap WHERE MainId=?", (mid[1],)).fetchone()[0]
offid = cur.execute("SELECT Offscreen FROM MipmapInfo WHERE MainId=?", (base,)).fetchone()[0]
bd = cur.execute("SELECT BlockData FROM Offscreen WHERE MainId=?", (offid,)).fetchone()[0]
eid = bytes(bd).split(b'\x00')[0].decode('ascii')
blob = ext_map(data)[eid]
print("offscreen", offid, "chunk", eid, "len", len(blob))

marker = b'\x00B\x00l\x00o\x00c\x00k\x00D\x00a\x00t\x00a\x00B\x00e\x00g\x00i\x00n'
o = blob.find(marker); shown = 0
while o != -1 and shown < 4:
    after = o + 19*2
    idx = int.from_bytes(blob[after:after+4], 'big')
    zs = after + 28
    if blob[zs] == 0x78:
        raw = np.frombuffer(zlib.decompressobj().decompress(blob[zs:]), np.uint8)
        C = len(raw)//(TILE*TILE)
        print("\n-- tile idx=%d  bytes=%d  C=%d --" % (idx, len(raw), C))
        pl = raw.reshape(C, TILE, TILE)
        print("  PLANAR  (plano: min/max/mean/%ceros)")
        for k in range(C):
            p = pl[k]; print("    p%d: %3d/%3d/%6.1f/%.0f%%" % (k, p.min(), p.max(), p.mean(), (p==0).mean()*100))
        il = raw.reshape(TILE, TILE, C)
        print("  INTERLEAVED (canal: min/max/mean/%ceros)")
        for k in range(C):
            p = il[:, :, k]; print("    c%d: %3d/%3d/%6.1f/%.0f%%" % (k, p.min(), p.max(), p.mean(), (p==0).mean()*100))
        shown += 1
    o = blob.find(marker, o+1)

# -*- coding: utf-8 -*-
"""Recorre todas las capas: chunk presente?, tamano offscreen, medias por plano."""
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
def pattr(attr):
    m = attr.find("Parameter".encode('utf-16-be')); p = m+18
    return int.from_bytes(attr[p:p+4],'big'), int.from_bytes(attr[p+4:p+8],'big')

data = Path(sys.argv[1]).read_bytes()
tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False); tmp.write(carve(data)); tmp.close()
cur = sqlite3.connect(tmp.name).cursor()
chunks = ext_map(data)
marker = b'\x00B\x00l\x00o\x00c\x00k\x00D\x00a\x00t\x00a\x00B\x00e\x00g\x00i\x00n'

rows = cur.execute("""SELECT MainId, LayerName, LayerRenderMipmap, LayerVisibility,
                             LayerOpacity, LayerComposite FROM Layer ORDER BY MainId""").fetchall()
print("%-14s %5s %3s %3s %3s  %-9s  %-11s  %s" % ("capa","vis","op","cmp","", "offscreen","chunk","planos (media / %ceros) sobre ~3 tiles"))
for mid, nm, rmip, vis, op, cmp in rows:
    r = cur.execute("SELECT BaseMipmapInfo FROM Mipmap WHERE MainId=?", (rmip,)).fetchone()
    if not r: print("%-14s vis=%d op=%3d cmp=%2d  (sin mipmap)" % (nm,vis,op,cmp)); continue
    off = cur.execute("SELECT Offscreen FROM MipmapInfo WHERE MainId=?", (r[0],)).fetchone()[0]
    row = cur.execute("SELECT Attribute, BlockData FROM Offscreen WHERE MainId=?", (off,)).fetchone()
    if not row: print("%-14s vis=%d op=%3d cmp=%2d  (sin offscreen)" % (nm,vis,op,cmp)); continue
    W,H = pattr(bytes(row[0])); eid = bytes(row[1]).split(b'\x00')[0].decode('ascii','ignore')
    if eid not in chunks:
        print("%-14s vis=%d op=%3d cmp=%2d  %4dx%-4d  AUSENTE" % (nm,vis,op,cmp,W,H)); continue
    blob = chunks[eid]; o = blob.find(marker); acc = []; n=0
    while o != -1 and n < 3:
        after=o+38; zs=after+28
        if blob[zs]==0x78:
            raw=np.frombuffer(zlib.decompressobj().decompress(blob[zs:]),np.uint8)
            C=len(raw)//65536; acc.append(raw.reshape(C,256,256)); n+=1
        o=blob.find(marker,o+1)
    if acc:
        a=np.stack(acc); C=a.shape[1]
        st=" ".join("p%d:%5.1f/%2.0f%%"%(k,a[:,k].mean(),(a[:,k]==0).mean()*100) for k in range(C))
        print("%-14s vis=%d op=%3d cmp=%2d  %4dx%-4d C=%d  %s" % (nm,vis,op,cmp,W,H,C,st))

# -*- coding: utf-8 -*-
"""Inspecciona la estructura interna de un .clip para planear la reconstruccion."""
import sys, sqlite3, tempfile
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"

def carve(data):
    i = data.find(SQLITE_MAGIC)
    ps = int.from_bytes(data[i+16:i+18], 'big'); ps = 65536 if ps == 1 else ps
    pc = int.from_bytes(data[i+28:i+32], 'big')
    return data[i:i+ps*pc] if ps and pc else data[i:]

clip = Path(sys.argv[1])
data = clip.read_bytes()
db = carve(data)
tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False); tmp.write(db); tmp.close()
con = sqlite3.connect(tmp.name); cur = con.cursor()

def cols(table):
    cur.execute('PRAGMA table_info("%s")' % table)
    return [r[1] for r in cur.fetchall()]

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("TABLAS:", ", ".join(tables))

for t in ("Canvas",):
    if t in tables:
        c = cols(t)
        cur.execute('SELECT * FROM "%s" LIMIT 1' % t)
        row = cur.fetchone()
        print("\n== %s ==" % t)
        for k, v in zip(c, row or []):
            if isinstance(v, (bytes, bytearray)):
                v = "<blob %d bytes>" % len(v)
            print("  %-28s %s" % (k, v))

if "Layer" in tables:
    c = cols("Layer")
    print("\n== Layer (columnas) ==")
    print("  " + ", ".join(c))
    pick = [x for x in c if x.lower() in
            ("layername", "layeropacity", "layercomposite", "layervisibility",
             "layerlayermaskmipmap", "layerrendermipmap", "layerfirstchildindex",
             "layertype", "canvasindex")]
    cur.execute("SELECT MainId, %s FROM Layer" % (", ".join('"%s"' % p for p in pick) if pick else "LayerName"))
    print("\n== Layer (filas) ==")
    for r in cur.fetchall():
        print("  ", r)

if "Offscreen" in tables:
    c = cols("Offscreen")
    print("\n== Offscreen (columnas) ==")
    print("  " + ", ".join(c))
    cur.execute('SELECT * FROM "Offscreen" LIMIT 3')
    for n, row in enumerate(cur.fetchall()):
        print("\n  -- fila %d --" % n)
        for k, v in zip(c, row):
            if isinstance(v, (bytes, bytearray)):
                head = bytes(v[:64])
                print("    %-24s <blob %d> %r" % (k, len(v), head))
            else:
                print("    %-24s %s" % (k, v))

print("\n== Chunks externos del .clip ==")
pos = data.find(b'CHNKExta'); n = 0
while pos != -1 and n < 40:
    idlen = int.from_bytes(data[pos+16:pos+24], 'big')
    extid = bytes(data[pos+24:pos+24+idlen])
    dlen = int.from_bytes(data[pos+24+idlen:pos+32+idlen], 'big')
    print("  id=%s  datos=%d bytes" % (extid.decode('ascii', 'ignore'), dlen))
    pos = data.find(b'CHNKExta', pos+8); n += 1
print("  total externos:", n)

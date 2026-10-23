# -*- coding: utf-8 -*-
"""Vuelca Mipmap, MipmapInfo y el orden/arbol de capas."""
import sys, sqlite3, tempfile
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"
def carve(data):
    i = data.find(SQLITE_MAGIC)
    ps = int.from_bytes(data[i+16:i+18], 'big'); ps = 65536 if ps == 1 else ps
    pc = int.from_bytes(data[i+28:i+32], 'big')
    return data[i:i+ps*pc] if ps and pc else data[i:]

data = Path(sys.argv[1]).read_bytes()
tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False); tmp.write(carve(data)); tmp.close()
con = sqlite3.connect(tmp.name); cur = con.cursor()

def dump(table, cols, limit=200):
    try:
        cur.execute('SELECT %s FROM "%s" LIMIT %d' % (cols, table, limit))
    except Exception as e:
        print("  (no se pudo: %s)" % e); return
    for r in cur.fetchall():
        print("  ", r)

print("== Mipmap (MainId, BaseMipmapInfo, ...) ==")
cur.execute("PRAGMA table_info(Mipmap)"); print("  cols:", [c[1] for c in cur.fetchall()])
dump("Mipmap", "MainId, BaseMipmapInfo")

print("\n== MipmapInfo (MainId, Offscreen, NextIndex, ThisScale) ==")
cur.execute("PRAGMA table_info(MipmapInfo)"); print("  cols:", [c[1] for c in cur.fetchall()])
dump("MipmapInfo", "MainId, Offscreen, NextIndex, ThisScale")

print("\n== Orden de capas (MainId, Name, Folder, Opacity, Composite, Visible, First, Next, RenderMipmap) ==")
cur.execute("""SELECT MainId, LayerName, LayerFolder, LayerOpacity, LayerComposite,
                      LayerVisibility, LayerFirstChildIndex, LayerNextIndex,
                      LayerRenderMipmap, LayerOffsetX, LayerOffsetY,
                      LayerRenderOffscrOffsetX, LayerRenderOffscrOffsetY
               FROM Layer ORDER BY MainId""")
print("  (MainId, Name, Folder, Op, Comp, Vis, First, Next, RMip, offX, offY, rOffX, rOffY)")
for r in cur.fetchall():
    print("  ", r)
print("\nRootFolder:", end=" ")
cur.execute("SELECT CanvasRootFolder FROM Canvas"); print(cur.fetchone())

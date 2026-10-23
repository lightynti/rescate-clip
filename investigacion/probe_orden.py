# -*- coding: utf-8 -*-
import sys, sqlite3, tempfile
from pathlib import Path
SQLITE_MAGIC = b"SQLite format 3\x00"
def carve(data):
    i = data.find(SQLITE_MAGIC); ps = int.from_bytes(data[i+16:i+18],'big'); ps = 65536 if ps==1 else ps
    pc = int.from_bytes(data[i+28:i+32],'big'); return data[i:i+ps*pc] if ps and pc else data[i:]
data = Path(sys.argv[1]).read_bytes()
tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False); tmp.write(carve(data)); tmp.close()
cur = sqlite3.connect(tmp.name).cursor()
print("root:", cur.execute("SELECT CanvasRootFolder FROM Canvas").fetchone())
print("%-6s %-6s %-14s %-6s %-6s %-6s %-6s" % ("_PW","MainId","Name","Folder","First","Next","Vis"))
for r in cur.execute("""SELECT _PW_ID, MainId, LayerName, LayerFolder,
        LayerFirstChildIndex, LayerNextIndex, LayerVisibility FROM Layer ORDER BY _PW_ID"""):
    print("%-6s %-6s %-14s %-6s %-6s %-6s %-6s" % r)

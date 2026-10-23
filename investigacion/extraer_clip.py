# -*- coding: utf-8 -*-
"""
extraer_clip.py  -  Rescata las imagenes guardadas dentro de un archivo .clip
(Clip Studio Paint) leyendolo directamente, SIN abrir el programa.

Un .clip es, por dentro, una base de datos SQLite con los datos del lienzo.
Este script:
  1) localiza esa base SQLite dentro del .clip,
  2) saca la vista previa del lienzo (PNG del dibujo aplanado),
  3) vuelca cualquier otra imagen PNG que encuentre embebida.

Esto no toca la licencia ni el programa: solo LEE tu propio archivo.

Uso:
    python extraer_clip.py "C:\\ruta\\a\\mi_dibujo.clip"

Las imagenes salen en una carpeta  <nombre_del_clip>_extraido  junto al .clip.
Solo usa la biblioteca estandar de Python (nada que instalar).
"""

import sys
import sqlite3
import tempfile
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SQLITE_MAGIC = b"SQLite format 3\x00"


def carve_sqlite(data):
    """Encuentra y recorta la base SQLite embebida en el .clip."""
    i = data.find(SQLITE_MAGIC)
    if i == -1:
        return None
    page_size = int.from_bytes(data[i + 16:i + 18], "big")
    if page_size == 1:          # 1 significa 65536 en SQLite
        page_size = 65536
    page_count = int.from_bytes(data[i + 28:i + 32], "big")
    if page_size and page_count:
        end = i + page_size * page_count
        if end <= len(data):
            return data[i:end]
    return data[i:]             # plan B: desde el magic hasta el final del archivo


def dump_pngs(db_path, out_dir):
    con = sqlite3.connect("file:{}?mode=ro&immutable=1".format(db_path), uri=True)
    con.text_factory = bytes
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0].decode("utf-8", "ignore") for t in cur.fetchall()]

    count = 0
    for table in tables:
        try:
            cur.execute('SELECT * FROM "{}"'.format(table))
        except sqlite3.DatabaseError:
            continue
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            for col, val in zip(cols, row):
                if isinstance(val, (bytes, bytearray)) and bytes(val[:8]) == PNG_MAGIC:
                    count += 1
                    name = "{}_{}_{:03d}.png".format(table, col, count)
                    (out_dir / name).write_bytes(val)
                    print("  -> {}  ({:,} bytes)".format(name, len(val)))
    con.close()
    return count


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    clip = Path(sys.argv[1])
    if not clip.is_file():
        print("No encuentro el archivo: {}".format(clip))
        sys.exit(1)

    print("Leyendo {} ...".format(clip.name))
    data = clip.read_bytes()

    db_bytes = carve_sqlite(data)
    if db_bytes is None:
        print("No parece un .clip valido (no hay base SQLite dentro).")
        sys.exit(1)

    out_dir = clip.with_name(clip.stem + "_extraido")
    out_dir.mkdir(exist_ok=True)

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.write(db_bytes)
    tmp.close()
    tmp_path = Path(tmp.name)

    try:
        print("Extrayendo imagenes embebidas:")
        n = dump_pngs(tmp_path, out_dir)
    finally:
        tmp_path.unlink()

    if n:
        print("\nListo: {} imagen(es) en  {}".format(n, out_dir))
        print("La vista previa del lienzo suele ser el PNG mas grande (CanvasPreview).")
    else:
        print("\nNo encontre PNGs embebidos. Puede ser un .clip muy nuevo o con otro formato.")


if __name__ == "__main__":
    main()

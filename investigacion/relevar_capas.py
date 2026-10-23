# -*- coding: utf-8 -*-
"""Releva en uno o varios .clip: modos de fusion, recortes, mascaras y carpetas."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rescate_clip import ClipFile

for path in sys.argv[1:]:
    clip = ClipFile(path)
    cols = [r[1] for r in clip.cur.execute("PRAGMA table_info(Layer)")]
    print("\n=== %s  (%dx%d) ===" % (Path(path).name, clip.width, clip.height))
    q = ("SELECT MainId, LayerName, LayerFolder, LayerVisibility, LayerOpacity, LayerComposite, "
         "LayerClip, LayerLayerMaskMipmap, LayerMasking, LayerType FROM Layer")
    for mid, nm, fol, vis, op, comp, clp, mmip, masking, ltype in clip.cur.execute(q):
        flags = []
        if fol: flags.append("CARPETA")
        if comp: flags.append("fusion=%d" % comp)
        if clp: flags.append("recorte=%d" % clp)
        if mmip: flags.append("mascara=%d" % mmip)
        if masking: flags.append("masking=%d" % masking)
        if flags or op != 256:
            print("  %-22s vis=%d op=%3d tipo=%s  %s" % (nm[:22], vis, op, ltype, " ".join(flags)))
    clip.close()

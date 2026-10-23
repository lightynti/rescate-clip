# -*- coding: utf-8 -*-
"""Deduce que modo de fusion es cada codigo LayerComposite comparando contra la miniatura."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fusion
from rescate_clip import ClipFile


def report(code, errs):
    ranking = sorted(errs, key=errs.get)
    print("\nCodigo %d:" % code)
    for name in ranking[:6]:
        print("   %-22s error %.2f" % (fusion.MODES[name][0], errs[name]))
    print("   ...  Normal: %.2f" % errs["normal"])


for path in sys.argv[1:]:
    clip = ClipFile(path)
    codes = clip.blend_codes() - {0}
    print("=== %s  codigos: %s" % (Path(path).name, sorted(codes)))
    found = clip.detect_modes(codes=codes, report=report)
    print("\nResultado:", {c: fusion.MODES[m][0] for c, m in found.items()})
    clip.close()

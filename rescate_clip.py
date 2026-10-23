# -*- coding: utf-8 -*-
"""
Rescate CLIP  -  Exporta a PNG, a resolucion completa, los dibujos guardados en
archivos .clip de Clip Studio Paint (incluidos sus autoguardados de recuperacion),
leyendo el archivo directamente, sin abrir Clip Studio.

Hecho con IA (Claude, de Anthropic).

Descargo de responsabilidad: proyecto independiente, sin relacion con CELSYS ni con
Clip Studio Paint. Se ofrece "tal cual", sin garantias; el autor no se hace responsable
por perdida de datos ni por el uso que se le de. Ver DISCLAIMER mas abajo y LICENSE.
Todos los derechos reservados: no se permite modificarlo ni distribuir versiones modificadas.

Uso:
    python rescate_clip.py                      -> abre la ventana
    python rescate_clip.py dibujo.clip [s.png]  -> exporta por consola
"""
import os
import sys
import sqlite3
import threading
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

import fusion

__version__ = "1.4"

DISCLAIMER = (
    "Rescate CLIP es un proyecto independiente y no oficial. No tiene relación con CELSYS "
    "ni con Clip Studio Paint, que son marcas de sus respectivos dueños.\n\n"
    "Se ofrece «tal cual», sin garantías de ningún tipo. Usalo solo con archivos propios o "
    "que tengas derecho a usar. El programa solo lee archivos .clip: no modifica, no activa "
    "ni evita la licencia de Clip Studio. Aun así, hacé copias de seguridad de tus archivos.\n\n"
    "El autor no se hace responsable por pérdida de datos, daños ni por el uso que se le dé.\n\n"
    "Hecho con IA (Claude, de Anthropic): puede contener errores.\n\n"
    "© Joky. Todos los derechos reservados. No se permite modificar el programa ni "
    "distribuir versiones modificadas.")

TILE = 256
STRIP = 512          # filas por tanda al fundir, para no gastar memoria de mas
# LayerComposite de Clip Studio -> modo de fusion. No esta documentado: es el orden del menu
# de modos de Clip Studio (0 = Normal ... 27 = Dividir), confirmado comparando contra la
# miniatura (6, 13 y 26). Los codigos que no esten aca se deducen con ClipFile.detect_modes.
COMPOSITE_CODES = dict(enumerate(fusion.MODES))
SQLITE_MAGIC = b"SQLite format 3\x00"
MARKER = "BlockDataBegin".encode("utf-16-be")
APPDATA = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
# donde la deja Clip Studio Paint en Windows
DEFAULT_RECOVERY_DIR = APPDATA / "CELSYSUserData" / "CELSYS" / "CLIPStudioPaintData" / "RecoveryBackup"
CONFIG_FILE = APPDATA / "RescateCLIP" / "config.json"


# ----------------------------------------------------------------- lectura .clip

class ClipFile:
    """Un .clip abierto: base SQLite interna + chunks externos con los tiles."""

    def __init__(self, path):
        self.path = Path(path)
        self.mtime = self.path.stat().st_mtime
        self.data = self.path.read_bytes()
        i = self.data.find(SQLITE_MAGIC)
        if i == -1:
            raise ValueError("No parece un archivo .clip valido.")
        ps = int.from_bytes(self.data[i + 16:i + 18], "big")
        ps = 65536 if ps == 1 else ps
        pc = int.from_bytes(self.data[i + 28:i + 32], "big")
        db = self.data[i:i + ps * pc] if ps and pc else self.data[i:]
        # la ventana exporta desde otro hilo; nunca se usa en dos hilos a la vez
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.deserialize(db)
        self.cur = self.con.cursor()
        self.width, self.height = [int(v) for v in self.cur.execute(
            "SELECT CanvasWidth, CanvasHeight FROM Canvas").fetchone()]
        self.chunks = self._external_chunks()

    def _external_chunks(self):
        chunks, d = {}, self.data
        pos = d.find(b"CHNKExta")
        while pos != -1:
            il = int.from_bytes(d[pos + 16:pos + 24], "big")
            eid = bytes(d[pos + 24:pos + 24 + il]).decode("ascii", "ignore")
            dl = int.from_bytes(d[pos + 24 + il:pos + 32 + il], "big")
            chunks[eid] = (pos + 32 + il, dl)
            pos = d.find(b"CHNKExta", pos + 8)
        return chunks

    def preview(self):
        """Miniatura aplanada que guarda Clip Studio (o None)."""
        try:
            blob = self.cur.execute("SELECT ImageData FROM CanvasPreview").fetchone()[0]
        except (sqlite3.Error, TypeError):
            return None
        from io import BytesIO
        return Image.open(BytesIO(blob)).convert("RGB")

    def layer_tree(self):
        """Arbol de capas, cada nivel de abajo hacia arriba. Las carpetas tienen 'kids'."""
        cur = self.cur
        root = cur.execute("SELECT CanvasRootFolder FROM Canvas").fetchone()[0]
        info = {r[0]: r[1:] for r in cur.execute(
            "SELECT MainId, LayerName, LayerNextIndex, LayerVisibility, LayerOpacity, LayerRenderMipmap, "
            "LayerFirstChildIndex, LayerFolder, LayerComposite, LayerClip FROM Layer")}
        seen = set()

        def walk(mid):
            nodes = []
            while mid and mid in info and mid not in seen:
                seen.add(mid)
                name, nxt, vis, op, rmip, first, folder, comp, clip = info[mid]
                node = dict(id=mid, name=name, visible=bool((vis or 0) & 1), opacity=(op or 0) / 256.0,
                            comp=comp or 0, clip=bool(clip), folder=bool(folder))
                if folder:
                    node["kids"] = walk(first)
                else:
                    node["off"] = self._offscreen_of(rmip)
                nodes.append(node)
                mid = nxt
            return nodes

        return walk(info[root][5])

    def visible_layers(self, tree=None):
        """Capas con pixeles que se ven (dentro de carpetas visibles), de abajo hacia arriba."""
        out = []

        def rec(nodes):
            for n in nodes:
                if n["visible"]:
                    if n["folder"]:
                        rec(n["kids"])
                    elif n["off"]:
                        out.append(n)

        rec(tree if tree is not None else self.layer_tree())
        return out

    def blend_codes(self):
        """Codigos de fusion en uso por capas o carpetas visibles."""
        codes = set()

        def rec(nodes):
            for n in nodes:
                if n["visible"]:
                    codes.add(n["comp"])
                    if n["folder"]:
                        rec(n["kids"])

        rec(self.layer_tree())
        return codes

    def _offscreen_of(self, rmip):
        r = self.cur.execute("SELECT BaseMipmapInfo FROM Mipmap WHERE MainId=?", (rmip,)).fetchone()
        if not r:
            return None
        r = self.cur.execute("SELECT Offscreen FROM MipmapInfo WHERE MainId=?", (r[0],)).fetchone()
        return r[0] if r else None

    def decode_layer(self, off):
        """Pixeles de una capa a tamano de lienzo: (H, W, 4) RGBA o None si no hay raster."""
        row = self.cur.execute("SELECT Attribute, BlockData FROM Offscreen WHERE MainId=?", (off,)).fetchone()
        if not row:
            return None
        attr = bytes(row[0])
        p = attr.find("Parameter".encode("utf-16-be")) + 18
        ow = int.from_bytes(attr[p:p + 4], "big")
        oh = int.from_bytes(attr[p + 4:p + 8], "big")
        eid = bytes(row[1]).split(b"\x00")[0].decode("ascii", "ignore")
        if eid not in self.chunks:
            return None
        start, length = self.chunks[eid]
        blob = self.data[start:start + length]

        gw, gh = (ow + TILE - 1) // TILE, (oh + TILE - 1) // TILE
        full = np.zeros((gh * TILE, gw * TILE, 4), np.uint8)
        found = False
        o = blob.find(MARKER)
        while o != -1:
            after = o + len("BlockDataBeginChunk") * 2
            idx = int.from_bytes(blob[after:after + 4], "big")
            zs = after + 28
            if zs < len(blob) and blob[zs] == 0x78:
                raw = np.frombuffer(zlib.decompressobj().decompress(blob[zs:]), np.uint8)
                if len(raw) == TILE * TILE * 5:
                    # tile = plano de alfa (256x256) + color BGRX intercalado (256x256x4)
                    tile = np.empty((TILE, TILE, 4), np.uint8)
                    tile[:, :, :3] = raw[TILE * TILE:].reshape(TILE, TILE, 4)[:, :, 2::-1]
                    tile[:, :, 3] = raw[:TILE * TILE].reshape(TILE, TILE)
                    r, c = divmod(idx, gw)
                    if r < gh:
                        full[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE] = tile
                        found = True
            o = blob.find(MARKER, o + 1)
        if not found:
            return None
        H, W = self.height, self.width
        canv = np.zeros((H, W, 4), np.uint8)
        h, w = min(H, oh), min(W, ow)
        canv[:h, :w] = full[:h, :w]
        return canv

    def _pixels(self, off, size, cache):
        """Pixeles RGBA de una capa al tamano pedido (reduciendo con alfa premultiplicado)."""
        if cache is not None and off in cache:
            return cache[off]
        px = self.decode_layer(off)
        if px is not None and size != (self.width, self.height):
            px = np.asarray(Image.fromarray(px, "RGBA").convert("RGBa")
                            .resize(size, Image.BILINEAR).convert("RGBA"))
        if cache is not None:
            cache[off] = px
        return px

    def flatten(self, size=None, modes=None, progress=None, cache=None):
        """Compone las capas visibles. Devuelve (color premultiplicado, alfa) en 0..1.

        size: (ancho, alto) para componer reducido. modes: {codigo: modo} que se suma a
        COMPOSITE_CODES. cache: dict para reusar capas decodificadas entre llamadas."""
        size = tuple(size or (self.width, self.height))
        W, H = size
        tree = self.layer_tree()
        table = {**COMPOSITE_CODES, **(modes or {})}
        total = len(self.visible_layers(tree))
        done = [0]

        def mode_of(n):
            return table.get(n["comp"], "normal")

        def fuse(col, alpha, rgba_or_pair, op, mode, base=None):
            for y0 in range(0, H, STRIP):
                y1 = min(H, y0 + STRIP)
                if isinstance(rgba_or_pair, tuple):           # carpeta: (color premult., alfa)
                    sc, sa = rgba_or_pair[0][y0:y1], rgba_or_pair[1][y0:y1]
                    cs, a = sc / np.maximum(sa, fusion.EPS), sa * op
                else:
                    p = rgba_or_pair[y0:y1]
                    cs = p[:, :, :3].astype(np.float32) / 255
                    a = p[:, :, 3:4].astype(np.float32) * (op / 255)
                if base is not None:                          # recorte: solo donde hay capa base
                    a *= base[0][y0:y1, :, 3:4].astype(np.float32) * (base[1] / 255)
                fusion.composite(col[y0:y1], alpha[y0:y1], cs, a, mode)

        def draw(nodes, col, alpha):
            base = None          # (pixeles, opacidad) de la ultima capa sin recorte
            base_hidden = False
            for n in nodes:
                if n["clip"] and base_hidden:
                    continue     # si la base esta oculta, lo recortado tampoco se ve
                if not n["visible"]:
                    if not n["clip"]:
                        base, base_hidden = None, True
                    continue
                if n["folder"]:
                    if n["opacity"] < 1 or n["comp"] != 0:
                        # carpeta con opacidad o modo propio: se compone aislada
                        sub = (np.zeros((H, W, 3), np.float32), np.zeros((H, W, 1), np.float32))
                        draw(n["kids"], *sub)
                        fuse(col, alpha, sub, n["opacity"], mode_of(n))
                        del sub
                    else:
                        draw(n["kids"], col, alpha)
                    if not n["clip"]:
                        base, base_hidden = None, False
                    continue
                if progress:
                    progress(done[0], total, n["name"])
                done[0] += 1
                px = self._pixels(n["off"], size, cache) if n["off"] else None
                if px is None:
                    if not n["clip"]:
                        base, base_hidden = None, False
                    continue
                fuse(col, alpha, px, n["opacity"], mode_of(n), base if n["clip"] else None)
                if not n["clip"]:
                    base, base_hidden = (px, n["opacity"]), False

        col = np.zeros((H, W, 3), np.float32)
        alpha = np.zeros((H, W, 1), np.float32)
        draw(tree, col, alpha)
        if progress:
            progress(total, total, "")
        return col, alpha

    def render(self, transparent=False, progress=None, modes=None):
        """Aplana las capas visibles a resolucion completa. Devuelve una imagen PIL."""
        col, alpha = self.flatten(modes=modes, progress=progress)
        if transparent:
            out = np.dstack([col / np.maximum(alpha, fusion.EPS), alpha]) * 255
            return Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8), "RGBA")
        out = (col + (1 - alpha)) * 255          # fondo papel blanco
        return Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8), "RGB")

    def detect_modes(self, codes=None, progress=None, report=None):
        """Deduce el modo de fusion de codigos desconocidos comparando contra la miniatura.

        Para cada codigo prueba todos los modos sobre la zona donde lo usan las capas y se
        queda con el que mejor reproduce la miniatura de Clip Studio. Devuelve {codigo: modo}."""
        prev = self.preview()
        if prev is None:
            return {}
        tree = self.layer_tree()
        if codes is None:
            codes = self.blend_codes() - set(COMPOSITE_CODES)
        target = np.asarray(prev, np.float32) / 255
        size = prev.size
        cache = {}
        self.flatten(size=size, cache=cache)          # llena el cache a baja resolucion
        found = {}
        for code in sorted(codes):
            zone = np.zeros(target.shape[:2], bool)
            for n in self.visible_layers(tree):
                px = cache.get(n["off"])
                if n["comp"] == code and px is not None:
                    zone |= px[:, :, 3] > 8
            if zone.sum() < 50:
                continue                               # capa vacia: no hay como saberlo
            errs = {}
            for name in fusion.MODES:
                if progress:
                    progress("Probando %s para el código %d..." % (fusion.MODES[name][0], code))
                col, alpha = self.flatten(size=size, modes={**found, code: name}, cache=cache)
                img = col + (1 - alpha)
                errs[name] = float(np.abs(img - target)[zone].mean() * 255)
            best = min(errs, key=errs.get)
            if report:
                report(code, errs)
            if errs[best] < errs["normal"] * 0.9 and errs[best] < errs["normal"] - 0.1:   # mejora clara
                found[code] = best
        return found

    def close(self):
        self.con.close()


def similarity(img, preview):
    """Diferencia media (0-255) entre el render reducido y la miniatura de Clip Studio."""
    if preview is None:
        return None
    small = np.asarray(img.convert("RGB").resize(preview.size, Image.BILINEAR), np.float32)
    return float(np.abs(small - np.asarray(preview, np.float32)).mean())


def load_config():
    try:
        import json
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    import json
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def find_recovery_dir():
    """Carpeta de autoguardados: la elegida a mano, la de siempre, o buscada en las carpetas de CELSYS."""
    chosen = load_config().get("recovery_dir")
    if chosen and Path(chosen).is_dir():
        return Path(chosen)
    if DEFAULT_RECOVERY_DIR.is_dir():
        return DEFAULT_RECOVERY_DIR
    # otras versiones de Clip Studio cambian el nombre de las subcarpetas; buscar solo dentro de CELSYS
    roots = [APPDATA / "CELSYSUserData", APPDATA / "CELSYS",
             Path(os.environ.get("LOCALAPPDATA", "")) / "CELSYS",
             Path.home() / "Documents" / "CELSYS"]
    found = []
    for root in roots:
        if root.is_dir():
            found += [p for p in root.rglob("RecoveryBackup") if p.is_dir()]
    if found:
        return max(found, key=lambda p: p.stat().st_mtime)
    return None


def find_backups(folder):
    if not folder or not Path(folder).is_dir():
        return []
    files = [p for p in Path(folder).glob("*.clip") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def resolve_modes(clip, progress=None):
    """Modos aprendidos antes + deduce (y recuerda) los codigos de fusion desconocidos."""
    cfg = load_config()
    learned = {int(k): v for k, v in cfg.get("modos", {}).items() if v in fusion.MODES}
    unknown = clip.blend_codes() - set(COMPOSITE_CODES) - set(learned)
    if unknown:
        found = clip.detect_modes(codes=unknown, progress=progress)
        if found:
            learned.update(found)
            cfg["modos"] = {str(k): v for k, v in learned.items()}
            save_config(cfg)
    return learned


def age_text(path):
    mins = (datetime.now().timestamp() - Path(path).stat().st_mtime) / 60
    if mins < 60:
        return "hace %d min" % max(mins, 0)
    if mins < 48 * 60:
        return "hace %d h" % (mins / 60)
    return "hace %d días" % (mins / 1440)


# ----------------------------------------------------------------- consola

def cli(src, dst=None):
    print("Rescate CLIP %s - sin garantias, el autor no se hace responsable por su uso "
          "(ver LICENSE). Hecho con IA." % __version__)
    clip = ClipFile(src)
    dst = Path(dst) if dst else Path(src).with_suffix(".png")
    print("Lienzo %dx%d  (archivo guardado %s)" % (clip.width, clip.height, age_text(src)))
    modes = resolve_modes(clip, progress=print)
    img = clip.render(modes=modes, progress=lambda i, n, name: name and print("  [%d/%d] %s" % (i + 1, n, name)))
    img.save(dst)
    d = similarity(img, clip.preview())
    print("Guardado: %s  (%dx%d)" % (dst, img.width, img.height))
    if d is not None:
        print("Diferencia con la miniatura de Clip Studio: %.2f / 255" % d)
    clip.close()


# ----------------------------------------------------------------- ventana

def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import ImageTk

    root = tk.Tk()
    root.title("Rescate CLIP %s" % __version__)
    try:
        root.iconbitmap(str(Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "icono.ico"))
    except tk.TclError:
        pass
    root.geometry("860x560")
    root.minsize(720, 480)

    state = {"files": [], "clip": None, "thumb": None, "busy": False, "dir": None}

    left = ttk.Frame(root, padding=10)
    left.pack(side="left", fill="y")
    ttk.Label(left, text="Autoguardados de Clip Studio", font=("Segoe UI", 10, "bold")).pack(anchor="w")
    dir_label = ttk.Label(left, text="", font=("Segoe UI", 8), foreground="#666", wraplength=270)
    dir_label.pack(anchor="w", pady=(2, 0))
    lst = tk.Listbox(left, width=38, height=16, activestyle="none", font=("Segoe UI", 9))
    lst.pack(fill="y", expand=True, pady=(6, 6))
    ttk.Button(left, text="Abrir otro .clip...", command=lambda: open_other()).pack(fill="x")
    ttk.Button(left, text="Elegir carpeta de autoguardados...", command=lambda: choose_dir()).pack(fill="x", pady=(4, 0))
    ttk.Button(left, text="Actualizar lista", command=lambda: refresh()).pack(fill="x", pady=(4, 0))
    ttk.Label(left, text="Consejo: en las Preferencias de Clip Studio (sección Archivo) bajá el "
                         "intervalo de los datos de recuperación, así estos autoguardados están "
                         "siempre al día.",
              font=("Segoe UI", 8), foreground="#666", wraplength=270).pack(anchor="w", pady=(8, 0))
    ttk.Label(left, text="Hecho con IA (Claude, de Anthropic). Sin garantías.",
              font=("Segoe UI", 8), foreground="#999").pack(anchor="w", pady=(6, 0))
    ttk.Button(left, text="Aviso legal", command=lambda: messagebox.showinfo(
        "Rescate CLIP - Aviso legal", DISCLAIMER)).pack(fill="x", pady=(4, 0))

    right = ttk.Frame(root, padding=10)
    right.pack(side="left", fill="both", expand=True)
    canvas = tk.Label(right, text="Elegí un archivo", anchor="center", background="#2b2b2b", foreground="#bbb")
    canvas.pack(fill="both", expand=True)
    info = ttk.Label(right, text="", font=("Segoe UI", 9))
    info.pack(anchor="w", pady=(8, 4))

    bottom = ttk.Frame(right)
    bottom.pack(fill="x")
    transp = tk.BooleanVar(value=False)
    ttk.Checkbutton(bottom, text="Fondo transparente", variable=transp).pack(side="left")
    export_btn = ttk.Button(bottom, text="Exportar PNG a resolución completa...", command=lambda: export())
    export_btn.pack(side="right")
    bar = ttk.Progressbar(right, mode="determinate")
    bar.pack(fill="x", pady=(8, 0))
    status = ttk.Label(right, text="", font=("Segoe UI", 9))
    status.pack(anchor="w")

    def refresh():
        if not (state["dir"] and state["dir"].is_dir()):
            state["dir"] = find_recovery_dir()
        lst.delete(0, "end")
        if state["dir"] is None:
            state["files"] = []
            dir_label.configure(text="Carpeta: no encontrada")
            lst.insert("end", "(no se encontró la carpeta)")
            messagebox.showerror(
                "Rescate CLIP",
                "No encontré la carpeta de autoguardados de Clip Studio.\n\n"
                "Normalmente está en:\n%s\n\n"
                "Puede no existir si Clip Studio todavía no hizo ningún autoguardado, "
                "o si tu versión la guarda en otro lado.\n\n"
                "Usá «Elegir carpeta de autoguardados...» para indicarla a mano, "
                "o «Abrir otro .clip...» para abrir un archivo suelto." % DEFAULT_RECOVERY_DIR)
            return
        dir_label.configure(text="Carpeta: %s" % state["dir"])
        state["files"] = find_backups(state["dir"])
        for p in state["files"]:
            st = p.stat()
            lst.insert("end", "%s   %5.1f MB" % (
                datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"), st.st_size / 1e6))
        if not state["files"]:
            lst.insert("end", "(la carpeta no tiene archivos .clip)")
        else:
            lst.selection_set(0)
            load(state["files"][0])

    def load(path):
        if state["busy"]:
            return
        try:
            if state["clip"]:
                state["clip"].close()
            clip = ClipFile(path)
        except Exception as e:
            messagebox.showerror("Rescate CLIP", "No se pudo leer el archivo:\n%s" % e)
            return
        state["clip"] = clip
        prev = clip.preview()
        if prev:
            box_w, box_h = max(canvas.winfo_width(), 300), max(canvas.winfo_height(), 300)
            t = prev.copy()
            t.thumbnail((box_w - 10, box_h - 10))
            state["thumb"] = ImageTk.PhotoImage(t)
            canvas.configure(image=state["thumb"], text="")
        else:
            canvas.configure(image="", text="(sin vista previa)")
        n = len(clip.visible_layers())
        info.configure(text="%s\nLienzo %d × %d px  ·  %d capas visibles  ·  guardado %s" % (
            Path(path).name, clip.width, clip.height, n, age_text(path)))
        status.configure(text="")
        bar["value"] = 0

    def on_select(_e):
        sel = lst.curselection()
        if sel and sel[0] < len(state["files"]):
            load(state["files"][sel[0]])

    def choose_dir():
        start = state["dir"] or (DEFAULT_RECOVERY_DIR if DEFAULT_RECOVERY_DIR.is_dir() else APPDATA)
        p = filedialog.askdirectory(title="Carpeta de autoguardados de Clip Studio", initialdir=str(start))
        if not p:
            return
        p = Path(p)
        if not find_backups(p) and not messagebox.askyesno(
                "Rescate CLIP", "Esa carpeta no tiene archivos .clip.\n\n%s\n\n¿Usarla igual?" % p):
            return
        cfg = load_config()
        cfg["recovery_dir"] = str(p)
        save_config(cfg)
        state["dir"] = p
        refresh()

    def open_other():
        p = filedialog.askopenfilename(title="Abrir .clip", filetypes=[("Clip Studio", "*.clip"), ("Todos", "*.*")])
        if p:
            lst.selection_clear(0, "end")
            load(p)

    def export():
        clip = state["clip"]
        if not clip or state["busy"]:
            return
        dst = filedialog.asksaveasfilename(
            title="Guardar PNG", defaultextension=".png", filetypes=[("PNG", "*.png")],
            initialdir=str(Path.home() / "Desktop"), initialfile="dibujo_%dx%d.png" % (clip.width, clip.height))
        if not dst:
            return
        state["busy"] = True
        export_btn.state(["disabled"])

        def progress(i, n, name):
            root.after(0, lambda: (bar.configure(maximum=n, value=i),
                                   status.configure(text="Componiendo %s..." % name if name else "Guardando...")))

        want_transparent = transp.get()

        def work():
            clip = state["clip"]
            try:
                if clip.path.stat().st_mtime != clip.mtime:   # Clip Studio lo reescribio
                    root.after(0, lambda: status.configure(text="El archivo cambió, releyendo..."))
                    clip.close()
                    clip = state["clip"] = ClipFile(clip.path)
                modes = resolve_modes(clip, progress=lambda text: root.after(0, lambda: status.configure(text=text)))
                img = clip.render(transparent=want_transparent, progress=progress, modes=modes)
                img.save(dst)
                d = similarity(img, clip.preview())
                msg = "Listo: %s (%d × %d)" % (Path(dst).name, img.width, img.height)
                if d is not None:
                    msg += "  ·  diferencia con la miniatura: %.1f/255" % d
                root.after(0, lambda: done(msg, dst))
            except Exception as e:
                err = "Error: %s" % e
                root.after(0, lambda: done(err, None))

        threading.Thread(target=work, daemon=True).start()

    def done(msg, dst):
        state["busy"] = False
        export_btn.state(["!disabled"])
        status.configure(text=msg)
        if dst and messagebox.askyesno("Rescate CLIP", msg + "\n\n¿Abrir la carpeta?"):
            os.startfile(str(Path(dst).parent))

    def start():
        cfg = load_config()
        if not cfg.get("aviso_aceptado"):
            if not messagebox.askokcancel(
                    "Rescate CLIP - Aviso legal", DISCLAIMER + "\n\n¿Aceptás estas condiciones?"):
                root.destroy()
                return
            cfg["aviso_aceptado"] = True
            save_config(cfg)
        refresh()

    lst.bind("<<ListboxSelect>>", on_select)
    root.after(150, start)
    root.mainloop()


if __name__ == "__main__":
    if sys.stdout is None:          # .exe sin consola
        sys.stdout = sys.stderr = open(os.devnull, "w")
    if len(sys.argv) > 1:
        cli(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        gui()

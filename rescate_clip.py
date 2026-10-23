# -*- coding: utf-8 -*-
"""
Rescate CLIP  -  Exporta a PNG, a resolucion completa, los dibujos guardados en
archivos .clip de Clip Studio Paint (incluidos sus autoguardados de recuperacion),
leyendo el archivo directamente, sin abrir Clip Studio.

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

__version__ = "1.2"

TILE = 256
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

    def visible_layers(self):
        """[(nombre, opacidad 0..1, offscreen_id)] de abajo hacia arriba."""
        cur = self.cur
        root = cur.execute("SELECT CanvasRootFolder FROM Canvas").fetchone()[0]
        info = {r[0]: r[1:] for r in cur.execute(
            "SELECT MainId, LayerName, LayerNextIndex, LayerVisibility, LayerOpacity, "
            "LayerRenderMipmap, LayerFirstChildIndex, LayerFolder FROM Layer")}
        out = []

        def walk(mid):
            while mid and mid in info:
                name, nxt, vis, op, rmip, first, folder = info[mid]
                if vis & 1:
                    if folder and first:
                        walk(first)
                    else:
                        off = self._offscreen_of(rmip)
                        if off:
                            out.append((name, (op or 0) / 256.0, off))
                mid = nxt

        walk(info[root][5])
        return out

    def _offscreen_of(self, rmip):
        r = self.cur.execute("SELECT BaseMipmapInfo FROM Mipmap WHERE MainId=?", (rmip,)).fetchone()
        if not r:
            return None
        r = self.cur.execute("SELECT Offscreen FROM MipmapInfo WHERE MainId=?", (r[0],)).fetchone()
        return r[0] if r else None

    def decode_layer(self, off):
        """Pixeles de una capa a tamano de lienzo: (H, W, 4) BGRA o None si no hay raster."""
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
                    tile = raw[TILE * TILE:].reshape(TILE, TILE, 4).copy()
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

    def render(self, transparent=False, progress=None):
        """Aplana las capas visibles a resolucion completa. Devuelve una imagen PIL."""
        layers = self.visible_layers()
        H, W = self.height, self.width
        col = np.zeros((H, W, 3), np.float32)    # color premultiplicado
        alpha = np.zeros((H, W, 1), np.float32)
        for n, (name, op, off) in enumerate(layers):
            if progress:
                progress(n, len(layers), name)
            px = self.decode_layer(off)
            if px is None:
                continue
            a = px[:, :, 3:4].astype(np.float32) * (op / 255.0)
            rgb = px[:, :, 2::-1].astype(np.float32)
            col = rgb * a + col * (1 - a)
            alpha = a + alpha * (1 - a)
            del px, a, rgb
        if progress:
            progress(len(layers), len(layers), "")
        if transparent:
            safe = np.where(alpha > 0, alpha, 1)
            out = np.dstack([col / safe, alpha * 255])
            return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")
        out = col + 255.0 * (1 - alpha)          # fondo papel blanco
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")

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


# ----------------------------------------------------------------- consola

def cli(src, dst=None):
    clip = ClipFile(src)
    dst = Path(dst) if dst else Path(src).with_suffix(".png")
    print("Lienzo %dx%d" % (clip.width, clip.height))
    img = clip.render(progress=lambda i, n, name: name and print("  [%d/%d] %s" % (i + 1, n, name)))
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
        info.configure(text="%s\nLienzo %d × %d px  ·  %d capas visibles" % (
            Path(path).name, clip.width, clip.height, n))
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
                img = clip.render(transparent=want_transparent, progress=progress)
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

    lst.bind("<<ListboxSelect>>", on_select)
    root.after(150, refresh)
    root.mainloop()


if __name__ == "__main__":
    if sys.stdout is None:          # .exe sin consola
        sys.stdout = sys.stderr = open(os.devnull, "w")
    if len(sys.argv) > 1:
        cli(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        gui()

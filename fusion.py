# -*- coding: utf-8 -*-
"""
Modos de fusion de Clip Studio Paint.

Colores en 0..1. Cada modo recibe el color de abajo (b) y el de la capa (s), ambos sin
premultiplicar, con forma (..., 3), y devuelve el color fusionado. Son las formulas
estandar (las de Photoshop / W3C); los modos "brillo" de Clip Studio (Sobreexponer
(brillo) y Anadir (brillo)) se aproximan con su version comun.

Parte de Rescate CLIP (c) Joky - https://github.com/lightynti/rescate-clip
Licencia MIT con atribucion; sin garantias (ver LICENSE).
"""
import numpy as np

EPS = 1e-6


def _lum(c):
    return c[..., 0:1] * 0.3 + c[..., 1:2] * 0.59 + c[..., 2:3] * 0.11


def _clip_color(c):
    l = _lum(c)
    n = c.min(-1, keepdims=True)
    x = c.max(-1, keepdims=True)
    c = np.where(n < 0, l + (c - l) * l / np.maximum(l - n, EPS), c)
    return np.where(x > 1, l + (c - l) * (1 - l) / np.maximum(x - l, EPS), c)


def _set_lum(c, l):
    return _clip_color(c + (l - _lum(c)))


def _sat(c):
    return c.max(-1, keepdims=True) - c.min(-1, keepdims=True)


def _set_sat(c, s):
    mn = c.min(-1, keepdims=True)
    rng = c.max(-1, keepdims=True) - mn
    return np.where(rng > EPS, (c - mn) * s / np.maximum(rng, EPS), 0.0)


def _burn(b, s):
    return np.where(b >= 1, 1.0, np.where(s <= 0, 0.0, 1 - np.minimum(1, (1 - b) / np.maximum(s, EPS))))


def _dodge(b, s):
    return np.where(b <= 0, 0.0, np.where(s >= 1, 1.0, np.minimum(1, b / np.maximum(1 - s, EPS))))


def _soft_light(b, s):
    d = np.where(b <= 0.25, ((16 * b - 12) * b + 4) * b, np.sqrt(b))
    return np.where(s <= 0.5, b - (1 - 2 * s) * b * (1 - b), b + (2 * s - 1) * (d - b))


def _hard_light(b, s):
    return np.where(s <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s))


# nombre interno -> (nombre en Clip Studio, funcion)
MODES = {
    "normal": ("Normal", lambda b, s: s),
    "darken": ("Oscurecer", np.minimum),
    "multiply": ("Multiplicar", lambda b, s: b * s),
    "color_burn": ("Subexponer color", _burn),
    "linear_burn": ("Subexposición lineal", lambda b, s: np.maximum(0, b + s - 1)),
    "subtract": ("Sustraer", lambda b, s: np.maximum(0, b - s)),
    "darker_color": ("Color más oscuro", lambda b, s: np.where(_lum(s) < _lum(b), s, b)),
    "lighten": ("Aclarar", np.maximum),
    "screen": ("Trama", lambda b, s: b + s - b * s),
    "color_dodge": ("Sobreexponer color", _dodge),
    "glow_dodge": ("Sobreexponer (brillo)", _dodge),
    "add": ("Añadir", lambda b, s: np.minimum(1, b + s)),
    "add_glow": ("Añadir (brillo)", lambda b, s: np.minimum(1, b + s)),
    "lighter_color": ("Color más claro", lambda b, s: np.where(_lum(s) > _lum(b), s, b)),
    "overlay": ("Superponer", lambda b, s: _hard_light(s, b)),
    "soft_light": ("Luz suave", _soft_light),
    "hard_light": ("Luz fuerte", _hard_light),
    "difference": ("Diferencia", lambda b, s: np.abs(b - s)),
    "vivid_light": ("Luz intensa", lambda b, s: np.where(s <= 0.5, _burn(b, 2 * s), _dodge(b, 2 * s - 1))),
    "linear_light": ("Luz lineal", lambda b, s: np.clip(b + 2 * s - 1, 0, 1)),
    "pin_light": ("Luz focal", lambda b, s: np.where(s <= 0.5, np.minimum(b, 2 * s), np.maximum(b, 2 * s - 1))),
    "hard_mix": ("Mezcla definida", lambda b, s: (b + s >= 1).astype(b.dtype)),
    "exclusion": ("Exclusión", lambda b, s: b + s - 2 * b * s),
    "hue": ("Tono", lambda b, s: _set_lum(_set_sat(s, _sat(b)), _lum(b))),
    "saturation": ("Saturación", lambda b, s: _set_lum(_set_sat(b, _sat(s)), _lum(b))),
    "color": ("Color", lambda b, s: _set_lum(s, _lum(b))),
    "luminosity": ("Luminosidad", lambda b, s: _set_lum(b, _lum(s))),
    "divide": ("Dividir", lambda b, s: np.where(s <= 0, np.where(b > 0, 1.0, 0.0), np.minimum(1, b / np.maximum(s, EPS)))),
}


def composite(col, alpha, cs, a, mode):
    """Funde la capa (color cs sin premultiplicar, alfa a) sobre col (premultiplicado) y alpha, en el lugar."""
    if mode == "normal":
        col *= 1 - a
        col += cs * a
    else:
        cb = col / np.maximum(alpha, EPS)
        fused = np.clip(MODES[mode][1](cb, cs), 0, 1)
        # compuesto W3C: (1-ab)*as*cs + (1-as)*ab*cb + as*ab*B(cb,cs)
        col *= 1 - a
        col += a * ((1 - alpha) * cs + alpha * fused)
    alpha *= 1 - a
    alpha += a

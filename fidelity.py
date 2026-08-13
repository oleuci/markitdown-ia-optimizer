#!/usr/bin/env python3
"""
Contrôle de fidélité factuelle (déterministe, local — aucun LLM).
Compare les VALEURS NUMÉRIQUES (montants, %, années, quantités, dates chiffrées)
présentes dans le document source et dans la version optimisée.

- « missing » : valeurs du source absentes de la sortie (information perdue).
- « added »   : valeurs de la sortie absentes du source (ajout / hallucination possible).

Ne prouve pas la fidélité sémantique complète, mais attrape le défaut le plus grave
et le plus fréquent sur des documents factuels : la perte ou l'altération de chiffres.
"""
import re
from collections import Counter

# nombres avec séparateurs de milliers (espace/point/virgule) et/ou décimales
_NUM = re.compile(
    r"\d{1,3}(?:[   .,]\d{3})+(?:[.,]\d+)?"  # 45 000 / 1.234.567 / 12,5
    r"|\d+(?:[.,]\d+)?"                                  # 320 / 3,5 / 12
)
# marqueurs de liste / titres numérotés en début de ligne (structurels, non factuels)
_MARKER = re.compile(r"^(\s*)(?:#{1,6}\s*)?\d+[.)]\s+", re.M)


def _strip_markers(text: str) -> str:
    return _MARKER.sub(r"\1", text or "")


def _canon(tok: str):
    t = re.sub(r"[\s  ]", "", tok)
    frac = ""
    m = re.search(r"[.,](\d+)$", t)
    if m and len(m.group(1)) != 3:      # dernier groupe ≠ 3 chiffres -> décimale
        frac = m.group(1)
        t = t[:m.start()]
    digits = re.sub(r"\D", "", t)
    if not digits:
        return None
    digits = digits.lstrip("0") or "0"   # normalise les zéros de tête
    return digits + ("." + frac if frac else "")


def _numbers(text: str):
    text = _strip_markers(text)
    out = []
    for m in _NUM.finditer(text):
        c = _canon(m.group(0))
        if c:
            out.append(c)
    return out


def check(source: str, output: str) -> dict:
    src = set(_numbers(source))
    out = set(_numbers(output))
    missing = sorted(src - out, key=lambda x: (-len(x), x))
    added = sorted(out - src, key=lambda x: (-len(x), x))
    total = len(src)
    return {
        "total": total,
        "kept": total - len(missing),
        "missing": missing[:12],
        "added": added[:12],
    }

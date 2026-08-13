#!/usr/bin/env python3
"""
Nettoyage heuristique du Markdown (Niveau 1) — sans perte de sens.
Réduit les tokens en retirant le « cruft » d'extraction (surtout PDF) :
césures de fin de ligne, numéros de page, espaces/lignes superflus.
Ne touche pas au contenu des blocs de code (``` … ```).
"""
import re

_PAGE_NUM = re.compile(r"^\s*\d{1,4}\s*$")
_PAGE_LABEL = re.compile(r"^\s*(?:page|p\.)\s*\d+(?:\s*(?:/|sur|of)\s*\d+)?\s*$", re.I)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MULTISPACE = re.compile(r"[ \t]{2,}")
_DEHYPHEN = re.compile(r"([a-zàâäéèêëïîôöùûüç])-\n([a-zàâäéèêëïîôöùûüç])")


def _split_code(text):
    """Découpe en segments (is_code, contenu) pour préserver les blocs ```."""
    parts, buf, in_code = [], [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            buf.append(line)
            parts.append((in_code, "\n".join(buf)))
            buf = []
            in_code = not in_code
            continue
        buf.append(line)
    if buf:
        parts.append((in_code, "\n".join(buf)))
    return parts


def clean_markdown(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ").replace("​", "")
    text = _CTRL.sub("", text)

    out_segments = []
    for is_code, seg in _split_code(text):
        if is_code:
            out_segments.append(seg)  # bloc de code : intact
            continue
        # recoller les mots coupés en fin de ligne (césure PDF)
        seg = _DEHYPHEN.sub(r"\1\2", seg)
        lines = []
        for ln in seg.split("\n"):
            ln = ln.rstrip()
            if _PAGE_NUM.match(ln) or _PAGE_LABEL.match(ln):
                continue  # ligne = simple numéro/label de page
            if "|" not in ln:  # ne pas déformer les tableaux Markdown
                ln = _MULTISPACE.sub(" ", ln)
            lines.append(ln)
        out_segments.append("\n".join(lines))

    text = "\n".join(out_segments)
    # supprimer les en-têtes/pieds répétés à l'identique (bruit fréquent)
    text = _drop_repeated_lines(text)
    # compacter 3+ lignes vides -> 1 vide
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _drop_repeated_lines(text: str, min_len=8, min_count=4) -> str:
    """Retire les lignes non triviales répétées de nombreuses fois (en-têtes/pieds)."""
    from collections import Counter
    lines = text.split("\n")
    counts = Counter(l.strip() for l in lines if len(l.strip()) >= min_len)
    noisy = {l for l, c in counts.items()
             if c >= min_count and not l.startswith(("#", "-", "*", ">", "|"))}
    if not noisy:
        return text
    return "\n".join(l for l in lines if l.strip() not in noisy)

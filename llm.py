#!/usr/bin/env python3
"""
Client LLM (API OpenAI-compatible /v1/chat/completions) pour les niveaux
d'optimisation, et comptage de tokens (tiktoken sinon estimation).
Cible par défaut : OVH AI Endpoints (rapide, GPU). Compatible aussi avec un
Ollama local (même format /v1), en changeant LLM_BASE / LLM_MODEL / LLM_API_KEY.
"""
import os
import re
import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import fidelity

LLM_BASE = os.environ.get("LLM_BASE", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "Meta-Llama-3_3-70B-Instruct")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))
LLM_ENABLED = os.environ.get("LLM_ENABLED", "1") == "1"
# Contexte large côté OVH (128k) ; on plafonne pour la maîtrise des coûts.
LLM_MAX_INPUT = int(os.environ.get("LLM_MAX_INPUT", "30000"))
# Parallélisme des appels lors du compactage découpé (gentil avec les quotas OVH).
LLM_MAX_WORKERS = int(os.environ.get("LLM_MAX_WORKERS", "5"))

# ---- comptage de tokens -------------------------------------------------
_ENC = None
def _enc():
    global _ENC
    if _ENC is None:
        try:
            import tiktoken
            _ENC = tiktoken.get_encoding("o200k_base")
        except Exception:
            _ENC = False
    return _ENC


def count_tokens(text: str) -> int:
    e = _enc()
    if e:
        try:
            return len(e.encode(text or ""))
        except Exception:
            pass
    return max(1, round(len(text or "") / 4))


# ---- niveaux d'optimisation --------------------------------------------
LEVELS = {
    2: {
        "name": "Compactage",
        "loss": "faible",
        "system": (
            "Tu es un outil de compactage de documents. Réécris le document Markdown en supprimant "
            "UNIQUEMENT les redondances, répétitions et formulations verbeuses (mots de remplissage, "
            "tournures alambiquées, répétitions d'idées). Conserve la structure (titres, listes, tableaux) "
            "et TOUTES les informations. Ne résume PAS et ne retire AUCUN élément de contenu — le texte "
            "doit rester quasi complet, seulement dégraissé. "
            "RÈGLE ABSOLUE : conserve CHAQUE nombre, montant, pourcentage, date et nom propre du texte, "
            "sans exception et sans arrondir ; en cas de doute, garde la phrase entière. "
            "Le résultat doit faire AU MOINS 75 % de la longueur d'origine : si tu descends en dessous, "
            "c'est que tu as résumé — ne le fais pas. "
            "Réponds UNIQUEMENT avec le Markdown, sans commentaire."
        ),
        "predict_ratio": 0.95, "predict_min": 300, "est_ratio": 0.80,
    },
    3: {
        "name": "Condensé",
        "loss": "modérée",
        "system": (
            "Tu es un outil de condensation. Produis une version d'environ la MOITIÉ de la longueur "
            "d'origine (ne descends pas nettement en dessous de 50 %). Conserve tous les points "
            "importants, chiffres et faits clés, organisés en sections avec titres Markdown ; "
            "supprime les détails secondaires, exemples et développements superflus. "
            "Réponds UNIQUEMENT avec le Markdown, sans commentaire."
        ),
        "predict_ratio": 0.55, "predict_min": 300, "est_ratio": 0.50,
    },
    4: {
        "name": "Synthèse",
        "loss": "forte",
        "system": (
            "Tu es un outil de synthèse exécutive. On te donne un document en Markdown. Produis une "
            "synthèse brève en puces des idées essentielles uniquement (5 à 15 puces), en conservant "
            "les chiffres clés. Réponds UNIQUEMENT avec le Markdown, sans commentaire."
        ),
        "predict_ratio": 0.28, "predict_min": 200, "est_ratio": 0.22,
    },
}


def _chat(system: str, user: str, max_tokens: int, model: str = None):
    """Appel LLM générique. Retourne (ok, texte_ou_erreur, meta)."""
    model = model or LLM_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False, "temperature": 0.1, "max_tokens": max_tokens,
    }
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = "Bearer " + LLM_API_KEY
    req = urllib.request.Request(LLM_BASE + "/chat/completions",
                                 data=json.dumps(payload).encode(), headers=headers)
    t = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=LLM_TIMEOUT)
        d = json.load(r)
    except urllib.error.HTTPError as e:
        return False, f"moteur: HTTP {e.code}", {}
    except urllib.error.URLError as e:
        return False, f"moteur injoignable ({getattr(e, 'reason', e)})", {}
    except TimeoutError:
        return False, "délai dépassé", {}
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", {}
    try:
        msg = d["choices"][0]["message"]["content"].strip()
    except Exception:
        return False, "réponse illisible du moteur", {}
    if not msg:
        return False, "réponse vide du modèle", {}
    return True, msg, {"seconds": round(time.time() - t, 1), "model": model}


def optimize(markdown: str, level: int, model: str = None):
    """Retourne (ok, texte_ou_erreur, meta)."""
    if not LLM_ENABLED:
        return False, "moteur LLM désactivé", {}
    if level not in LEVELS:
        return False, "niveau inconnu", {}
    in_tok = count_tokens(markdown)
    if in_tok > LLM_MAX_INPUT:
        return False, f"document trop long (~{LLM_MAX_INPUT} tokens max)", {}
    spec = LEVELS[level]
    max_tokens = min(8000, max(spec["predict_min"], int(in_tok * spec["predict_ratio"]) + 128))
    return _chat(spec["system"], markdown, max_tokens, model)


def repair(source: str, current: str, missing: list, model: str = None):
    """Réintègre dans `current` les valeurs chiffrées `missing` perdues, en s'appuyant sur `source`."""
    system = (
        "Tu corriges un texte Markdown compacté qui devait conserver TOUS les chiffres du document "
        "source mais qui en a perdu. Réintègre les valeurs manquantes indiquées, à leur place logique "
        "et avec leur contexte, en t'appuyant sur le document source. Ne retire rien d'autre, ne résume "
        "pas davantage, ne modifie aucune autre valeur. Réponds UNIQUEMENT avec le Markdown corrigé."
    )
    user = ("DOCUMENT SOURCE :\n" + source + "\n\nTEXTE COMPACTÉ À CORRIGER :\n" + current +
            "\n\nVALEURS À RÉINTÉGRER (elles figurent dans le source) : " + ", ".join(missing))
    max_tokens = min(8000, max(400, int(count_tokens(current) * 1.5) + 200))
    return _chat(system, user, max_tokens, model)


def _split_oversized(text: str, target: int):
    """Découpe un bloc trop gros par phrases (puis par mots si une phrase est énorme)."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    out, cur, cur_tok = [], [], 0
    for p in parts:
        p = p.strip()
        if not p:
            continue
        pt = count_tokens(p)
        if pt > target:  # phrase gigantesque -> découpe brute par mots
            if cur:
                out.append(" ".join(cur)); cur, cur_tok = [], 0
            words, wcur, wtok = p.split(" "), [], 0
            for w in words:
                wt = count_tokens(w + " ")
                if wcur and wtok + wt > target:
                    out.append(" ".join(wcur)); wcur, wtok = [], 0
                wcur.append(w); wtok += wt
            if wcur:
                out.append(" ".join(wcur))
            continue
        if cur and cur_tok + pt > target:
            out.append(" ".join(cur)); cur, cur_tok = [], 0
        cur.append(p); cur_tok += pt
    if cur:
        out.append(" ".join(cur))
    return out


def _split_chunks(markdown: str, target: int = 500):
    """Découpe le Markdown en morceaux <= ~target tokens. Robuste même sans lignes vides
    (extractions PDF « en bloc ») : re-découpe les blocs trop gros par phrases."""
    # 1) unités = paragraphes (lignes vides), les gros paragraphes re-découpés par phrases
    units = []
    for b in re.split(r"\n\s*\n", markdown.strip()):
        b = b.strip()
        if not b:
            continue
        if count_tokens(b) <= target:
            units.append(b)
        else:
            units.extend(_split_oversized(b, target))
    # 2) regroupement des unités en morceaux <= target (nouveau morceau sur un titre)
    chunks, cur, cur_tok = [], [], 0
    for u in units:
        ut = count_tokens(u)
        is_heading = u.lstrip().startswith("#")
        if cur and (cur_tok + ut > target or (is_heading and cur_tok > target * 0.5)):
            chunks.append("\n\n".join(cur))
            cur, cur_tok = [], 0
        cur.append(u)
        cur_tok += ut
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks or [markdown]


def compact_chunked(markdown: str, model: str = None):
    """Compactage section par section : sur un petit morceau, le modèle dégraisse au lieu de résumer.
    Donne un taux régulier et proche du texte complet."""
    chunks = _split_chunks(markdown, target=500)
    spec = LEVELS[2]

    def _do(ch):
        """Compacte une section — n'accepte la version IA que si elle est PLUS COURTE
        et ne perd/n'invente AUCUN chiffre ; sinon garde la section originale."""
        cht = count_tokens(ch)
        ok, res, _m = _chat(spec["system"], ch, min(8000, cht + 40), model)
        if not ok:
            return ch
        res = res.strip()
        f = fidelity.check(ch, res)
        if count_tokens(res) < cht and not f["missing"] and not f["added"]:
            return res
        return ch

    t = time.time()
    workers = min(LLM_MAX_WORKERS, len(chunks)) or 1
    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(_do, chunks))  # ex.map conserve l'ordre
    result = "\n\n".join(out)
    # Filet de sécurité global : jamais plus gros, jamais un chiffre perdu
    # (cas limite d'un nombre coupé entre 2 sections) -> sinon on retombe sur le Nettoyage.
    fw = fidelity.check(markdown, result)
    if count_tokens(result) >= count_tokens(markdown) or fw["missing"] or fw["added"]:
        result = markdown
    return True, result, {"seconds": round(time.time() - t, 1),
                          "model": model or LLM_MODEL, "chunks": len(chunks)}

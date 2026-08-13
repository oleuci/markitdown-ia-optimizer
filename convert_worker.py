#!/usr/bin/env python3
"""
Worker de conversion isolé — appelé en sous-processus par app.py.
Isolation volontaire : timeout + limites de ressources, aucun accès réseau utilisé.
Sortie: JSON {"ok": bool, "markdown": str, "engine": str, "error": str} sur stdout.

Usage: convert_worker.py <chemin_fichier> <mode:auto|plain|ocr> <langs:fra+eng>
"""
import sys
import os
import json
import resource
import subprocess
import tempfile
import shutil

OCR_EXT_IMAGE = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
# Formats bureautiques passés par LibreOffice headless -> OOXML -> MarkItDown
SOFFICE_MAP = {".odt": "docx", ".doc": "docx", ".rtf": "docx",
               ".ods": "xlsx", ".odp": "pptx", ".ppt": "pptx"}


def _limit_resources():
    # Plafond CPU (secondes) — dernier rempart si un parseur boucle.
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (170, 175))
    except Exception:
        pass


def _markitdown(path: str) -> str:
    from markitdown import MarkItDown
    md = MarkItDown(enable_plugins=False)  # pas de plugins tiers
    res = md.convert(path)
    return res.text_content or ""


def _ocr_pdf(path: str, langs: str) -> str:
    """OCRmyPDF (tesseract) -> PDF avec couche texte -> markitdown."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        out_pdf = tf.name
    try:
        subprocess.run(
            ["ocrmypdf", "--force-ocr", "-l", langs, "--optimize", "0",
             "--output-type", "pdf", path, out_pdf],
            check=True, capture_output=True, timeout=150,
        )
        return _markitdown(out_pdf)
    finally:
        try:
            os.unlink(out_pdf)
        except Exception:
            pass


def _ocr_image(path: str, langs: str) -> str:
    import pytesseract
    from PIL import Image
    with Image.open(path) as im:
        return pytesseract.image_to_string(im, lang=langs) or ""


def _soffice(path: str, ext: str) -> str:
    """LibreOffice headless : ODF/doc/ppt/rtf -> OOXML -> MarkItDown."""
    target = SOFFICE_MAP[ext]
    outdir = tempfile.mkdtemp(prefix="so_")
    profile = tempfile.mkdtemp(prefix="soprofile_")
    try:
        subprocess.run(
            ["soffice", "--headless", "--nologo", "--nofirststartwizard", "--nolockcheck",
             "-env:UserInstallation=file://" + profile,
             "--convert-to", target, "--outdir", outdir, path],
            check=True, capture_output=True, timeout=120,
            env={**os.environ, "HOME": profile},
        )
        base = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(outdir, base + "." + target)
        if not os.path.exists(out):
            produced = os.listdir(outdir)
            if not produced:
                raise RuntimeError("LibreOffice : aucune sortie produite")
            out = os.path.join(outdir, produced[0])
        return _markitdown(out)
    finally:
        shutil.rmtree(outdir, ignore_errors=True)
        shutil.rmtree(profile, ignore_errors=True)


def main():
    _limit_resources()
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "no path"}))
        return
    path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "auto"
    langs = sys.argv[3] if len(sys.argv) > 3 else "fra+eng"
    ext = os.path.splitext(path)[1].lower()

    try:
        # Formats bureautiques (ODF, doc, ppt, rtf) -> LibreOffice -> OOXML -> MarkItDown
        if ext in SOFFICE_MAP:
            return _emit(_soffice(path, ext), "libreoffice+markitdown")

        # OCR explicite
        if mode == "ocr":
            if ext in OCR_EXT_IMAGE:
                return _emit(_ocr_image(path, langs), "ocr-image")
            if ext == ".pdf":
                return _emit(_ocr_pdf(path, langs), "ocr-pdf")
            # sinon, pas d'OCR pertinent -> conversion normale
            return _emit(_markitdown(path), "markitdown")

        # Conversion normale
        text = _markitdown(path)

        # Repli automatique : PDF/image quasi vide => probablement scanné
        if mode == "auto" and len(text.strip()) < 12:
            if ext == ".pdf":
                ocr = _ocr_pdf(path, langs)
                if len(ocr.strip()) > len(text.strip()):
                    return _emit(ocr, "ocr-pdf (auto)")
            elif ext in OCR_EXT_IMAGE:
                ocr = _ocr_image(path, langs)
                if len(ocr.strip()) > len(text.strip()):
                    return _emit(ocr, "ocr-image (auto)")

        return _emit(text, "markitdown")
    except subprocess.TimeoutExpired:
        print(json.dumps({"ok": False, "error": "OCR: délai dépassé"}))
    except FileNotFoundError as e:
        print(json.dumps({"ok": False, "error": f"outil manquant: {e}"}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))


def _emit(markdown: str, engine: str):
    print(json.dumps({"ok": True, "markdown": markdown, "engine": engine}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
MarkItDown Web — conversion de fichiers -> Markdown, avec comptes + 2FA.
Exposé derrière un reverse proxy TLS. Sécurité par défaut.
"""
import os
import io
import re
import json
import time
import base64
import secrets
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timezone

import pyotp
import qrcode
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from cleanup import clean_markdown
import llm
import fidelity

# ---------------------------------------------------------------- config
BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "app.db"
WORKER = str(BASE / "convert_worker.py")
PYBIN = os.environ.get("PYBIN", "python3")

SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET:
    raise SystemExit("SESSION_SECRET manquant (voir secret.env)")

SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "1") == "1"
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
MAX_UPLOAD = MAX_UPLOAD_MB * 1024 * 1024
CONVERT_TIMEOUT = int(os.environ.get("CONVERT_TIMEOUT", "170"))
APP_TITLE = os.environ.get("APP_TITLE", "MarkItDown")
OCR_LANGS = os.environ.get("OCR_LANGS", "fra+eng")

# Verrouillage anti-bruteforce
MAX_FAILED = 5
LOCK_SECONDS = 15 * 60

ALLOWED_EXT = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".doc", ".ppt",
    ".html", ".htm", ".csv", ".tsv", ".json", ".xml", ".txt", ".md",
    ".rtf", ".epub", ".odt", ".ods", ".odp",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp",
    ".zip",
}

ph = PasswordHasher()
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _asset_ver():
    v = 0
    for p in ("static/app.js", "static/style.css", "static/account.js",
              "static/admin.js", "static/favicon.svg"):
        try:
            v = max(v, int(os.path.getmtime(BASE / p)))
        except OSError:
            pass
    return str(v)


ASSET_VER = _asset_ver()

# ---------------------------------------------------------------- db
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            pw_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            totp_secret TEXT,
            totp_enabled INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            must_change_pw INTEGER NOT NULL DEFAULT 0,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
    bootstrap_admin()


def get_setting(key, default=None):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_setting(key, value):
    with db() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def enforce_2fa() -> bool:
    return get_setting("enforce_2fa", "0") == "1"


def user_by_id(uid):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def user_by_name(name):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()


def now_ts():
    return int(time.time())


def bootstrap_admin():
    with db() as c:
        n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    if n:
        return
    pw = os.environ.get("ADMIN_INITIAL_PASSWORD") or secrets.token_urlsafe(12)
    with db() as c:
        c.execute("INSERT INTO users(username,pw_hash,role,must_change_pw,created_at) "
                  "VALUES(?,?,?,?,?)",
                  ("admin", ph.hash(pw), "admin", 1, datetime.now(timezone.utc).isoformat()))
    p = DATA_DIR / "INITIAL_ADMIN_PASSWORD.txt"
    p.write_text(f"admin / {pw}\n(à changer à la première connexion)\n")
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    print(f"[bootstrap] compte admin créé — identifiants dans {p}", flush=True)


# ---------------------------------------------------------------- app
app = FastAPI(title=APP_TITLE, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SECURE_COOKIES,
    same_site="strict",
    max_age=8 * 3600,
    session_cookie="md_session",
)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.on_event("startup")
def _startup():
    init_db()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    return resp


# ---------------------------------------------------------------- helpers
def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


def csrf_ok(request: Request, supplied: str) -> bool:
    tok = request.session.get("csrf")
    return bool(tok) and bool(supplied) and secrets.compare_digest(tok, supplied)


def flash(request: Request, msg: str, kind: str = "info"):
    request.session.setdefault("_flash", []).append({"msg": msg, "kind": kind})


def render(request: Request, tpl: str, **ctx):
    user = current_user(request)
    flashes = request.session.pop("_flash", [])
    base = {
        "request": request, "app_title": APP_TITLE, "user": user,
        "csrf": csrf_token(request), "flashes": flashes,
        "enforce_2fa": enforce_2fa(), "asset_ver": ASSET_VER,
    }
    base.update(ctx)
    return templates.TemplateResponse(tpl, base)


def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    u = user_by_id(uid)
    if not u or not u["active"]:
        request.session.clear()
        return None
    return u


def post_login_target(user) -> str:
    if user["must_change_pw"]:
        return "/account"
    if enforce_2fa() and not user["totp_enabled"]:
        return "/2fa/setup"
    return "/"


def incomplete_redirect(request: Request, user):
    """Empêche d'utiliser l'appli tant que compte incomplet (pw à changer / 2FA à activer)."""
    if user["must_change_pw"]:
        return RedirectResponse("/account", status_code=303)
    if enforce_2fa() and not user["totp_enabled"]:
        return RedirectResponse("/2fa/setup", status_code=303)
    return None


# ---------------------------------------------------------------- auth routes
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          csrf: str = Form("")):
    if not csrf_ok(request, csrf):
        flash(request, "Session expirée, réessayez.", "error")
        return RedirectResponse("/login", status_code=303)
    u = user_by_name(username.strip())
    generic = "Identifiants invalides."
    if not u or not u["active"]:
        flash(request, generic, "error")
        return RedirectResponse("/login", status_code=303)
    if u["locked_until"] > now_ts():
        mins = (u["locked_until"] - now_ts()) // 60 + 1
        flash(request, f"Compte temporairement verrouillé (~{mins} min).", "error")
        return RedirectResponse("/login", status_code=303)
    try:
        ph.verify(u["pw_hash"], password)
    except VerifyMismatchError:
        fa = u["failed_attempts"] + 1
        locked = now_ts() + LOCK_SECONDS if fa >= MAX_FAILED else 0
        with db() as c:
            c.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                      (fa if not locked else 0, locked, u["id"]))
        flash(request, generic, "error")
        return RedirectResponse("/login", status_code=303)
    # mot de passe OK
    with db() as c:
        c.execute("UPDATE users SET failed_attempts=0, locked_until=0 WHERE id=?", (u["id"],))
    if ph.check_needs_rehash(u["pw_hash"]):
        with db() as c:
            c.execute("UPDATE users SET pw_hash=? WHERE id=?", (ph.hash(password), u["id"]))
    if u["totp_enabled"]:
        request.session["pending_uid"] = u["id"]
        return RedirectResponse("/2fa", status_code=303)
    return _finalize_login(request, u)


def _finalize_login(request: Request, user):
    request.session.pop("pending_uid", None)
    request.session["uid"] = user["id"]
    return RedirectResponse(post_login_target(user), status_code=303)


@app.get("/2fa", response_class=HTMLResponse)
def twofa_form(request: Request):
    if not request.session.get("pending_uid"):
        return RedirectResponse("/login", status_code=303)
    return render(request, "twofa_verify.html")


@app.post("/2fa")
def twofa_verify(request: Request, code: str = Form(...), csrf: str = Form("")):
    if not csrf_ok(request, csrf):
        return RedirectResponse("/login", status_code=303)
    pid = request.session.get("pending_uid")
    if not pid:
        return RedirectResponse("/login", status_code=303)
    u = user_by_id(pid)
    if not u or not u["totp_secret"]:
        return RedirectResponse("/login", status_code=303)
    if not pyotp.TOTP(u["totp_secret"]).verify(code.strip(), valid_window=1):
        flash(request, "Code invalide.", "error")
        return RedirectResponse("/2fa", status_code=303)
    return _finalize_login(request, u)


@app.get("/2fa/setup", response_class=HTMLResponse)
def twofa_setup_form(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    if u["must_change_pw"]:
        return RedirectResponse("/account", status_code=303)
    secret = request.session.get("setup_secret")
    if not secret:
        secret = pyotp.random_base32()
        request.session["setup_secret"] = secret
    uri = pyotp.TOTP(secret).provisioning_uri(name=u["username"], issuer_name=APP_TITLE)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return render(request, "twofa_setup.html", qr_data=qr_data, secret=secret,
                  forced=(enforce_2fa() and not u["totp_enabled"]))


@app.post("/2fa/setup")
def twofa_setup(request: Request, code: str = Form(...), csrf: str = Form("")):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    if not csrf_ok(request, csrf):
        return RedirectResponse("/2fa/setup", status_code=303)
    secret = request.session.get("setup_secret")
    if not secret or not pyotp.TOTP(secret).verify(code.strip(), valid_window=1):
        flash(request, "Code invalide, réessayez.", "error")
        return RedirectResponse("/2fa/setup", status_code=303)
    with db() as c:
        c.execute("UPDATE users SET totp_secret=?, totp_enabled=1 WHERE id=?", (secret, u["id"]))
    request.session.pop("setup_secret", None)
    flash(request, "Double authentification activée. ✅", "ok")
    return RedirectResponse("/account", status_code=303)


@app.post("/2fa/disable")
def twofa_disable(request: Request, csrf: str = Form("")):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    if not csrf_ok(request, csrf):
        return RedirectResponse("/account", status_code=303)
    if enforce_2fa():
        flash(request, "La 2FA est imposée par l'administrateur, impossible de la désactiver.", "error")
        return RedirectResponse("/account", status_code=303)
    with db() as c:
        c.execute("UPDATE users SET totp_secret=NULL, totp_enabled=0 WHERE id=?", (u["id"],))
    flash(request, "Double authentification désactivée.", "info")
    return RedirectResponse("/account", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- account
@app.get("/account", response_class=HTMLResponse)
def account(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    return render(request, "account.html", force_pw=bool(u["must_change_pw"]))


@app.post("/account/password")
def change_password(request: Request, current: str = Form(""), new1: str = Form(...),
                    new2: str = Form(...), csrf: str = Form("")):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    if not csrf_ok(request, csrf):
        return RedirectResponse("/account", status_code=303)
    # si changement volontaire (pas forcé), vérifier le mot de passe actuel
    if not u["must_change_pw"]:
        try:
            ph.verify(u["pw_hash"], current)
        except VerifyMismatchError:
            flash(request, "Mot de passe actuel incorrect.", "error")
            return RedirectResponse("/account", status_code=303)
    if new1 != new2:
        flash(request, "Les deux mots de passe ne correspondent pas.", "error")
        return RedirectResponse("/account", status_code=303)
    if len(new1) < 10:
        flash(request, "Mot de passe trop court (10 caractères minimum).", "error")
        return RedirectResponse("/account", status_code=303)
    with db() as c:
        c.execute("UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?",
                  (ph.hash(new1), u["id"]))
    flash(request, "Mot de passe mis à jour. ✅", "ok")
    return RedirectResponse(post_login_target(user_by_id(u["id"])), status_code=303)


# ---------------------------------------------------------------- convert (app)
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    r = incomplete_redirect(request, u)
    if r:
        return r
    exts = ", ".join(sorted(e[1:] for e in ALLOWED_EXT))
    levels = [{"id": k, "name": v["name"], "loss": v["loss"],
               "est": v.get("est_ratio", 0.4)}
              for k, v in sorted(llm.LEVELS.items())]
    return render(request, "convert.html", allowed=exts, max_mb=MAX_UPLOAD_MB,
                  levels_json=json.dumps(levels, ensure_ascii=False),
                  llm_enabled=llm.LLM_ENABLED, llm_max_input=llm.LLM_MAX_INPUT)


@app.post("/optimize")
async def optimize_ep(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"error": "non authentifié"}, status_code=401)
    if u["must_change_pw"] or (enforce_2fa() and not u["totp_enabled"]):
        return JSONResponse({"error": "compte à finaliser"}, status_code=403)
    if not csrf_ok(request, request.headers.get("x-csrf-token", "")):
        return JSONResponse({"error": "jeton CSRF invalide"}, status_code=403)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "corps invalide"}, status_code=400)
    md = data.get("markdown", "")
    try:
        level = int(data.get("level", 0))
    except (TypeError, ValueError):
        level = 0
    if not md or level not in llm.LEVELS:
        return JSONResponse({"error": "paramètres invalides"}, status_code=400)
    if len(md.encode("utf-8")) > MAX_UPLOAD:
        return JSONResponse({"error": "texte trop long"}, status_code=400)
    # Compactage (niveau 2) = découpage en sections pour un dégraissage régulier (pas de résumé)
    if level == 2:
        ok, res, meta = llm.compact_chunked(md)
    else:
        ok, res, meta = llm.optimize(md, level)
    if not ok:
        return JSONResponse({"ok": False, "error": res})
    fid = fidelity.check(md, res)
    return JSONResponse({"ok": True, "markdown": res, "tokens": llm.count_tokens(res),
                         "engine": f"{llm.LEVELS[level]['name']} · {meta.get('model', '')}",
                         "seconds": meta.get("seconds"),
                         "fidelity": fid})


@app.post("/convert")
async def convert(request: Request, files: list[UploadFile] = File(...),
                  mode: str = Form("auto"), x_csrf_token: str = Form("", alias="csrf")):
    u = current_user(request)
    if not u:
        return JSONResponse({"error": "non authentifié"}, status_code=401)
    if u["must_change_pw"] or (enforce_2fa() and not u["totp_enabled"]):
        return JSONResponse({"error": "compte à finaliser"}, status_code=403)
    if not csrf_ok(request, request.headers.get("x-csrf-token", x_csrf_token)):
        return JSONResponse({"error": "jeton CSRF invalide"}, status_code=403)
    if mode not in ("auto", "plain", "ocr"):
        mode = "auto"

    import tempfile
    results = []
    for f in files:
        name = os.path.basename(f.filename or "fichier")
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_EXT:
            results.append({"name": name, "ok": False, "error": f"extension non autorisée ({ext or 'aucune'})"})
            continue
        # écriture en flux sur disque (pas de gros tampon en RAM), taille bornée
        tmpd = tempfile.mkdtemp(prefix="md_")
        src = os.path.join(tmpd, "input" + ext)
        size = 0
        too_big = False
        try:
            with open(src, "wb") as fh:
                while True:
                    chunk = await f.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        too_big = True
                        break
                    fh.write(chunk)
            if too_big:
                results.append({"name": name, "ok": False,
                                "error": f"trop volumineux (> {MAX_UPLOAD_MB} Mo)"})
                continue
            results.append(_convert_path(name, ext, src, mode))
        finally:
            try:
                os.remove(src)
                os.rmdir(tmpd)
            except Exception:
                pass
    return JSONResponse({"results": results})


def _conv_fidelity(src, ext, engine, brut_md):
    """Fidélité de la CONVERSION (PDF numérique uniquement) : combien de valeurs chiffrées
    de la couche texte du PDF se retrouvent dans le Markdown converti."""
    if ext != ".pdf":
        return {"applicable": False, "reason": "non-PDF"}
    if "ocr" in (engine or "").lower():
        return {"applicable": False, "reason": "scan/OCR"}
    try:
        p = subprocess.run(["pdftotext", "-layout", src, "-"],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0 or len(p.stdout.strip()) < 5:
            return {"applicable": False, "reason": "pas de couche texte"}
        f = fidelity.check(p.stdout, brut_md)
        return {"applicable": True, "total": f["total"], "kept": f["kept"], "missing": f["missing"]}
    except Exception:
        return {"applicable": False, "reason": "erreur"}


def _convert_path(name, ext, src, mode):
    try:
        proc = subprocess.run(
            [PYBIN, WORKER, src, mode, OCR_LANGS],
            capture_output=True, text=True, timeout=CONVERT_TIMEOUT,
        )
        if proc.returncode != 0:
            return {"name": name, "ok": False,
                    "error": (proc.stderr or "échec conversion").strip()[:300]}
        try:
            out = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            return {"name": name, "ok": False, "error": "sortie worker illisible"}
        if not out.get("ok"):
            return {"name": name, "ok": False, "error": out.get("error", "échec")}
        md_text = out.get("markdown", "")
        cleaned = clean_markdown(md_text)
        engine = out.get("engine", "")
        return {"name": name, "ok": True, "markdown": md_text,
                "engine": engine, "md_name": re.sub(r"\.[^.]+$", "", name) + ".md",
                "cleaned": cleaned,
                "tokens_raw": llm.count_tokens(md_text),
                "tokens_clean": llm.count_tokens(cleaned),
                "conv_fidelity": _conv_fidelity(src, ext, engine, md_text)}
    except subprocess.TimeoutExpired:
        return {"name": name, "ok": False, "error": "délai de conversion dépassé"}


# ---------------------------------------------------------------- admin
def require_admin(request: Request):
    u = current_user(request)
    if not u or u["role"] != "admin":
        return None
    return u


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    a = require_admin(request)
    if not a:
        return RedirectResponse("/login", status_code=303)
    with db() as c:
        users = c.execute("SELECT * FROM users ORDER BY id").fetchall()
    return render(request, "admin.html", users=users)


@app.post("/admin/users/create")
def admin_create(request: Request, username: str = Form(...), role: str = Form("user"),
                 csrf: str = Form("")):
    a = require_admin(request)
    if not a:
        return RedirectResponse("/login", status_code=303)
    if not csrf_ok(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    uname = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", uname):
        flash(request, "Nom d'utilisateur invalide (3-32, lettres/chiffres/._-).", "error")
        return RedirectResponse("/admin", status_code=303)
    if user_by_name(uname):
        flash(request, "Ce nom existe déjà.", "error")
        return RedirectResponse("/admin", status_code=303)
    if role not in ("user", "admin"):
        role = "user"
    temp = secrets.token_urlsafe(10)
    with db() as c:
        c.execute("INSERT INTO users(username,pw_hash,role,must_change_pw,created_at) "
                  "VALUES(?,?,?,1,?)",
                  (uname, ph.hash(temp), role, datetime.now(timezone.utc).isoformat()))
    flash(request, f"Utilisateur « {uname} » créé. Mot de passe provisoire : {temp} "
                   f"(à lui transmettre, il devra le changer).", "ok")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{uid}/reset")
def admin_reset(request: Request, uid: int, csrf: str = Form("")):
    a = require_admin(request)
    if not a:
        return RedirectResponse("/login", status_code=303)
    if not csrf_ok(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    u = user_by_id(uid)
    if not u:
        return RedirectResponse("/admin", status_code=303)
    temp = secrets.token_urlsafe(10)
    with db() as c:
        c.execute("UPDATE users SET pw_hash=?, must_change_pw=1, failed_attempts=0, "
                  "locked_until=0 WHERE id=?", (ph.hash(temp), uid))
    flash(request, f"Mot de passe de « {u['username']} » réinitialisé : {temp}", "ok")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{uid}/toggle")
def admin_toggle(request: Request, uid: int, csrf: str = Form("")):
    a = require_admin(request)
    if not a:
        return RedirectResponse("/login", status_code=303)
    if not csrf_ok(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    if uid == a["id"]:
        flash(request, "Vous ne pouvez pas désactiver votre propre compte.", "error")
        return RedirectResponse("/admin", status_code=303)
    u = user_by_id(uid)
    if u:
        with db() as c:
            c.execute("UPDATE users SET active=? WHERE id=?", (0 if u["active"] else 1, uid))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{uid}/reset2fa")
def admin_reset2fa(request: Request, uid: int, csrf: str = Form("")):
    a = require_admin(request)
    if not a:
        return RedirectResponse("/login", status_code=303)
    if not csrf_ok(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    with db() as c:
        c.execute("UPDATE users SET totp_secret=NULL, totp_enabled=0 WHERE id=?", (uid,))
    flash(request, "2FA réinitialisée pour cet utilisateur.", "info")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{uid}/delete")
def admin_delete(request: Request, uid: int, csrf: str = Form("")):
    a = require_admin(request)
    if not a:
        return RedirectResponse("/login", status_code=303)
    if not csrf_ok(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    if uid == a["id"]:
        flash(request, "Vous ne pouvez pas supprimer votre propre compte.", "error")
        return RedirectResponse("/admin", status_code=303)
    with db() as c:
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    flash(request, "Utilisateur supprimé.", "info")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/enforce2fa")
def admin_enforce2fa(request: Request, value: str = Form("0"), csrf: str = Form("")):
    a = require_admin(request)
    if not a:
        return RedirectResponse("/login", status_code=303)
    if not csrf_ok(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    set_setting("enforce_2fa", "1" if value == "1" else "0")
    flash(request, "2FA obligatoire " + ("activée." if value == "1" else "désactivée."), "ok")
    return RedirectResponse("/admin", status_code=303)


@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")

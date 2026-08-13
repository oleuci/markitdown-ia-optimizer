# Security notes

This app is designed to be self-hosted **behind a TLS reverse proxy**, for a small,
**admin-managed** set of users. Please read this before exposing it.

## Deployment hardening

- **Always run behind a TLS reverse proxy** (Caddy, Traefik, Nginx Proxy Manager…).
  Never expose port 8080 directly on the Internet. Keep `SECURE_COOKIES=1`.
- **Generate a strong `SESSION_SECRET`** (`openssl rand -hex 32`). Never reuse the example value.
- **Bring your own LLM key** (`LLM_API_KEY`). Set an appropriate `LLM_MAX_INPUT` for cost control.
- **No public sign-up**: accounts are created by an admin only. Enforce 2FA
  (Admin → "2FA obligatoire"). Change the initial admin password immediately.
- **systemd deployment**: set `FORWARDED_ALLOW_IPS` to your proxy's IP so client IPs
  are read from `X-Forwarded-For` correctly (do not trust `*` unless the app is only
  reachable from the proxy, as is the case inside a Docker network).
- Consider an **edge protection layer** (fail2ban, CrowdSec, or your proxy's rate limiting)
  on the login endpoints.

## What the app already does

- Passwords hashed with **Argon2id**, per-user **TOTP 2FA** (enforceable), signed
  session cookies (HttpOnly/Secure/SameSite=strict), **CSRF** tokens, login lockout.
- Uploaded files are converted in a **bounded subprocess** and **deleted immediately** after.
- Strict security headers (CSP, X-Frame-Options, nosniff, Referrer-Policy).

## Residual risk to understand

Converting **untrusted files** means feeding them to parsers (MarkItDown's dependencies,
LibreOffice, Tesseract). A malicious file could, in theory, exploit a parser vulnerability.
Mitigations here: authenticated users only, bounded subprocess, and **you should run the
service in an isolated container/host with minimal outbound network access** and keep
dependencies up to date. The fidelity control is about data integrity, not sandboxing.

## Reporting

Found a vulnerability? Please open a private security advisory or contact the maintainer
rather than a public issue.

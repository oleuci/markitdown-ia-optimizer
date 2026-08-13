# MarkItDown IA Optimizer

[English](README.md) · **Français**

Application web auto-hébergée qui convertit des documents (PDF, Office, ODT, images…) en **Markdown**,
puis les **optimise pour les LLM**, avec un **contrôle de fidélité déterministe** qui garantit qu'aucune
valeur chiffrée n'est perdue en silence.

> La conversion s'appuie sur l'excellent [MarkItDown](https://github.com/microsoft/markitdown) de Microsoft.
> Ce projet ajoute la couche *autour* : nettoyage, optimisation des tokens, garde-fou de fidélité,
> authentification multi-utilisateurs et déploiement clé en main.

## Aperçu

![MarkItDown IA Optimizer : conversion et optimisation de tokens avec contrôle de fidélité](docs/screenshot.png)

*Exemple réel : un rapport public (Cour des comptes). Converti (fidélité de conversion 57/57), puis
compacté de −34 % en conservant les 52/52 valeurs chiffrées. La fidélité est vérifiée à la conversion
et à chaque niveau d'optimisation.*

## Fonctionnalités

- **Conversion multi-formats vers Markdown** : MarkItDown (natif), **LibreOffice headless**
  (ODT/ODS/ODP, DOC/PPT/RTF) et **OCR** (Tesseract + OCRmyPDF) pour les scans.
- **Optimizer** : réduction des tokens par niveaux : **Nettoyage** (heuristique, sans perte),
  **Compactage** (fidèle : jamais plus gros, jamais un chiffre perdu), **Condensé** / **Synthèse**
  (résumés, perte signalée).
- **Contrôle de fidélité** déterministe et local : compare les valeurs chiffrées (montants, %, années,
  quantités) entre la couche texte du PDF et le Markdown, puis entre le Markdown et chaque sortie
  optimisée. Rien ne se perd en silence.
- **Multi-utilisateurs** avec comptes gérés par l'admin, **Argon2id**, **2FA TOTP**, CSRF, verrouillage
  anti-bruteforce.
- **Moteur LLM au choix** : tout endpoint compatible OpenAI `/v1/chat/completions` (OpenAI,
  OVH AI Endpoints, un Ollama/vLLM local…).

## Démarrage rapide (Docker)

```bash
git clone https://github.com/<vous>/markitdown-ia-optimizer.git
cd markitdown-ia-optimizer
cp .env.example secret.env      # renseigner SESSION_SECRET et votre endpoint/clé LLM
docker compose up -d --build
```

L'appli écoute sur `127.0.0.1:8080`. **Placez-la derrière un reverse proxy TLS** (voir `SECURITY.md`).
Au premier démarrage, un mot de passe admin aléatoire est écrit dans `data/INITIAL_ADMIN_PASSWORD.txt`
(et dans les logs) : connectez-vous en `admin`, changez-le et activez la 2FA.

## Configuration

Tout par variables d'environnement (`secret.env`, voir `.env.example`) : `SESSION_SECRET`, `LLM_BASE`,
`LLM_MODEL`, `LLM_API_KEY`, `LLM_MAX_INPUT`, `LLM_MAX_WORKERS`, `MAX_UPLOAD_MB`, `OCR_LANGS`…

## Comment le compactage reste fidèle

Un LLM à qui l'on demande de « compacter sans perte » a tendance à *résumer* et à laisser filer des
chiffres. Deux mécanismes gardent **Compactage** honnête et sûr :

1. **Traitement section par section** : le document est découpé en petits morceaux ; sur un fragment
   réduit, le modèle ne peut que dégraisser, pas résumer. Les sections sont traitées en parallèle.
2. **Garde-fou déterministe** : une section compactée n'est acceptée **que si elle est plus courte *et*
   conserve chaque chiffre** ; sinon la section originale est gardée. Un contrôle final sur tout le
   document retombe sur le *Nettoyage* sans perte au moindre écart. Résultat : Compactage ne peut
   **jamais grossir** ni **perdre un chiffre** (au pire, il égale le Nettoyage).

*Condensé* et *Synthèse* sont des résumés assumés ; leur fidélité est affichée à titre informatif.

## Options de déploiement

- **Docker / Compose** (recommandé) : voir ci-dessus.
- **systemd** : voir `deploy/markitdown-web.service` (adapter les chemins et `FORWARDED_ALLOW_IPS`),
  dépendances système : `python3-venv tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng ocrmypdf
  poppler-utils libreoffice-writer libreoffice-calc libreoffice-impress`.

## Sécurité

Lisez **[SECURITY.md](SECURITY.md)** avant d'exposer l'appli. En résumé : reverse proxy TLS,
`SESSION_SECRET` fort, votre propre clé LLM, comptes admin uniquement + 2FA, conteneur isolé.

## Licence

MIT, voir [LICENSE](LICENSE). Utilise [MarkItDown](https://github.com/microsoft/markitdown) (MIT, Microsoft).

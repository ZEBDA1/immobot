# Bot Telegram De Veille Immobiliere

Bot Telegram SaaS de surveillance immobiliere multi-sources.
Il detecte des annonces, applique des filtres par utilisateur, note les annonces, detecte des signaux d'arnaque et envoie des alertes.

## Fonctionnalites Principales

- Multi-utilisateurs (filtres isoles par compte)
- Filtres immobiliers complets (prix, surface, pieces, zone, type de bien, budget charges)
- Multi-filtres pour comptes Premium (filtres nommes)
- Alertes Telegram avec format structure
- Bouton `❤️ J'aime` sous les annonces + gestion des favoris
- Panneau utilisateur avec boutons
- Mode admin (gestion premium, debug scraping, affichage annonces globales)
- Scheduler resilient (timeouts, backoff source, execution parallele des sources)
- Deduplication intra-source et inter-sources
- Fallback Selenium/Playwright en cas de blocage

## Sources Scraping

Le projet inclut actuellement:

- `paruvendu`
- `entreparticuliers`
- `leboncoin` (souvent protege anti-bot)
- `seloger` (souvent protege anti-bot)
- `pap` (souvent protege anti-bot)

Important: selon IP/proxy/cookies, certaines sources peuvent rester actives mais renvoyer `0` annonce.
Utilise `/debug_sources` (admin) pour verifier `last_batch`, `listings_total`, `empty_streak`, etc.

## Commandes Bot

### Utilisateur

- `/start` : ouvre le panneau principal
- `/set_filters [nom]` : cree/modifie un filtre
- `/add_filter [nom]` : ajoute un filtre (Premium)
- `/edit_filters [nom]` : modifie un filtre existant
- `/manage_filters` : menu cliquable de gestion des filtres
- `/view_filters` : affiche les filtres actifs
- `/favorites` : affiche les annonces aimees
- `/premium` : affiche les plans
- `/stop` : met en pause les alertes
- `/my_id` : affiche ton Telegram ID

### Admin

- `/admin` : aide admin
- `/grant_premium <telegram_id>` : passe un utilisateur en premium
- `/revoke_premium <telegram_id>` : retire le premium
- `/debug_sources` : etat detaille des sources scraping
- `/all_listings` : affiche les annonces recentes sans filtre

## Plans Free / Premium

### Free

- 1 filtre (`default`)
- alertes differees (`PREMIUM_FREE_DELAY_SECONDS`)

### Premium

- multi-filtres nommes
- alertes instantanees
- priorite de traitement

## Installation

## 1) Prerequis

- Python 3.11+
- Chrome/Chromium recommande (fallback Selenium/Playwright)

## 2) Installer

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Optionnel (Playwright):

```bash
pip install playwright
python -m playwright install chromium
```

## 3) Configurer `.env`

Copie `.env.example` vers `.env`, puis renseigne au minimum:

- `BOT_TOKEN`
- `DATABASE_URL` (par defaut `sqlite:///data.db`)

Variables utiles:

- `ADMIN_TELEGRAM_IDS=123456789,987654321`
- `PREMIUM_FREE_DELAY_SECONDS=300`
- `PROXY_URL=...`
- `ENABLE_GEOCODING=false`
- `USE_SELENIUM_FALLBACK=true`

Cookies optionnels (si sources bloquees):

- `LEBONCOIN_COOKIE=...`
- `PAP_COOKIE=...`
- `SELOGER_COOKIE=...`
- `SELOGER_USER_AGENT=...`

## 4) Lancer

```bash
python main.py
```

## Structure Du Projet

- `bot/` : handlers Telegram, etats FSM, claviers
- `scraper/` : scrapers par source
- `database/` : modeles SQLAlchemy + repository
- `services/` : scheduler, matcher, notifications, anti-scam
- `utils/` : HTTP, cache, geocodage, parsing, hash, user-agents

## Notes Importantes

- Les protections anti-bot peuvent bloquer certaines sources.
- Le scheduler applique un backoff automatique source par source.
- Les filtres peuvent recevoir un backfill initial d'annonces recentes apres sauvegarde.
- Les annonces sont dedupliquees pour limiter les doublons.

## Depannage Rapide

- Bot lent: verifier les logs scheduler et la charge Selenium/Playwright.
- Peu d'annonces: verifier `/debug_sources`, cookies, proxy, et largeur des filtres.
- Pas d'alertes: verifier que l'utilisateur est `active`, et que les filtres sont bien enregistres.

## Licence

Utilisation selon les conditions definies par le proprietaire du projet.

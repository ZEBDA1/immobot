# Deploiement sur Ubuntu Server (Docker)

## 1) Installer Docker

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

## 2) Preparer le projet

```bash
cd /opt/TGBBot
cp .env.example .env
mkdir -p storage
```

Edite `.env` et renseigne au minimum:

```env
BOT_TOKEN=ton_token_telegram
```

## 3) Lancer le bot

```bash
docker compose up -d --build
```

## 4) Verifier

```bash
docker compose ps
docker compose logs -f tgbbot
```

## 5) Mise a jour

```bash
git pull
docker compose up -d --build
```

## Notes

- La base SQLite est persistante dans `./storage/data.db`.
- Redemarrage auto active avec `restart: unless-stopped`.

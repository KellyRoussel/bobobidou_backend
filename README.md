# Bobobidou backend

Backend de l'app mobile [Bobobidou](https://github.com/KellyRoussel/Bobobidou) : connexion Google, reconnaissance des ingrédients d'une photo de repas (OpenAI), réservée aux abonnés après 3 photos d'essai, abonnements (RevenueCat) et suppression de compte. Il tourne sur Google Cloud Run.

Il vient de [my-backend](https://github.com/KellyRoussel/my-backend), qui servait Bobobidou avec les autres apps. Les routes sont les mêmes (`/login/google/bobobidou`, `/auth/exchange/bobobidou`, `/bobobidou/ingredients`…) : pour passer d'un backend à l'autre, l'app n'a qu'à changer `BACKEND_URL`.

## Stack

- FastAPI, SQLAlchemy 2.0, Alembic
- PostgreSQL chez Supabase, schéma `bobobidou` (base partagée avec my-backend)
- OpenAI (vision) pour les ingrédients
- Poetry pour les dépendances
- Docker, déployé sur Cloud Run (`min-instances=0`)

## Routes

| Route | Auth | Rôle |
|---|---|---|
| `GET /health` | — | Réveil du service (appelé au lancement de l'app) |
| `GET /login/google/bobobidou` | — | URL de connexion Google |
| `GET /auth/callback/bobobidou` | — | Retour de Google, redirige vers `bobobidou://auth` |
| `GET /auth/exchange/bobobidou?code=…&state=…&service=GOOGLE` | — | Échange le code contre nos JWT |
| `POST /auth/refresh-token` (en-tête `X-Refresh-Token`) | — | Nouveaux access et refresh tokens |
| `POST /bobobidou/ingredients?language=fr\|en` | Bearer | Photo → liste d'ingrédients (402 `subscription_required` une fois l'essai épuisé, 429 au-delà de la limite du jour) |
| `GET /bobobidou/subscription` | Bearer | Abonnement, photos d'essai restantes et identifiant RevenueCat de l'utilisateur |
| `POST /bobobidou/subscription/refresh` | Bearer | Relit l'abonnement chez RevenueCat (après un achat, sans attendre le webhook) |
| `POST /bobobidou/revenuecat/webhook` | En-tête `Authorization` de RevenueCat | Met à jour l'abonnement à chaque événement RevenueCat |
| `DELETE /bobobidou/account` | Bearer | Supprime le compte et résilie l'abonnement (`subscription`: `none`, `cancelled` ou `failed`) |
| `GET /account/delete` | — | Pages web de suppression de compte (exigées par Google Play) |

## Développement local

```bash
poetry install                # crée .venv avec les dépendances, dev comprises
cp .env.example .env          # puis remplir les valeurs
poetry run uvicorn main:app --reload
poetry run pytest
```

Pour se connecter en local, le client OAuth Google doit accepter `http://localhost:8000/auth/callback/bobobidou` comme redirect URI.

Ajouter une dépendance : `poetry add <paquet>` (ou `poetry add --group dev <paquet>`), puis committer `pyproject.toml` **et** `poetry.lock`.

## Configuration

Toutes les variables sont dans [`config.py`](config.py) et [`.env.example`](.env.example). Les principales :

| Variable | |
|---|---|
| `SECRET_KEY` | Signe les JWT et sert de clé au HMAC des compteurs de photos. En changer déconnecte tout le monde et remet les limites à zéro |
| `DATABASE_URL` | Connexion avec le rôle `bobobidou_app` (accès au seul schéma `bobobidou`) |
| `MIGRATION_DATABASE_URL` | Connexion admin, **uniquement pour Alembic**, jamais sur Cloud Run |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Reconnaissance des ingrédients |
| `FREE_TRIAL_PHOTOS` | Photos offertes une seule fois par personne (3) |
| `DAILY_PHOTO_LIMIT`, `GLOBAL_DAILY_PHOTO_LIMIT` | Photos par abonné et par jour (5), et pour tout le monde (500) |
| `REVENUECAT_SECRET_KEY`, `REVENUECAT_WEBHOOK_AUTH` | Clé secrète de l'API RevenueCat (v1), et valeur de l'en-tête `Authorization` du webhook |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Client OAuth Google |
| `AUTH_REDIRECT_URI` | `<url du backend>/auth/callback` (le nom de l'app est ajouté au bout) |

## Base de données et migrations

La base Supabase est **partagée avec my-backend**, mais chacun gère son schéma :

- **my-backend** gère `public`, avec sa table de versions `public.alembic_version`. Son Alembic ignore le schéma `bobobidou`. Ses migrations `b0b1d0a1c2d3` (création du schéma) et `c2d3e4f5a6b7` (`photo_usage`) restent chez lui pour l'historique.
- **Ce repo** gère `bobobidou`, avec sa table de versions `bobobidou.alembic_version`. Toute nouvelle table ou modification Bobobidou se fait ici.

La première migration, `0001_baseline`, décrit le schéma tel qu'il était dans my-backend : schéma, rôle `bobobidou_app` et ses droits, tables `users`, `refresh_tokens`, `auth_states` et `photo_usage`. `0002_subscriptions` ajoute `photo_trials` (photos d'essai utilisées) et `subscriptions` (état RevenueCat).

```bash
export MIGRATION_DATABASE_URL=postgresql://postgres.<ref>:<mdp>@<pooler>:5432/postgres

# Une seule fois sur la base Supabase existante, où tout existe déjà :
# marque la baseline comme appliquée sans rien exécuter
poetry run alembic stamp 0001_baseline

# Sur une base neuve (ou pour les migrations suivantes)
poetry run alembic upgrade head

# Nouvelle migration après avoir modifié models/database_models.py
poetry run alembic revision --autogenerate -m "description"
poetry run alembic check      # vérifie que la base correspond aux modèles
```

Les migrations ne tournent pas au démarrage du conteneur : le service n'a que le rôle `bobobidou_app`, qui n'a pas le droit de modifier le schéma. Elles se lancent depuis un poste, avec la connexion admin, **avant** de déployer le code qui en a besoin. Les nouvelles tables du schéma sont accessibles au rôle grâce aux `DEFAULT PRIVILEGES` de la baseline.

## Déploiement

Voir [docs/deploiement-gcp.md](docs/deploiement-gcp.md) : projet GCP, client OAuth, infrastructure Terraform (`infra/`), déploiement automatique de chaque push sur `main` (GitHub Actions), puis bascule de l'app.

## Abonnements

La reconnaissance des photos est payante : 3 photos offertes une fois par personne, puis abonnement mensuel (Google Play, géré par RevenueCat). Les décisions sont dans le doc « Abonnement Bobobidou : décisions » ; en résumé :

- l'abonnement suit l'**email**, pas le compte : l'identifiant RevenueCat est un HMAC de l'email (`subscription_service.subscriber_id`), donné à l'app par `GET /bobobidou/subscription`. RevenueCat ne voit jamais l'email ;
- à chaque webhook, le backend relit le client dans l'API RevenueCat et garde la fin de la période payée dans `subscriptions`. L'accès aux photos ne lit que cette table ;
- supprimer son compte **arrête le renouvellement** (annulation Google Play via RevenueCat, sans remboursement) ; la période payée reste utilisable si la personne revient. Un échec n'empêche pas la suppression ;
- les photos d'essai (`photo_trials`) et l'état d'abonnement survivent à la suppression du compte, comme les compteurs du jour. Purges : essai inutilisé depuis 3 ans, abonnement terminé depuis 30 jours.

Configuration RevenueCat : voir [docs/deploiement-gcp.md](docs/deploiement-gcp.md#9-abonnements-revenuecat).

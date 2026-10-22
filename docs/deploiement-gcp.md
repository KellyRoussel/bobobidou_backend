# Déploiement sur Google Cloud Run

Objectif : un service Cloud Run dédié à Bobobidou, qui reste dans l'offre gratuite. L'offre gratuite de Cloud Run couvre environ 2 millions de requêtes et 180 000 vCPU-secondes par mois. Avec `min-instances=0`, rien n'est facturé quand personne n'utilise l'app ; en contrepartie, la première requête après une pause attend quelques secondes le démarrage (d'où l'appel à `/health` au lancement de l'app).

Les commandes utilisent ces variables :

```bash
PROJECT_ID=bobobidou            # à adapter
REGION=europe-west1             # Belgique : région « tier 1 », la moins chère
SERVICE=bobobidou-backend
SA=$SERVICE@$PROJECT_ID.iam.gserviceaccount.com
```

## 1. Projet GCP

1. Crée le projet et associe-lui un compte de facturation. C'est obligatoire même pour rester dans l'offre gratuite.
2. **Ajoute une alerte de budget**, par exemple 5 € : Facturation → Budgets et alertes.
3. Sélectionne-le dans gcloud (les API sont activées par Terraform, étape 4) :

```bash
gcloud config set project $PROJECT_ID
```

L'URL du service est connue d'avance : `https://$SERVICE-<numéro du projet>.$REGION.run.app`. Le numéro s'obtient avec :

```bash
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
BACKEND_URL=https://$SERVICE-$PROJECT_NUMBER.$REGION.run.app
```

## 2. Nouveau client OAuth Google (à faire)

Aujourd'hui, Bobobidou utilise le client OAuth de my-backend. On en crée un dédié dans le projet Bobobidou :

1. **Google Auth Platform → Branding** : nom « Bobobidou », e-mail d'assistance, logo, URL de la politique de confidentialité (`https://kellyroussel.github.io/Bobobidou/privacy/`).
2. **Audience** : externe. Tant que l'app est en « Testing », seuls les utilisateurs de test peuvent se connecter : passe-la en production avant la sortie publique. Les scopes `email` et `profile` ne demandent pas de vérification par Google.
3. **Clients → Créer un client** de type « Application Web », avec ces URI de redirection autorisées :
   - `$BACKEND_URL/auth/callback/bobobidou`
   - `$BACKEND_URL/account/delete/callback`
   - `http://localhost:8000/auth/callback/bobobidou` (développement local)
4. Note le client ID (variable d'environnement) et le client secret (va dans Secret Manager, étape 4).

## 3. Base de données

Le service se connecte au Supabase actuel avec le rôle `bobobidou_app`. Cloud Run sort en IPv4, alors que la connexion directe Supabase (`db.<ref>.supabase.co`) n'est disponible qu'en IPv6 : il faut passer par le **pooler** (Supabase → Connect → *Session pooler*). L'utilisateur devient alors `bobobidou_app.<ref du projet>` :

```
postgresql://bobobidou_app.<ref>:<mot de passe>@aws-0-<région>.pooler.supabase.com:5432/postgres
```

Une seule fois, avec la connexion admin, marque la baseline comme appliquée (voir le README) :

```bash
MIGRATION_DATABASE_URL=postgresql://postgres.<ref>:<mdp admin>@aws-0-<région>.pooler.supabase.com:5432/postgres \
  poetry run alembic stamp 0001_baseline
```

## 4. Infrastructure (Terraform)

L'infrastructure est décrite dans [`infra/`](../infra/) :

- les API du projet ;
- le compte de service `$SERVICE`, qui n'a accès qu'à ses secrets ;
- les 4 secrets ;
- le compte `bobobidou-builder`, qui construit les images avec le seul rôle `roles/run.builder`. Le compte Compute par défaut n'est pas utilisé : il a `roles/editor` sur le projet, et quiconque lance un build avec un compte en obtient les droits ;
- le dépôt Artifact Registry `cloud-run-source-deploy`, qui ne garde que les dernières images (0,5 Go gratuits) ;
- le service Cloud Run : 0 à 2 instances, 1 CPU, 512 Mo, variables et secrets, accès public ;
- le déploiement depuis GitHub Actions ([`infra/github.tf`](../infra/github.tf), étape 5).

Terraform ne gère ni les **valeurs** des secrets, ni le **code** déployé. Les valeurs des secrets se retrouveraient en clair dans l'état Terraform : on les ajoute dans la console Secret Manager. Le code est déployé par `gcloud run deploy` (étape 5), et Terraform ignore l'image du service.

Prérequis : Terraform (`winget install Hashicorp.Terraform`), et des identifiants pour Terraform :

```bash
gcloud auth application-default login
```

### Une seule fois : le bucket de l'état

L'état Terraform est rangé dans un bucket GCS, à créer avant le premier `terraform init` :

```bash
gcloud storage buckets create gs://bobobidou-tfstate --location=$REGION \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://bobobidou-tfstate --versioning
```

Le versioning permet de récupérer un état précédent en cas d'erreur.

### Première création

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # mets le client ID de l'étape 2
terraform init

# 1. D'abord les secrets seuls : le service Cloud Run ne démarre pas sans leurs valeurs
# (les guillemets évitent que PowerShell ne coupe l'argument au point)
terraform apply '-target=google_secret_manager_secret.app'
```

Ajoute les valeurs dans la console : Secret Manager → chaque secret → **Nouvelle version**. Le `SECRET_KEY` est nouveau, différent de celui de my-backend : génère-le avec `python -c "import secrets; print(secrets.token_urlsafe(64))"`.

Puis le reste :

```bash
# 2. Importe le compte de service déjà créé avec gcloud, crée tout le reste
terraform apply
```

Le service démarre avec une image de démonstration de Google, remplacée à l'étape 5.

### Ensuite

- **Changer la configuration** (variables, nombre d'instances, mémoire…) : modifie `infra/` puis `terraform apply`. Ne passe pas par la console ou les options de `gcloud run deploy`, sinon Terraform les annule au prochain `apply`.
- **Changer la valeur d'un secret** : nouvelle version dans la console Secret Manager, puis redéploie (étape 5) pour que le service lise la nouvelle version.
- **Vérifier que rien n'a bougé à la main** : `terraform plan` doit afficher `No changes`.

## 5. Déployer le code

Chaque push sur `main` est déployé par GitHub Actions ([`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml)) : les tests, puis `gcloud run deploy --source .`, puis un appel à `/health`. On peut aussi le relancer à la main : Actions → Deploy → *Run workflow*.

`--source .` fait construire l'image par Cloud Build à partir du `Dockerfile`, puis la range dans le dépôt `cloud-run-source-deploy`. Seule l'image change : le reste de la configuration vient de Terraform et n'est pas modifié.

Le workflow ne lance pas les migrations : lance-les à la main **avant** de merger le code qui en a besoin (voir le README).

### Une seule fois : relier GitHub au projet

Le workflow se connecte à GCP par Workload Identity Federation : GitHub échange son jeton OIDC contre des identifiants temporaires du compte `github-deployer`. Aucune clé n'est stockée dans GitHub. GCP n'accepte que le jeton du workflow `deploy.yml`, lancé depuis `main` par un push ou à la main, dans l'environnement GitHub `production` ; le repo et son propriétaire sont vérifiés par leurs identifiants numériques, qui ne changent pas même si un nom est repris par quelqu'un d'autre. Le compte `github-deployer` peut seulement déployer.

1. Crée l'environnement `production`, limité à la branche `main` (Settings → Environments, ou) :

   ```bash
   gh api -X PUT repos/KellyRoussel/bobobidou_backend/environments/production \
     -F 'deployment_branch_policy[protected_branches]=false' -F 'deployment_branch_policy[custom_branch_policies]=true'
   gh api -X POST repos/KellyRoussel/bobobidou_backend/environments/production/deployment-branch-policies -f name=main -f type=branch
   ```

2. Après le `terraform apply`, donne au repo les deux valeurs dont il a besoin. Ce sont des variables, pas des secrets : elles ne donnent aucun accès à elles seules.

   ```bash
   cd infra
   gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --body "$(terraform output -raw github_workload_identity_provider)"
   gh variable set GCP_DEPLOYER_SERVICE_ACCOUNT --body "$(terraform output -raw github_deployer_service_account)"
   ```

Les actions du workflow sont épinglées par SHA de commit (un tag peut être déplacé vers un autre code), et Dependabot ([`.github/dependabot.yml`](../.github/dependabot.yml)) propose les mises à jour.

### À la main, si besoin

Depuis la racine du repo :

```bash
gcloud run deploy $SERVICE --source . --region $REGION \
  --build-service-account projects/$PROJECT_ID/serviceAccounts/bobobidou-builder@$PROJECT_ID.iam.gserviceaccount.com
```

`.gcloudignore` choisit les fichiers envoyés : ceux du `.gitignore` (dont `.env`) et les identifiants `gha-creds-*.json` écrits par le workflow n'en font pas partie.

## 6. Vérifier

```bash
curl $BACKEND_URL/health                  # {"status":"ok"}
curl $BACKEND_URL/login/google/bobobidou  # authorization_url avec le nouveau client_id
```

Ensuite, lance l'app en local avec `BACKEND_URL=$BACKEND_URL` dans son `.env` et teste :

- la connexion ;
- une photo de repas ;
- la limite de photos ;
- la suppression du compte, depuis l'app puis depuis `$BACKEND_URL/account/delete`.

Les logs sont dans la console Cloud Run → Journaux, ou s'affichent avec `gcloud run services logs read $SERVICE --region $REGION`.

## 7. Basculer l'app

1. Mets `BACKEND_URL` à la nouvelle URL dans le `.env` de l'app et publie une version.
   Comme le `SECRET_KEY` est nouveau, les jetons émis par my-backend sont refusés : après la mise à jour, chacun doit se reconnecter une fois. Tant que d'anciennes versions de l'app appellent encore my-backend, les deux backends tiennent des compteurs de photos séparés. Pendant cette période, quelqu'un qui garde les deux versions de l'app peut donc dépasser la limite du jour.
2. **Google Play Console** : remplace l'URL de suppression de compte par `$BACKEND_URL/account/delete` (Contenu de l'application → Sécurité des données).
3. **Politique de confidentialité** (`docs/privacy/` dans le repo de l'app) : l'hébergeur devient Google Cloud (région `europe-west1`, Belgique) à la place de Render.

## 8. Plus tard : nettoyer my-backend

Quand plus aucune version de l'app n'appelle my-backend, retire de my-backend :

- `endpoints/bobobidou.py` et `endpoints/account_deletion.py` ;
- `dependencies/photo_quota_service.py` et `dependencies/account_service.py` ;
- le store Bobobidou de `dependencies/app_auth.py` et `bobobidou_auth_handler` ;
- `models/bobobidou_models.py`, `get_bobobidou_db` et `BobobidouSessionLocal` dans `database.py` ;
- les réglages `bobobidou_*` et `OPENAI_BOBOBIDOU_KEY` dans `config.py` et sur Render.

Garde ses migrations `b0b1d0a1c2d3` et `c2d3e4f5a6b7` : elles font partie de l'historique de `public.alembic_version`. Supprime aussi les URI de redirection Bobobidou de l'ancien client OAuth.

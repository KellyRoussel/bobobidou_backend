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
- le droit `roles/run.builder` du compte Compute par défaut, qui construit les images ;
- le dépôt Artifact Registry `cloud-run-source-deploy`, qui ne garde que les dernières images (0,5 Go gratuits) ;
- le service Cloud Run : 0 à 2 instances, 1 CPU, 512 Mo, variables et secrets, accès public.

Terraform ne gère ni les **valeurs** des secrets, ni le **code** déployé. Les valeurs des secrets se retrouveraient en clair dans l'état Terraform : on les ajoute avec gcloud. Le code est déployé par `gcloud run deploy` (étape 5), et Terraform ignore l'image du service.

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
terraform apply -target=google_secret_manager_secret.app
```

Ajoute les valeurs. `printf '%s'` évite le retour à la ligne final, qui se retrouverait dans le secret :

```bash
# Nouveau SECRET_KEY : différent de celui de my-backend
python -c "import secrets; print(secrets.token_urlsafe(64), end='')" | \
  gcloud secrets versions add bobobidou-secret-key --data-file=-
printf '%s' 'postgresql://bobobidou_app.<ref>:<mdp>@...:5432/postgres' | \
  gcloud secrets versions add bobobidou-database-url --data-file=-
printf '%s' 'sk-...' | gcloud secrets versions add bobobidou-openai-key --data-file=-
printf '%s' '<client secret>' | gcloud secrets versions add bobobidou-google-client-secret --data-file=-
```

Puis le reste :

```bash
# 2. Importe le compte de service déjà créé avec gcloud, crée tout le reste
terraform apply
```

Le service démarre avec une image de démonstration de Google, remplacée à l'étape 5.

### Ensuite

- **Changer la configuration** (variables, nombre d'instances, mémoire…) : modifie `infra/` puis `terraform apply`. Ne passe pas par la console ou les options de `gcloud run deploy`, sinon Terraform les annule au prochain `apply`.
- **Changer la valeur d'un secret** : `gcloud secrets versions add <nom> --data-file=-`, puis redéploie (étape 5) pour que le service lise la nouvelle version.
- **Vérifier que rien n'a bougé à la main** : `terraform plan` doit afficher `No changes`.

## 5. Déployer le code

Depuis la racine du repo :

```bash
gcloud run deploy $SERVICE --source . --region $REGION
```

`--source .` fait construire l'image par Cloud Build à partir du `Dockerfile`, puis la range dans le dépôt `cloud-run-source-deploy`. Seule l'image change : le reste de la configuration vient de Terraform et n'est pas modifié.

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

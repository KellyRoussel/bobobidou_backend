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
3. Active les API nécessaires :

```bash
gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
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

## 4. Compte de service et secrets

Un compte de service dédié, qui n'a accès qu'à ses secrets :

```bash
gcloud iam service-accounts create $SERVICE --display-name "Bobobidou backend (Cloud Run)"

# Nouveau SECRET_KEY : différent de celui de my-backend
python -c "import secrets; print(secrets.token_urlsafe(64), end='')" | \
  gcloud secrets create bobobidou-secret-key --data-file=-
printf '%s' 'postgresql://bobobidou_app.<ref>:<mdp>@...:5432/postgres' | \
  gcloud secrets create bobobidou-database-url --data-file=-
printf '%s' 'sk-...' | gcloud secrets create bobobidou-openai-key --data-file=-
printf '%s' '<client secret>' | gcloud secrets create bobobidou-google-client-secret --data-file=-

for s in bobobidou-secret-key bobobidou-database-url bobobidou-openai-key bobobidou-google-client-secret; do
  gcloud secrets add-iam-policy-binding $s --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
done
```

`printf '%s'` évite le retour à la ligne final, qui se retrouverait dans le secret. Pour changer une valeur plus tard : `gcloud secrets versions add <nom> --data-file=-`, puis redéploie.

## 5. Déployer

Depuis la racine du repo :

```bash
gcloud run deploy $SERVICE \
  --source . \
  --region $REGION \
  --service-account $SA \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 2 \
  --cpu 1 --memory 512Mi \
  --set-env-vars "GOOGLE_CLIENT_ID=<client id>,AUTH_REDIRECT_URI=$BACKEND_URL/auth/callback" \
  --set-secrets "SECRET_KEY=bobobidou-secret-key:latest,DATABASE_URL=bobobidou-database-url:latest,OPENAI_API_KEY=bobobidou-openai-key:latest,GOOGLE_CLIENT_SECRET=bobobidou-google-client-secret:latest"
```

- `--source .` fait construire l'image par Cloud Build à partir du `Dockerfile`, puis la range dans Artifact Registry (dépôt `cloud-run-source-deploy`).
- `--allow-unauthenticated` est nécessaire : c'est l'app qui appelle le service, l'authentification se fait par nos JWT.
- `--max-instances 2` plafonne la facture si quelque chose s'emballe.
- Si le build échoue sur une erreur de permission, donne `roles/run.builder` au compte de service Compute par défaut (`<numéro>-compute@developer.gserviceaccount.com`).

Les redéploiements suivants : `gcloud run deploy $SERVICE --source . --region $REGION`. La configuration (variables, secrets, compte de service) est conservée.

Artifact Registry n'offre que 0,5 Go gratuits : garde seulement les dernières images.

```bash
cat > /tmp/cleanup.json <<'EOF'
[
  {"name": "keep-last-3", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
  {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "7d"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --location=$REGION --policy=/tmp/cleanup.json
```

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

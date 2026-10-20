"""Web page to delete one's Bobobidou account without the app (required by Google Play).

Flow: GET /account/delete (explanations) → /account/delete/start (Google sign-in)
→ /account/delete/callback (confirmation form) → POST /account/delete/confirm.
"""
from datetime import timedelta
from html import escape

from fastapi import APIRouter, Depends, Form
from jwt import ExpiredSignatureError, PyJWTError
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from dependencies.account_service import delete_account, revoke_google_token
from dependencies.google_auth_service import google_auth_service
from dependencies.tokens import APP, create_token, decode_token
from models.database_models import User

account_deletion_router = APIRouter(prefix="/account/delete", tags=["Account deletion"], include_in_schema=False)

# auth_states.app_name of this page's sign-in
_APP_NAME = "account_deletion"
_TOKEN_TYPE = "account_deletion"
_CONFIRM_TOKEN_MINUTES = 10


def _redirect_uri() -> str:
    if settings.account_deletion_redirect_uri:
        return settings.account_deletion_redirect_uri
    base = settings.auth_redirect_uri.removesuffix("/").removesuffix("/auth/callback")
    return f"{base}/account/delete/callback"


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  :root {{ --corail: #E07A7A; --corail-profond: #A13F47; --peche: #FFF0E5; --cacao: #3D2B2B;
          --cacao-doux: #7A6260; --creme: #FFFFFF; --ligne: #F0DDD3; --erreur: #B3261E; }}
  body {{ margin: 0; background: var(--peche); color: var(--cacao);
         font: 16px/1.5 Nunito, system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 560px; margin: 0 auto; padding: 32px 20px 48px; }}
  .card {{ background: var(--creme); border: 1px solid var(--ligne); border-radius: 24px; padding: 24px; }}
  h1 {{ font: 600 28px/1.25 Fredoka, system-ui, sans-serif; margin: 0 0 16px; }}
  h2 {{ font: 600 18px/1.3 Fredoka, system-ui, sans-serif; margin: 24px 0 8px; }}
  p, li {{ margin: 0 0 8px; }}
  .muted {{ color: var(--cacao-doux); font-size: 14px; }}
  .button {{ display: inline-block; border: 0; border-radius: 999px; padding: 12px 24px; margin-top: 16px;
            font: 800 16px/1.25 Nunito, system-ui, sans-serif; text-decoration: none; cursor: pointer;
            background: var(--corail); color: #fff; }}
  .danger {{ background: var(--erreur); }}
  .secondary {{ background: transparent; color: var(--corail-profond); border: 1px solid var(--ligne); margin-left: 8px; }}
</style>
</head>
<body><main><div class="card">{body}</div></main></body>
</html>"""
    return HTMLResponse(html, status_code=status_code)


def _error(message_fr: str, message_en: str, status_code: int = 400) -> HTMLResponse:
    return _page("Erreur", f"""
<h1>Oups</h1>
<p>{message_fr}</p>
<p class="muted">{message_en}</p>
<a class="button" href="/account/delete">Recommencer · Start again</a>
""", status_code)


@account_deletion_router.get("", response_class=HTMLResponse)
async def account_deletion_page():
    return _page("Supprimer mon compte Bobobidou", """
<h1>Supprimer mon compte Bobobidou</h1>
<p>Vous pouvez supprimer votre compte Bobobidou depuis l'application (onglet <strong>Moi</strong> →
<strong>Supprimer mon compte</strong>) ou depuis cette page, en vous connectant avec le compte Google
utilisé dans l'application.</p>
<h2>Ce qui est supprimé</h2>
<ul>
  <li>votre compte (nom, adresse e-mail, photo de profil) ;</li>
  <li>vos jetons de connexion.</li>
</ul>
<p>La suppression est immédiate et définitive. Votre compte Google lui-même n'est pas supprimé.
Votre journal (repas, douleurs) n'est stocké que sur votre téléphone : il disparaît en désinstallant l'application.</p>
<a class="button" href="/account/delete/start">Continuer avec Google</a>

<h2>Delete my Bobobidou account</h2>
<p class="muted">Delete your account from the app (<strong>Me</strong> tab → <strong>Delete my account</strong>)
or from this page by signing in with the Google account you use in the app. Your account (name, email,
profile picture) and sign-in tokens are deleted immediately. Your Google account itself is not deleted. Your journal is only stored on your phone and is
removed when you uninstall the app.</p>
""")


@account_deletion_router.get("/start")
async def start_account_deletion(db: Session = Depends(get_db)):
    auth_url = await google_auth_service.get_auth_url(_APP_NAME, db, redirect_uri=_redirect_uri())
    return RedirectResponse(auth_url)


@account_deletion_router.get("/callback", response_class=HTMLResponse)
async def account_deletion_callback(
        code: str = None,
        state: str = None,
        error: str = None,
        db: Session = Depends(get_db),
):
    if error or not code or not state:
        return _error("La connexion Google a échoué ou a été annulée.", "Google sign-in failed or was cancelled.")

    token_info = None
    try:
        token_info = await google_auth_service.exchange_code_for_token(
            code, _APP_NAME, state, db, redirect_uri=_redirect_uri())
        user_info = await google_auth_service.get_user_info(token_info)
    except Exception as e:
        print(f"🔴 Account deletion sign-in failed: {e}")
        return _error("La connexion Google a échoué.", "Google sign-in failed.")
    finally:
        # Only needed to identify the user: revoking it also revokes the user's
        # grant to Bobobidou (its Google tokens are never stored)
        if token_info and token_info.get("access_token"):
            await revoke_google_token(token_info["access_token"])

    # users.id is our own UUID: Google users are matched by email (see save_user)
    user = (db.query(User).filter(User.email == user_info.email).first()
            if user_info.email else None)
    if not user:
        return _page("Aucun compte", f"""
<h1>Aucun compte</h1>
<p>Il n'y a pas de compte Bobobidou associé à <strong>{escape(user_info.email or "")}</strong>.</p>
<p class="muted">There is no Bobobidou account for this Google account.</p>
""", 404)

    confirm_token = create_token({"sub": user.id}, type=_TOKEN_TYPE,
                                 expires_delta=timedelta(minutes=_CONFIRM_TOKEN_MINUTES))
    return _page("Confirmer la suppression", f"""
<h1>Confirmer la suppression</h1>
<p>Supprimer définitivement le compte associé à <strong>{escape(user.email)}</strong> ?
Cette action est irréversible.</p>
<p class="muted">Permanently delete the account of this email? This cannot be undone.</p>
<form method="post" action="/account/delete/confirm">
  <input type="hidden" name="token" value="{escape(confirm_token)}">
  <button class="button danger" type="submit">Supprimer mon compte · Delete</button>
  <a class="button secondary" href="/account/delete">Annuler · Cancel</a>
</form>
""")


@account_deletion_router.post("/confirm", response_class=HTMLResponse)
async def confirm_account_deletion(token: str = Form(...), db: Session = Depends(get_db)):
    try:
        payload = decode_token(token)
    except ExpiredSignatureError:
        return _error("La demande a expiré, reconnectez-vous.", "The request expired, please sign in again.")
    except PyJWTError:
        return _error("Demande invalide.", "Invalid request.")
    if payload.get("type") != _TOKEN_TYPE or payload.get("app") != APP or not payload.get("sub"):
        return _error("Demande invalide.", "Invalid request.")

    try:
        delete_account(payload["sub"], db)
    except Exception as e:
        print(f"🔴 Error deleting account {payload['sub']}: {e}")
        return _error("La suppression a échoué, réessayez plus tard.", "Deletion failed, please try again later.", 500)

    # An already deleted account (form sent twice) ends here too
    return _page("Compte supprimé", """
<h1>Compte supprimé</h1>
<p>Votre compte Bobobidou a été supprimé et son accès à votre compte Google a été révoqué.</p>
<p class="muted">Your Bobobidou account has been deleted and its access to your Google account has been revoked.</p>
""")

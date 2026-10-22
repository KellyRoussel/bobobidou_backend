import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

from fastapi import FastAPI, Depends

from dependencies.auth_handler import auth_handler
from endpoints.account_deletion import account_deletion_router
from endpoints.authentication import authentication_router
from endpoints.bobobidou import bobobidou_router

# No CORS middleware: the only clients are the mobile app and the account deletion
# pages, served by this backend
app = FastAPI(title="Bobobidou backend", docs_url="/docs")

app.include_router(bobobidou_router, dependencies=[Depends(auth_handler)])
app.include_router(authentication_router)
app.include_router(account_deletion_router)


@app.get("/health")
def health():
    return {"status": "ok"}

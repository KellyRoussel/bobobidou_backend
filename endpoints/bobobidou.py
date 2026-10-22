import base64
import json
from datetime import datetime, timezone

from openai import AsyncOpenAI
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from dependencies.account_service import delete_account
from dependencies.photo_quota_service import PhotoLimitReached, consume_photo, next_reset, refund_photo
from models.database_models import User

bobobidou_router = APIRouter(prefix="/bobobidou", tags=["Bobobidou"])

# The language is injected in the prompt: only accept the languages the app supports
SUPPORTED_LANGUAGES = {"en": "English", "fr": "French"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


@bobobidou_router.post("/ingredients")
async def get_ingredients(
    request: Request,
    language: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    language_name = SUPPORTED_LANGUAGES.get(language.lower())
    if language_name is None:
        raise HTTPException(status_code=400, detail="Unsupported language")

    content_type = file.content_type or "image/jpeg"
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported image type")

    # Read image data, refusing files bigger than the limit
    max_bytes = settings.max_image_mb * 1024 * 1024
    image_bytes = await file.read(max_bytes + 1)
    if len(image_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail="Image too large")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image")

    # Count the photo before calling OpenAI, given back if the analysis fails
    # Also refuses the access tokens of an account deleted since they were issued
    user = db.query(User).filter(User.id == request.state.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    try:
        ticket = consume_photo(user, db)
    except PhotoLimitReached as e:
        reset_at = next_reset()
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Daily photo limit reached",
                "code": "daily_photo_limit" if e.scope == "user" else "global_photo_limit",
                "limit": e.limit,
                "reset_at": reset_at.isoformat(),
            },
            headers={"Retry-After": str(int((reset_at - datetime.now(timezone.utc)).total_seconds()))},
        )

    try:
        base64_image = base64.b64encode(image_bytes).decode("utf-8")

        # In the try: a missing key raises here, and the photo must be given back.
        # `async with` closes the client's HTTP connections
        async with AsyncOpenAI(api_key=settings.openai_api_key) as client:
            response = await client.responses.create(
                model=settings.openai_model,
                temperature=0,
                input=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant that can extract ingredients from an image."
                                   "The image provided should contain cooked food. "
                                   "Your role is to extrapolate what ingredients may be present in the food."
                                   "You have to go deep into to include root ingredients. For example, if you see pasta, include in the list both pasta AND flour"
                                   "Return a list of ingredients in required language. Every ingredient name should be in the singular form."
                                   f"Language: {language_name}",
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_image", "image_url": f"data:{content_type};base64,{base64_image}"},
                        ],
                    }
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "ingredients_list",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "ingredients": {
                                    "type": "array",
                                    "items": {
                                        "type": "string"
                                    }
                                }
                            },
                            "required": ["ingredients"],
                            "additionalProperties": False
                        },
                        "strict": True
                    }
                },
            )

        ingredients = json.loads(response.output_text)["ingredients"]
        ingredients = [ingredients.lower().strip() for ingredients in ingredients]

        return {"ingredients": ingredients}
    except Exception as e:
        # Don't leak internal errors (OpenAI messages, keys config...) to the client
        print(f"🔴 Bobobidou ingredients error: {e}")
        refund_photo(ticket, db)
        raise HTTPException(status_code=502, detail="Ingredient recognition failed")


@bobobidou_router.delete("/account")
async def delete_my_account(request: Request, db: Session = Depends(get_db)):
    """Delete the caller's account (user and refresh tokens, not the Google account)."""
    user_id = request.state.user_id
    try:
        deleted = delete_account(user_id, db)
    except Exception as e:
        print(f"🔴 Error deleting account {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete account")
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "Account deleted", "user_id": user_id}

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from config import settings
from database_url import sanitize_db_url

engine = create_engine(
    sanitize_db_url(settings.database_url),
    poolclass=NullPool,  # Supabase free tier: don't hold connections between requests
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

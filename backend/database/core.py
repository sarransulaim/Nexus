import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# PostgreSQL connection — reads from .env file
# Add this to your .env:
# DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/nexus_core
# ---------------------------------------------------------------------------
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/nexus_core"  # fallback default
)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,       # auto-reconnect if connection drops
    pool_size=10,             # keep 10 connections warm
    max_overflow=20,          # allow 20 extra under heavy load
    pool_timeout=30,          # wait max 30s for a connection
    echo=False                # set True to see SQL queries in console (debug only)
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get the DB session in our API routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
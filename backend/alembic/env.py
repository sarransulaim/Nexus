import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Make sure our app modules are importable from this file
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# ---------------------------------------------------------------------------
# Alembic config object — gives access to alembic.ini values
# ---------------------------------------------------------------------------
config = context.config

# Override sqlalchemy.url with our env variable so we never hardcode credentials
config.set_main_option(
    "sqlalchemy.url",
    os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/nexus_core")
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import ALL models so Alembic can detect them for autogenerate
from database.models import Base   # noqa: E402  (must come after sys.path insert)
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline mode — generate SQL without connecting
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect and migrate directly
# ---------------------------------------------------------------------------
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,      # detect column type changes
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
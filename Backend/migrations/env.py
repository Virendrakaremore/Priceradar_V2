from alembic import context
from sqlalchemy import pool
from priceradar.config import get_settings
from priceradar.models import Base, make_engine

target_metadata = Base.metadata
url = get_settings().database_url

def run_migrations_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    engine = make_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=url.startswith("sqlite"))
        with context.begin_transaction():
            context.run_migrations()

(run_migrations_offline if context.is_offline_mode() else run_migrations_online)()

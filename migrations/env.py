from alembic import context

from src.core.config import get_settings
from src.database.models import Base
from src.database.session import make_engine

target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = make_engine(get_settings().database_url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()

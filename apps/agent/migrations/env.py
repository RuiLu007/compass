from alembic import context
from compass_agent.database import Base
from compass_agent.models import entities  # noqa: F401
from compass_agent.settings import get_settings
from sqlalchemy import create_engine

config = context.config
if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url") or "postgresql+psycopg://",
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    else:
        url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
        with create_engine(url).connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()

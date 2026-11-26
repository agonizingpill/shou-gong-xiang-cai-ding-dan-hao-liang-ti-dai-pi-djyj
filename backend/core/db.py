from contextlib import contextmanager
from sqlalchemy import MetaData, create_engine, inspect, text

from core.config import DATABASE_URL

metadata = MetaData()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def ensure_schema():
    """Add columns introduced after the initial snapshot on persistent volumes."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if 'scent_orders' in tables:
        columns = {column['name'] for column in inspector.get_columns('scent_orders')}
        if 'due_date' not in columns:
            with engine.begin() as conn:
                conn.execute(text('ALTER TABLE scent_orders ADD COLUMN due_date VARCHAR(32) NULL'))


@contextmanager
def transaction():
    with engine.begin() as conn:
        yield conn

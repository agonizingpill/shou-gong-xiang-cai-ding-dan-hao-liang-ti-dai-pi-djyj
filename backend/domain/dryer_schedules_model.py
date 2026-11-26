from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, JSON,
                        String, Table)
from datetime import datetime, timezone

from core.db import metadata


dryer_racks = Table('dryer_racks', metadata,
    Column('code', String(64), primary_key=True),
    Column('active', Boolean, nullable=False, default=True),
    Column('capacity_g', Integer, nullable=True),
)


dryer_schedule_events = Table('dryer_schedule_events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('kind', String(32), nullable=False),
    Column('order_ref', String(64), nullable=True),
    Column('dryer', String(64), nullable=True),
    Column('created_at', DateTime(timezone=False), nullable=False,
           default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)),
)


dryer_starts = Table('dryer_starts', metadata,
    Column('code', String(64), primary_key=True),
    Column('order_ref', String(64), ForeignKey('scent_orders.code'),
           nullable=False, unique=True),
    Column('schedule_code', String(64), nullable=True),
    Column('dryer', String(64), nullable=False),
    Column('wet_grams', Integer, nullable=False),
    Column('due_date', String(32), nullable=True),
    Column('revision', Integer, nullable=False),
    Column('state', String(32), nullable=False),
)


dryer_schedules = Table('dryer_schedules', metadata,
    Column('code', String(64), primary_key=True),
    Column('token', String(64), nullable=False, unique=True),
    Column('status', String(32), nullable=False),
    Column('base_revision', Integer, nullable=False),
    Column('capacity_a_g', Integer, nullable=True),
    Column('capacity_b_g', Integer, nullable=True),
    Column('snapshot', JSON, nullable=False),
)

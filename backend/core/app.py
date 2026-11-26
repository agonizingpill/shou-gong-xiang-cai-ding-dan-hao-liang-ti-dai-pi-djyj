from contextlib import asynccontextmanager
import logging

from fastapi import Depends, FastAPI
from sqlalchemy import text

from core import auth
from core.db import metadata, transaction, ensure_schema
from core.config import SEED_DEMO
from core.middleware import install
from core.queueing import broker, view
from domain.routes import routers
from domain.dryer_schedules_service import ensure_rack_rows
from scripts.seed_demo import seed

@asynccontextmanager
async def lifespan(app):
    from core.db import engine
    metadata.create_all(engine)
    ensure_schema()
    with transaction() as conn:
        ensure_rack_rows(conn)
    if SEED_DEMO:
        try:
            seed()
        except Exception:
            logging.getLogger('uvicorn.error').exception('demo seed failed')
    yield

app = FastAPI(lifespan=lifespan)
install(app)
app.include_router(auth.router)
for router in routers:
    app.include_router(router)

@app.get('/health')
def health():
    with transaction() as conn:
        conn.execute(text('SELECT 1'))
    return {'ok': bool(broker.ping())}

@app.get('/compute/{token}')
def compute(token: str, user=Depends(auth.actor)):
    return view(user, token)

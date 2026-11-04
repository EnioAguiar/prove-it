from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import build_services, router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.services = await build_services(get_settings())
    yield


app = FastAPI(title="Prove It", lifespan=lifespan)
app.include_router(router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

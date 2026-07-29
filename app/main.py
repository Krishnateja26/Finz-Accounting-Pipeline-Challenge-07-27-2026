import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pymongo.errors import AutoReconnect, PyMongoError, ServerSelectionTimeoutError

from app.api import admin, dashboard, pnl, quickbooks, reconciliation, transactions, uploads
from app.config import get_settings
from app.database import close_client, ensure_indexes

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await ensure_indexes()
    except (AutoReconnect, PyMongoError, ServerSelectionTimeoutError) as exc:
        logging.warning("MongoDB index initialization skipped because the database is unavailable: %s", exc)
    yield
    await close_client()


settings = get_settings()
APP_DIR = Path(__file__).resolve().parent
app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

app.include_router(uploads.router)
app.include_router(transactions.router)
app.include_router(pnl.router)
app.include_router(quickbooks.router)
app.include_router(reconciliation.router)
app.include_router(dashboard.router)
app.include_router(admin.router)


def _mongo_unavailable_response(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "MongoDB connection issue. Please check your Atlas cluster status, "
                "IP access list, username/password, and network connectivity, then try again."
            ),
            "error": type(exc).__name__,
        },
    )


@app.exception_handler(ServerSelectionTimeoutError)
@app.exception_handler(AutoReconnect)
@app.exception_handler(PyMongoError)
async def mongo_exception_handler(request: Request, exc: Exception):
    return _mongo_unavailable_response(request, exc)


def _ctx(request: Request, active_page: str, **extra):
    return {"request": request, "active_page": active_page, "company_name": settings.company_name, **extra}


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", _ctx(request, "dashboard"))


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse("upload.html", _ctx(request, "upload"))


@app.get("/transactions", response_class=HTMLResponse)
async def transactions_page(request: Request):
    return templates.TemplateResponse("transactions.html", _ctx(request, "transactions"))


@app.get("/pnl", response_class=HTMLResponse)
async def pnl_page(request: Request):
    return templates.TemplateResponse("pnl.html", _ctx(request, "pnl"))


@app.get("/reconciliation", response_class=HTMLResponse)
async def reconciliation_page(request: Request):
    return templates.TemplateResponse("reconciliation.html", _ctx(request, "reconciliation"))


@app.get("/runs", response_class=HTMLResponse)
async def runs_page(request: Request):
    return templates.TemplateResponse("runs.html", _ctx(request, "runs"))


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

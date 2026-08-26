"""FastAPI entrypoint.

Run with (inside .venv-app):
    ./.venv-app/bin/uvicorn main:app --reload
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db import init_db

import os
import sys

if sys.platform == "win32":
    # Needed for WeasyPrint (PDF generation) to find its native GTK/Pango
    # DLLs on Windows. Wrapped in try/except so a missing GTK3 install
    # doesn't crash the entire app on startup — you'll only see an error
    # when a PDF is actually rendered, not on every request.
    try:
        os.add_dll_directory(r"C:\Program Files\GTK3-Runtime Win64\bin")
    except (FileNotFoundError, OSError):
        print(
            "WARNING: GTK3 Runtime not found at 'C:\\Program Files\\GTK3-Runtime "
            "Win64\\bin'. The app will still start, but PDF report generation "
            "(WeasyPrint) will fail until GTK3 is installed. "
            "See https://github.com/Kozea/WeasyPrint/blob/main/docs/install.rst#windows"
        )

from app.api.routes_project import router as project_router

from app.api.routes_project import router as project_router

app = FastAPI(
    title="audit-explainer",
    description="Turns raw Slither findings into plain-English audit reports via RAG.",
    version="0.1.0",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# The frontend is a static HTML/JS file opened directly in the browser (or
# served from a different origin/port than uvicorn), so it needs CORS
# enabled to call this API. Locked to * for local/dev use — tighten this
# to your actual frontend origin before deploying anywhere public.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(project_router)


@app.get("/health")
def health():
    return {"status": "ok"}
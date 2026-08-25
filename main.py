"""FastAPI entrypoint.

Run with (inside .venv-app):
    ./.venv-app/bin/uvicorn main:app --reload
"""
from fastapi import FastAPI

from app.api.routes import router
from app.db import init_db

import os
import sys

if sys.platform == "win32":
    os.add_dll_directory(r"C:\Program Files\GTK3-Runtime Win64\bin")

from app.api.routes_project import router as project_router

app = FastAPI(
    title="audit-explainer",
    description="Turns raw Slither findings into plain-English audit reports via RAG.",
    version="0.1.0",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.include_router(router, prefix="/api")
app.include_router(project_router)


@app.get("/health")
def health():
    return {"status": "ok"}

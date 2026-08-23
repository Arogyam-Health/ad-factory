"""Serve the React press-room build as the only dashboard UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


def react_dist(root: Path) -> Path:
    return root / "dashboard" / "web" / "dist"


def mount_react_spa(app: FastAPI, root: Path) -> None:
    dist = react_dist(root)
    index = dist / "index.html"

    def spa_index() -> FileResponse:
        if index.is_file():
            return FileResponse(
                index,
                media_type="text/html",
                headers={
                    "Cache-Control": "no-cache",
                    "Permissions-Policy": "local-network-access=(self)",
                },
            )
        raise HTTPException(status_code=404, detail="Not found")

    @app.get("/invite/{token:path}")
    def serve_invite(token: str) -> FileResponse:
        del token
        return spa_index()

    @app.get("/next")
    @app.get("/next/{path:path}")
    def redirect_legacy_react(path: str = "") -> RedirectResponse:
        target = f"/{path}" if path else "/"
        return RedirectResponse(target, status_code=307)

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/")
    def serve_root() -> FileResponse:
        return spa_index()

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        if not dist.is_dir():
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (dist / full_path).resolve()
        try:
            candidate.relative_to(dist.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        if candidate.is_file():
            return FileResponse(candidate)
        return spa_index()

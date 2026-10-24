"""Public documentation and help, packaged with the website."""

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

DOCUMENTS = json.loads((Path(__file__).parent / "site_docs.json").read_text())


def create_pages_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()
    headers = {"Cache-Control": "private, max-age=300"}

    @router.get("/skill.md", response_class=PlainTextResponse)
    async def contributor_skill():
        return PlainTextResponse(DOCUMENTS["agents"]["skill_markdown"],
                                 media_type="text/markdown",
                                 headers={"Cache-Control": "public, max-age=300"})

    @router.get("/docs", response_class=HTMLResponse)
    async def documentation_index(request: Request):
        return templates.TemplateResponse(request, "docs.html", {}, headers=headers)

    @router.get("/docs/{slug:path}", response_class=HTMLResponse)
    async def documentation_page(request: Request, slug: str):
        document = DOCUMENTS.get(slug)
        if document is None:
            raise HTTPException(404)
        return templates.TemplateResponse(request, "document.html", {"document": document}, headers=headers)

    @router.get("/help", response_class=HTMLResponse)
    async def help_page(request: Request):
        return templates.TemplateResponse(request, "help.html", {}, headers=headers)

    return router

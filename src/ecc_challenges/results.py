"""Read-only pages for Certicom's published challenge solutions."""

from datetime import date
from functools import lru_cache
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from .catalog import BY_ID

# Dates and communicators in section 6.1 of Certicom's 2009 report.
# Scalars and curve parameters come from the existing published-value registry.
PUBLISHED_RESULTS = {
    "ecc2-79": ("1997-12-16", "Robert J. Harley", 27),
    "ecc2-89": ("1998-02-09", "Robert J. Harley", 28),
    "ecc2k-95": ("1998-05-21", "Robert J. Harley", 28),
    "ecc2-97": ("1999-09-22", "Robert J. Harley", 28),
    "ecc2k-108": ("2000-04-04", "Robert J. Harley", 29),
    "ecc2-109": ("2004-04-08", "Chris Monico", 29),
    "eccp-79": ("1997-12-06", "Robert J. Harley", 29),
    "eccp-89": ("1998-01-12", "Robert J. Harley", 30),
    "eccp-97": ("1998-03-18", "Dimitris Tsapakidis", 30),
    "eccp-109": ("2002-10-15", "Chris Monico", 30),
}


def solved_date(campaign: str) -> str:
    """The section 6.1 solve date as "16 December 1997"."""
    return date.fromisoformat(PUBLISHED_RESULTS[campaign][0]).strftime("%d %B %Y").lstrip("0")


SOLVED_DATES = {campaign: solved_date(campaign) for campaign in PUBLISHED_RESULTS}


@lru_cache(maxsize=1)
def _registry():
    # Display the published data directly; serving a page requires no curve
    # arithmetic, worker configuration, database connection or external request.
    return json.loads((Path(__file__).parent / "multicurve" / "instances.json").read_text())


def published_result(campaign: str) -> dict | None:
    challenge = BY_ID.get(campaign)
    if challenge is None or not challenge.solved_in_2009 or campaign not in PUBLISHED_RESULTS:
        return None
    registry = _registry()
    vector = registry["reference_vectors"][campaign]
    instance = next(item for item in registry["instances"] if item["id"] == campaign)
    solved_on, communicated_by, source_page = PUBLISHED_RESULTS[campaign]
    return {
        "challenge": challenge.public(),
        "instance": instance,
        "scalar_hex": vector["scalar"],
        "scalar_decimal": str(int(vector["scalar"], 16)),
        "solved_on": solved_on,
        "solved_date": solved_date(campaign),
        "communicated_by": communicated_by,
        "source_section": vector["source_section"],
        "source_url": registry["source"]["url"] + f"#page={source_page}",
        "parameter_source_url": registry["source"]["url"] + "#page=35",
    }


def create_results_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/results/{campaign}", response_class=HTMLResponse)
    async def result_page(request: Request, campaign: str):
        result = published_result(campaign)
        if result is None:
            raise HTTPException(404)
        return templates.TemplateResponse(request, "result.html", {"result": result},
                                          headers={"Cache-Control": "private, max-age=300"})

    return router

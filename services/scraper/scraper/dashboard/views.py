"""scraper/dashboard/views.py — Server-rendered dashboard routes.

These pages are rendered on the server straight from the database, and a browser
navigating to a page cannot attach an ``X-API-Key`` header. So the dashboard has
a small sign-in form: the engine key entered there is checked against the engine
(the same ``authenticate`` the JSON API uses) and kept in an HttpOnly,
SameSite=Strict session cookie. Every page applies the same visibility rule as
the API. The page's own buttons call the JSON API, which still requires the
header; the sign-in form also puts the key in ``sessionStorage`` for them.
The key is never stored on the server.
"""

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from scraper.api.auth import Caller, Credential, authenticate
from scraper.storage.database import Database
from scraper.storage.models import PageStatus, ScrapeJob

router = APIRouter()

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

KEY_COOKIE = "scraper_key"


class _SignInRequiredError(Exception):
    """The dashboard request carries no usable key."""


async def _dashboard_caller(request: Request) -> Caller:
    key = request.cookies.get(KEY_COOKIE)
    if not key:
        raise _SignInRequiredError
    try:
        return await authenticate(request, Credential("api_key", key))
    except HTTPException as exc:
        if exc.status_code == 401:
            raise _SignInRequiredError from exc
        raise


def _to_login() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(KEY_COOKIE, path="/")
    return response


async def _visible_job(request: Request, caller: Caller, job_id: str) -> ScrapeJob:
    db: Database = request.app.state.db
    job = await db.get_job(job_id)
    if job is None or not caller.can_see(job):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str | None = None) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
async def login(request: Request) -> Response:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    key = (form.get("api_key") or [""])[0].strip()
    try:
        await authenticate(request, Credential("api_key", key) if key else None)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return templates.TemplateResponse(
            request, "login.html", {"error": "That key was not accepted."}, status_code=401
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        KEY_COOKIE,
        key,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@router.get("/logout")
async def logout() -> Response:
    return _to_login()


@router.get("/", response_class=HTMLResponse)
async def jobs_list(request: Request) -> Response:
    try:
        caller = await _dashboard_caller(request)
    except _SignInRequiredError:
        return _to_login()
    db: Database = request.app.state.db
    enriched = []
    for job in await db.list_jobs():
        if not caller.can_see(job):
            continue
        d: dict[str, Any] = dict(job.to_dict())
        d["counts"] = await db.count_pages_by_status(job.id)
        enriched.append(d)
    return templates.TemplateResponse(request, "jobs.html", {"jobs": enriched})


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str, status: str | None = None) -> Response:
    try:
        caller = await _dashboard_caller(request)
    except _SignInRequiredError:
        return _to_login()
    job = await _visible_job(request, caller, job_id)
    try:
        page_status = PageStatus(status) if status else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown page status: {status}") from exc
    db: Database = request.app.state.db
    counts = await db.count_pages_by_status(job_id)
    pages = await db.list_pages(job_id, status=page_status)
    return templates.TemplateResponse(request, "job_detail.html", {
        "job": job.to_dict(),
        "counts": counts,
        "pages": [p.to_dict() for p in pages],
    })


@router.get("/jobs/{job_id}/pages/{page_id}", response_class=HTMLResponse)
async def page_detail(request: Request, job_id: str, page_id: str) -> Response:
    try:
        caller = await _dashboard_caller(request)
    except _SignInRequiredError:
        return _to_login()
    await _visible_job(request, caller, job_id)
    db: Database = request.app.state.db
    page = await db.get_page(page_id)
    if not page or page.job_id != job_id:
        raise HTTPException(status_code=404, detail="Page not found")
    content_path = Path(request.app.state.data_dir) / "jobs" / job_id / f"{page_id}.md"
    content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
    return templates.TemplateResponse(request, "page_detail.html", {
        "page": page.to_dict(), "content": content,
    })

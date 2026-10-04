"""Demo UI (FastAPI + Jinja + vanilla JS): /  connections, /tools playground, /chat agent chat.
Zoho's registered redirect URI must be {UI_BASE_URL}/oauth2callback/zoho_inventory.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware

from api.integrations.config.settings import get_settings
from api.integrations.constants.constants import IntegrationsRouteNameMapping
from api.integrations.models.errors import ConnectorError
from api.integrations.routers.integrations_handlers import IntegrationsHandlers
from api.integrations.routers.mcp_spec import collect_spec
from ui.agent_loop import AgentLoop

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Agent Studio connector demo", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

handlers = IntegrationsHandlers()
agent = AgentLoop(handlers)

# One-shot flash messages ride in a signed session cookie, so concurrent browsers never see
# each other's messages. UI_SECRET keeps sessions valid across restarts; otherwise per-process.
app.add_middleware(SessionMiddleware, secret_key=get_settings().ui_secret or secrets.token_urlsafe(32),
                   same_site="lax", https_only=False)


MAX_FORM_BODY_BYTES = 8 * 1024


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    """Same error envelope the tools return, so the UI and agents can route on error.type."""
    message = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', [])[1:])}: {e.get('msg')}" for e in exc.errors()) or "invalid request"
    return JSONResponse({"ok": False, "error": {"type": "validation_error", "message": message}}, status_code=422)


def flash(request: Request, level: str, text: str) -> None:
    request.session.setdefault("flash", []).append({"level": level, "text": text})


def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
    messages = request.session.pop("flash", [])
    return templates.TemplateResponse(request, name, {"flash": messages, "settings": get_settings(), **ctx})


@app.get("/", response_class=HTMLResponse)
async def connections(request: Request):
    cards = []
    for conn in handlers.connections():
        route = conn["integration"]
        card = dict(conn)
        card["form"] = handlers.get_form(route) if conn["auth"] == "form" else None
        cards.append(card)
    return render(request, "connections.html", cards=cards)


@app.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request):
    spec = await collect_spec(handlers)
    return render(request, "tools.html", tools=spec["tools"], connections=handlers.connections())


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    settings = get_settings()
    connections = handlers.connections()
    spec = await collect_spec(handlers)
    return render(request, "chat.html", chat_enabled=bool(settings.anthropic_api_key), model=settings.anthropic_model,
                  connections=connections, tool_count=len(spec["tools"]))


@app.post("/connect/{route}")
async def connect_form(request: Request, route: str):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_FORM_BODY_BYTES:
        flash(request, "error", "form submission too large")
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    try:
        obj = await handlers.handle_callback(route, {k: str(v) for k, v in form.items()})
        flash(request, "ok", f"Connected {IntegrationsRouteNameMapping.get(route, route)}: {obj.account_name}")
    except ConnectorError as exc:
        flash(request, "error", exc.message)
    return RedirectResponse("/", status_code=303)


@app.get("/authorize/{route}")
async def authorize(request: Request, route: str, dc: str | None = None, actions: str | None = None):
    """`actions=seed` widens the requested scopes via action_scope_map (used once for seeding)."""
    try:
        extra = [a for a in (actions or "").split(",") if a]
        url, _ = handlers.get_authorization_url(route, dc=dc, actions=extra or None)
    except ConnectorError as exc:
        flash(request, "error", exc.message)
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(url, status_code=302)


@app.get("/oauth2callback/{route}")
async def oauth_callback(request: Request, route: str):
    query = {k: v for k, v in request.query_params.items()}
    try:
        obj = await handlers.handle_callback(route, query)
        flash(request, "ok", f"Connected {IntegrationsRouteNameMapping.get(route, route)}: {obj.account_name}")
    except ConnectorError as exc:
        flash(request, "error", exc.message)
    return RedirectResponse("/", status_code=303)


@app.post("/disconnect/{route}")
async def disconnect(request: Request, route: str):
    try:
        result = await handlers.disconnect(route)
        flash(request, "ok", f"Disconnected {route}" + (" and revoked the Zoho grant" if result.get("revoked_upstream") else ""))
    except ConnectorError as exc:
        flash(request, "error", exc.message)
    return RedirectResponse("/", status_code=303)


@app.get("/health/{route}")
async def health(route: str):
    try:
        return JSONResponse(await handlers.health(route))
    except ConnectorError as exc:
        return JSONResponse({"ok": False, "error": exc.to_dict()}, status_code=400)


@app.post("/tasks/{route}/{task}")
async def run_task(route: str, task: str, request: Request):
    try:
        inputs = await request.json() if await request.body() else {}
    except ValueError:
        inputs = None
    if not isinstance(inputs, dict):
        return JSONResponse({"ok": False, "error": {"type": "validation_error", "message": "request body must be a JSON object"}},
                            status_code=400)
    try:
        return JSONResponse(await handlers.handle_task(route, task, inputs))
    except ConnectorError as exc:
        return JSONResponse({"ok": False, "error": exc.to_dict()}, status_code=400)


MAX_QUESTION_CHARS = 2000
MAX_HISTORY_ENTRIES = 80
MAX_HISTORY_BYTES = 600_000  # full transcript incl. tool results, replayed so the model sees its own earlier calls


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_HISTORY_ENTRIES)

    @field_validator("history")
    @classmethod
    def _history_shape(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for entry in value:
            if entry.get("role") not in ("user", "assistant") or not isinstance(entry.get("content"), (str, list)):
                raise ValueError("history entries must be {role: user|assistant, content: str | list of blocks}")
        if len(json.dumps(value)) > MAX_HISTORY_BYTES:
            raise ValueError(f"history exceeds {MAX_HISTORY_BYTES} bytes, start a new chat")
        return value


@app.post("/chat")
async def chat(body: ChatIn):
    result = await agent.run(body.question.strip(), body.history)
    return JSONResponse({
        "answer": result.answer,
        "error": result.error,
        "rounds": result.rounds,
        "usage": result.usage,
        "cost_usd": result.cost_usd,
        "tool_calls": [{"name": t.name, "input": t.input, "ok": t.ok, "output": t.output, "ms": t.ms} for t in result.tool_calls],
    })


@app.post("/chat/stream")
async def chat_stream(body: ChatIn, request: Request):
    """Server-sent events: one `data: {json}` line per agent event. Stops when the client disconnects."""

    async def events():
        async for ev in agent.stream(body.question.strip(), body.history):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

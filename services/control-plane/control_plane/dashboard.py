import hmac
import json
import secrets
from html import escape
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .auth import make_operator_cookie, operator_required
from .models import AppRegistration, GroupPolicy, PersonalityTemplate

router = APIRouter(prefix="/operator")


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<title>{escape(title)}</title><style>
body{{font:15px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}}
a{{color:#075985}} table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ddd;padding:.45rem;text-align:left}} pre{{white-space:pre-wrap}}
.muted{{color:#666}} input,button,textarea{{padding:.5rem;margin:.2rem}}
</style></head><body><nav><a href="/operator">Control plane</a></nav>
<h1>{escape(title)}</h1>{body}</body></html>"""
    )

# Input: A small operator form request.
# Output: One plain key/value map.
async def _form(request: Request) -> dict[str, str]:
    values = parse_qs((await request.body()).decode())
    return {key: items[-1] for key, items in values.items()}


def _check_csrf(form: dict[str, str], expected: str) -> None:
    if not hmac.compare_digest(form.get("csrf", ""), expected):
        raise HTTPException(403, "invalid CSRF token")


@router.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return _page(
        "Operator login",
        '<form method="post"><input name="password" type="password" '
        'placeholder="Password"><button>Sign in</button></form>',
    )


@router.post("/login")
async def login(request: Request) -> RedirectResponse:
    raw = (await request.body()).decode()
    password = raw.partition("password=")[2].replace("+", " ")
    from urllib.parse import unquote

    password = unquote(password.partition("&")[0])
    if not hmac.compare_digest(
        password,
        request.app.state.settings.operator_password,
    ):
        raise HTTPException(401, "invalid password")
    response = RedirectResponse("/operator", 303)
    response.set_cookie(
        "operator_session",
        make_operator_cookie(request.app.state.settings),
        httponly=True,
        samesite="strict",
        secure=False,
        max_age=43_200,
    )
    return response


@router.get("", response_class=HTMLResponse)
def apps(request: Request, csrf: str = Depends(operator_required)) -> HTMLResponse:
    rows = request.app.state.repo.list_apps()
    body = "<p class=muted>Applications → groups → sessions</p><ul>"
    for row in rows:
        app_id = escape(row["app_id"])
        body += f'<li><a href="/operator/apps/{quote(row["app_id"])}">{app_id}</a>'
        body += f" — {escape(row['display_name'])}</li>"
    body += f"""</ul><h2>Register application</h2>
<form method="post" action="/operator/apps">
<input name="appId" placeholder="App ID" required>
<input name="displayName" placeholder="Display name" required>
<input name="callbackUrl" placeholder="Callback URL" required>
<input type="hidden" name="csrf" value="{escape(csrf)}">
<button>Register</button></form>"""
    return _page("Applications", body)


@router.post("/apps", response_class=HTMLResponse)
async def register_app(
    request: Request,
    csrf: str = Depends(operator_required),
) -> HTMLResponse:
    form = await _form(request)
    _check_csrf(form, csrf)
    token = secrets.token_urlsafe(32)
    webhook_secret = secrets.token_hex(32)
    app = AppRegistration.model_validate({
        "appId": form.get("appId"),
        "displayName": form.get("displayName"),
        "callbackUrl": form.get("callbackUrl"),
        "bearerToken": token,
        "webhookSecret": webhook_secret,
    })
    request.app.state.repo.save_app(app)
    body = "<p>Save these values now. They are not shown again.</p>"
    body += f"<pre>APP_TOKEN={escape(token)}\nWEBHOOK_SECRET={escape(webhook_secret)}</pre>"
    return _page("Application registered", body)


@router.get("/apps/{app_id}", response_class=HTMLResponse)
def groups(
    request: Request, app_id: str, csrf: str = Depends(operator_required)
) -> HTMLResponse:
    repo = request.app.state.repo
    if not repo.get_app(app_id):
        raise HTTPException(404, "app not found")
    body = "<h2>Groups</h2><ul>"
    for group in repo.list_groups(app_id):
        body += (
            f'<li><a href="/operator/apps/{quote(app_id)}/groups/{quote(group)}">'
            f"{escape(group)}</a></li>"
        )
    body += "</ul><h2>Personality templates</h2><ul>"
    for item in repo.personalities(app_id):
        body += f"<li>{escape(item['name'])}: {escape(item['prompt'])}</li>"
    body += f"""</ul><form method="post" action="/operator/apps/{quote(app_id)}/personalities">
<input name="personalityId" placeholder="Template ID" required>
<input name="name" placeholder="Name" required>
<textarea name="prompt" placeholder="Short personality prompt" required></textarea>
<input type="hidden" name="csrf" value="{escape(csrf)}">
<button>Save personality</button></form>"""
    return _page(app_id, body)


@router.post("/apps/{app_id}/personalities")
async def save_personality(
    request: Request,
    app_id: str,
    csrf: str = Depends(operator_required),
) -> RedirectResponse:
    form = await _form(request)
    _check_csrf(form, csrf)
    item = PersonalityTemplate.model_validate({
        key: form.get(key)
        for key in ("personalityId", "name", "prompt")
    })
    request.app.state.repo.save_personality(app_id, item)
    return RedirectResponse(f"/operator/apps/{quote(app_id)}", 303)


@router.get("/apps/{app_id}/groups/{group_id}", response_class=HTMLResponse)
def sessions(
    request: Request,
    app_id: str,
    group_id: str,
    csrf: str = Depends(operator_required),
) -> HTMLResponse:
    repo = request.app.state.repo
    rows = repo.list_sessions(app_id, group_id)
    body = "<table><tr><th>Session</th><th>State</th><th>Created</th></tr>"
    participants = {}
    for row in rows:
        link = f"/operator/apps/{quote(app_id)}/sessions/{quote(row['session_id'])}"
        body += (
            f"<tr><td><a href={link}>{escape(row['session_id'])}</a></td>"
            f"<td>{escape(row['state'])}</td><td>{escape(row['created_at'])}</td></tr>"
        )
        registration = repo.get_session(app_id, row["session_id"])
        for slot in registration.participants if registration else []:
            if slot.role.value == "virtual":
                participants[slot.participant_id] = str(
                    slot.display.get("name", slot.participant_id)
                )
    policy = repo.group_policy(app_id, group_id)
    assignments = policy.personality_assignments if policy else {}
    personalities = repo.personalities(app_id)
    body += f"""</table><h2>Virtual participants</h2>
<form method="post" action="/operator/apps/{quote(app_id)}/groups/{quote(group_id)}/policy">
<table><tr><th>Name</th><th>Participant ID</th><th>Personality</th></tr>"""
    for participant_id, name in sorted(participants.items(), key=lambda item: item[1]):
        options = '<option value="">Session default</option>'
        for personality in personalities:
            personality_id = personality["personality_id"]
            selected = " selected" if assignments.get(participant_id) == personality_id else ""
            options += (
                f'<option value="{escape(personality_id)}"{selected}>'
                f"{escape(personality['name'])}</option>"
            )
        body += (
            f"<tr><td>{escape(name)}</td><td><code>{escape(participant_id)}</code></td>"
            f'<td><select name="assignment:{escape(participant_id)}">{options}</select></td></tr>'
        )
    if not participants:
        body += "<tr><td colspan=3>No virtual participants registered yet.</td></tr>"
    body += f"""</table>
<input type="hidden" name="csrf" value="{escape(csrf)}">
<button>Save assignments</button></form>"""
    return _page(f"{app_id} / {group_id}", body)


@router.post("/apps/{app_id}/groups/{group_id}/policy")
async def save_policy(
    request: Request,
    app_id: str,
    group_id: str,
    csrf: str = Depends(operator_required),
) -> RedirectResponse:
    form = await _form(request)
    _check_csrf(form, csrf)
    assignments = {
        key.removeprefix("assignment:"): value
        for key, value in form.items()
        if key.startswith("assignment:") and value
    }
    if "personalityAssignments" in form:
        assignments.update(json.loads(form["personalityAssignments"]))
    policy = GroupPolicy(
        groupId=group_id,
        personalityAssignments=assignments,
    )
    request.app.state.repo.save_group_policy(app_id, policy)
    target = f"/operator/apps/{quote(app_id)}/groups/{quote(group_id)}"
    return RedirectResponse(target, 303)


@router.get("/apps/{app_id}/sessions/{session_id}", response_class=HTMLResponse)
def session_detail(
    request: Request,
    app_id: str,
    session_id: str,
    _: None = Depends(operator_required),
) -> HTMLResponse:
    repo = request.app.state.repo
    session = repo.get_session(app_id, session_id)
    if not session:
        raise HTTPException(404, "session not found")
    transcript = repo.transcript(app_id, session_id, limit=500)
    audit = repo.audit_log(app_id, session_id)
    health = repo.webhook_health(app_id)
    body = "<h2>Registration and private assignments</h2>"
    body += f"<pre>{escape(session.model_dump_json(by_alias=True, indent=2))}</pre>"
    body += "<h2>Transcript</h2>"
    body += f"<pre>{escape(json.dumps(transcript, indent=2))}</pre>"
    body += "<h2>Audit</h2>"
    body += f"<pre>{escape(json.dumps(audit, indent=2))}</pre>"
    body += "<h2>Webhook health</h2>"
    body += f"<pre>{escape(json.dumps(health, indent=2))}</pre>"
    return _page(f"{app_id} / {session_id}", body)

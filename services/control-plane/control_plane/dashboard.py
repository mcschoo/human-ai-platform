import hmac
import json
import secrets
from datetime import datetime
from html import escape
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .auth import make_operator_cookie, operator_required
from .models import AppRegistration, GroupPolicy, PersonalityTemplate

router = APIRouter(prefix="/operator")


def _page(
    title: str,
    body: str,
    back_href: str | None = None,
    back_label: str = "Back",
) -> HTMLResponse:
    back = (
        f'<a class="back-link" href="{escape(back_href)}" '
        f'aria-label="Back to {escape(back_label)}" title="Back to {escape(back_label)}">←</a>'
        if back_href
        else ""
    )
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
:root{{--blue:#3b82f6;--blue-dark:#2563eb;--ink:#111827;--muted:#6b7280;
--line:#d1d5db;--surface:#fff;--canvas:#f3f4f6;--danger:#b91c1c}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--canvas);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.topbar{{background:var(--surface);border-bottom:1px solid var(--line)}}
.topbar-inner{{max-width:1180px;margin:auto;padding:16px 24px;display:flex;
align-items:center;justify-content:space-between}} .topbar-left{{display:flex;
align-items:center;gap:8px}} .back-link{{display:inline-flex;width:28px;height:28px;
align-items:center;justify-content:center;border-radius:50%;color:#6b7280;
font-size:19px;text-decoration:none}} .back-link:hover{{background:#f3f4f6;
color:var(--ink);text-decoration:none}} .brand{{color:var(--ink);
font-size:16px;font-weight:700;text-decoration:none}} .brand-mark{{display:inline-block;
width:10px;height:10px;margin-right:9px;background:var(--blue);border-radius:2px}}
main{{max-width:1180px;margin:28px auto;padding:0 24px 48px}} h1{{font-size:28px;
margin:0}} h2{{font-size:18px;margin:0 0 14px}} h3{{font-size:15px}}
.page-heading{{margin:0 0 20px}}
a{{color:var(--blue-dark);text-decoration:none}} a:hover{{text-decoration:underline}}
.card{{background:var(--surface);border:1px solid #e5e7eb;border-radius:8px;
padding:20px;margin:0 0 18px;box-shadow:0 1px 2px #0000000d}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px}}
.item{{display:block;border:1px solid #e5e7eb;border-radius:7px;padding:15px;
background:#fff}} .item:hover{{border-color:#93c5fd;text-decoration:none}}
.item-title{{font-weight:650;color:var(--ink)}} .meta,.muted{{color:var(--muted);
font-size:13px}} .mono,code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
table{{border-collapse:collapse;width:100%;background:#fff}} th{{background:#f9fafb;
color:#4b5563;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
td,th{{border-bottom:1px solid #e5e7eb;padding:11px 12px;text-align:left;
vertical-align:top}} tr:last-child td{{border-bottom:0}}
label{{display:block;color:#374151;font-weight:600;margin-bottom:5px}}
input,textarea,select{{width:100%;min-height:40px;padding:9px 11px;border:1px solid
var(--line);border-radius:6px;background:#fff;color:var(--ink);font:inherit}}
textarea{{min-height:96px;resize:vertical}} input:focus,textarea:focus,select:focus{{
outline:2px solid #bfdbfe;border-color:var(--blue)}} .form-grid{{display:grid;
grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}} .field-wide{{grid-column:1/-1}}
.actions{{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:14px}}
button,.button{{display:inline-flex;width:auto;min-height:38px;align-items:center;
justify-content:center;border:1px solid transparent;border-radius:6px;padding:8px 14px;
background:var(--blue);color:#fff;font:600 14px system-ui;cursor:pointer}}
button:hover,.button:hover{{background:var(--blue-dark);text-decoration:none}}
.button-secondary{{background:#fff;color:#374151;border-color:var(--line)}}
.button-secondary:hover{{background:#f9fafb}} .button-danger{{background:#fff;
color:var(--danger);border-color:#fecaca}} .button-danger:hover{{background:#fef2f2}}
.inline-form{{display:inline}} .inline-form button{{margin:0}} .split{{display:flex;
justify-content:space-between;gap:16px;align-items:flex-start}} .pill{{display:inline-block;
padding:2px 8px;border-radius:999px;background:#eff6ff;color:#1d4ed8;font-size:12px}}
.message{{border-left:3px solid var(--blue);padding:7px 11px;margin:8px 0;background:#f9fafb}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f9fafb;border:1px solid #e5e7eb;
border-radius:6px;padding:12px}} details summary{{cursor:pointer;font-weight:600}}
@media(max-width:700px){{.form-grid{{grid-template-columns:1fr}} main{{padding:0 14px}}
.topbar-inner{{padding:14px}} .split{{display:block}}}}
</style></head><body><header class="topbar"><div class="topbar-inner">
<div class="topbar-left">{back}<a class="brand" href="/operator">
<span class="brand-mark"></span>Human AI Control Plane</a></div>
<span class="meta">Private operator console</span></div></header>
<main><div class="page-heading"><h1>{escape(title)}</h1></div>{body}</main></body></html>"""
    )

# Input: A small operator form request.
# Output: One plain key/value map.
async def _form(request: Request) -> dict[str, str]:
    values = parse_qs((await request.body()).decode())
    return {key: items[-1] for key, items in values.items()}


def _check_csrf(form: dict[str, str], expected: str) -> None:
    if not hmac.compare_digest(form.get("csrf", ""), expected):
        raise HTTPException(403, "invalid CSRF token")


# Input: A stored ISO timestamp.
# Output: A short UTC date for operator pages.
def _display_time(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


@router.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return _page(
        "Operator login",
        """<section class="card" style="max-width:420px">
<p class="muted">Sign in to manage applications and AI participants.</p>
<form method="post"><label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<div class="actions"><button>Sign in</button></div></form></section>""",
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
    body = """<section class="card"><div class="split"><div><h2>Connected applications</h2>
<p class="muted">Choose an application to manage groups, sessions, and personalities.</p>
</div><span class="pill">Applications → groups → sessions</span></div><div class="grid">"""
    for row in rows:
        app_id = escape(row["app_id"])
        body += f"""<a class="item" href="/operator/apps/{quote(row["app_id"])}">
<div class="item-title">{escape(row['display_name'])}</div>
<div class="meta mono">{app_id}</div>
<div class="meta">{escape(row['callback_url'])}</div></a>"""
    body += f"""</div></section><section class="card"><h2>Register application</h2>
<p class="muted">Create credentials for a new application adapter.</p>
<form class="form-grid" method="post" action="/operator/apps">
<div><label for="appId">Application ID</label>
<input id="appId" name="appId" placeholder="example-study" required></div>
<div><label for="displayName">Display name</label>
<input id="displayName" name="displayName" placeholder="Example study" required></div>
<div class="field-wide"><label for="callbackUrl">Callback URL</label>
<input id="callbackUrl" name="callbackUrl" placeholder="https://app.example/actions" required></div>
<input type="hidden" name="csrf" value="{escape(csrf)}">
<div class="field-wide actions"><button>Register application</button></div></form></section>"""
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
    body = """<section class="card"><h2>Credentials</h2>
<p>Save these values now. They are not shown again.</p>"""
    body += f"<pre>APP_TOKEN={escape(token)}\nWEBHOOK_SECRET={escape(webhook_secret)}</pre>"
    body += '<a class="button" href="/operator">Return to applications</a></section>'
    return _page("Application registered", body)


@router.get("/apps/{app_id}", response_class=HTMLResponse)
def groups(
    request: Request, app_id: str, csrf: str = Depends(operator_required)
) -> HTMLResponse:
    repo = request.app.state.repo
    app = repo.get_app(app_id)
    if not app:
        raise HTTPException(404, "app not found")
    body = """<section class="card"><div class="split"><div><h2>Groups</h2>
<p class="muted">Groups are created when the application registers its first session.</p>
</div></div><div class="grid">"""
    for group in repo.list_groups(app_id):
        group_id = group["group_id"]
        display_name = group["display_name"] or "Unnamed group"
        body += (
            f'<a class="item" href="/operator/apps/{quote(app_id)}/groups/{quote(group_id)}">'
            f'<div class="item-title">{escape(display_name)}</div>'
            f'<div class="meta mono">{escape(group_id)}</div>'
            f'<div class="meta">Created {escape(_display_time(group["created_at"]))}</div></a>'
        )
    body += "</div></section><section class=card><h2>Personality templates</h2>"
    body += "<p class=muted>Create reusable behavior prompts for virtual participants.</p>"
    body += "<table><tr><th>Name</th><th>Template ID</th><th>Prompt</th><th></th></tr>"
    for item in repo.personalities(app_id):
        personality_id = item["personality_id"]
        delete_url = (
            f"/operator/apps/{quote(app_id)}/personalities/"
            f"{quote(personality_id)}/delete"
        )
        body += f"""<tr><td>{escape(item['name'])}</td>
<td><code>{escape(personality_id)}</code></td><td>{escape(item['prompt'])}</td>
<td><form class="inline-form" method="post" action="{delete_url}">
<input type="hidden" name="csrf" value="{escape(csrf)}">
<button class="button-danger">Remove</button></form></td></tr>"""
    body += f"""</table></section><section class="card"><h2>Add personality</h2>
<form class="form-grid" method="post" action="/operator/apps/{quote(app_id)}/personalities">
<div><label for="personalityId">Template ID</label>
<input id="personalityId" name="personalityId" placeholder="friendly" required></div>
<div><label for="personalityName">Display name</label>
<input id="personalityName" name="name" placeholder="Friendly" required></div>
<div class="field-wide"><label for="personalityPrompt">Behavior prompt</label>
<textarea id="personalityPrompt" name="prompt" placeholder="Keep responses warm and brief." required></textarea></div>
<input type="hidden" name="csrf" value="{escape(csrf)}">
<div class="field-wide actions"><button>Save personality</button></div></form></section>"""
    return _page(app.display_name, body, "/operator", "Applications")


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


@router.post("/apps/{app_id}/personalities/{personality_id}/delete")
async def delete_personality(
    request: Request,
    app_id: str,
    personality_id: str,
    csrf: str = Depends(operator_required),
) -> RedirectResponse:
    form = await _form(request)
    _check_csrf(form, csrf)
    request.app.state.repo.delete_personality(app_id, personality_id)
    return RedirectResponse(f"/operator/apps/{quote(app_id)}", 303)


@router.get("/apps/{app_id}/groups/{group_id}", response_class=HTMLResponse)
def sessions(
    request: Request,
    app_id: str,
    group_id: str,
    csrf: str = Depends(operator_required),
) -> HTMLResponse:
    repo = request.app.state.repo
    group = repo.get_group(app_id, group_id)
    if not group:
        raise HTTPException(404, "group not found")
    rows = repo.list_sessions(app_id, group_id)
    display_name = group["display_name"] or "Unnamed group"
    group_url = f"/operator/apps/{quote(app_id)}/groups/{quote(group_id)}"
    body = f"""<section class="card"><div class="split"><div>
<h2>Group details</h2><div class="item-title">{escape(display_name)}</div>
<div class="meta mono">{escape(group_id)}</div>
<div class="meta">Created {escape(_display_time(group["created_at"]))}</div></div>
<form class="inline-form" method="post" action="{group_url}/remove">
<input type="hidden" name="csrf" value="{escape(csrf)}">
<button class="button-danger">Remove group</button></form></div>
<form class="form-grid" method="post" action="{group_url}/metadata">
<div class="field-wide"><label for="displayName">Operator-facing name</label>
<input id="displayName" name="displayName" value="{escape(group["display_name"] or "")}"
placeholder="Add a name that is easy to recognize"></div>
<input type="hidden" name="csrf" value="{escape(csrf)}">
<div class="field-wide actions"><button>Save name</button></div></form>
<p class="meta">Removing a group hides it from the dashboard but keeps its study records.</p>
</section><section class="card"><h2>Sessions</h2>
<table><tr><th>Session</th><th>State</th><th>Created</th></tr>"""
    participants = {}
    for row in rows:
        link = f"/operator/apps/{quote(app_id)}/sessions/{quote(row['session_id'])}"
        body += (
            f'<tr><td><a href="{link}"><code>{escape(row["session_id"])}</code></a></td>'
            f'<td><span class="pill">{escape(row["state"])}</span></td>'
            f'<td>{escape(_display_time(row["created_at"]))}</td></tr>'
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
    body += f"""</table></section><section class="card"><h2>Virtual participants</h2>
<p class="muted">Assign a personality to each participant ID registered by the application.</p>
<form method="post" action="{group_url}/policy">
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
<div class="actions"><button>Save assignments</button></div></form></section>"""
    app_url = f"/operator/apps/{quote(app_id)}"
    return _page(display_name, body, app_url, app_id)


@router.post("/apps/{app_id}/groups/{group_id}/metadata")
async def save_group_metadata(
    request: Request,
    app_id: str,
    group_id: str,
    csrf: str = Depends(operator_required),
) -> RedirectResponse:
    form = await _form(request)
    _check_csrf(form, csrf)
    request.app.state.repo.rename_group(app_id, group_id, form.get("displayName", ""))
    target = f"/operator/apps/{quote(app_id)}/groups/{quote(group_id)}"
    return RedirectResponse(target, 303)


@router.post("/apps/{app_id}/groups/{group_id}/remove")
async def remove_group(
    request: Request,
    app_id: str,
    group_id: str,
    csrf: str = Depends(operator_required),
) -> RedirectResponse:
    form = await _form(request)
    _check_csrf(form, csrf)
    request.app.state.repo.archive_group(app_id, group_id)
    return RedirectResponse(f"/operator/apps/{quote(app_id)}", 303)


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
    health = [
        item
        for item in repo.webhook_health(app_id)
        if item["session_id"] == session_id
    ]
    names = {
        slot.participant_id: str(slot.display.get("name", slot.participant_id))
        for slot in session.participants
    }
    body = f"""<section class="card"><h2>Session overview</h2>
<div class="grid"><div><div class="meta">Session ID</div>
<div class="mono">{escape(session_id)}</div></div>
<div><div class="meta">Group ID</div>
<div class="mono">{escape(session.group_id or "Ungrouped")}</div></div></div>
<details><summary>Registration and private assignments</summary>
<pre>{escape(session.model_dump_json(by_alias=True, indent=2))}</pre></details></section>
<section class="card"><h2>Transcript</h2>"""
    for message in transcript:
        sender = names.get(message["sender_id"], message["sender_id"] or message["role"])
        body += f"""<div class="message"><div class="split">
<strong>{escape(str(sender))}</strong>
<span class="meta">{escape(message["channel"])} ·
{escape(_display_time(message["created_at"]))}</span></div>
<div>{escape(message["text"])}</div></div>"""
    if not transcript:
        body += '<p class="muted">No messages recorded yet.</p>'
    body += """</section><section class="card"><h2>Audit</h2>
<table><tr><th>Event</th><th>Details</th><th>Time</th></tr>"""
    for item in audit:
        body += f"""<tr><td>{escape(item["kind"])}</td>
<td><code>{escape(item["detail"])}</code></td>
<td>{escape(_display_time(item["created_at"]))}</td></tr>"""
    body += """</table></section><section class="card"><h2>Webhook delivery</h2>
<table><tr><th>Action</th><th>Status</th><th>Attempts</th><th>Last error</th></tr>"""
    for item in health:
        body += f"""<tr><td><code>{escape(item["action_id"])}</code></td>
<td>{escape(item["status"])}</td><td>{item["attempts"]}</td>
<td>{escape(item["last_error"] or "—")}</td></tr>"""
    body += "</table></section>"
    back_href = (
        f"/operator/apps/{quote(app_id)}/groups/{quote(session.group_id)}"
        if session.group_id
        else f"/operator/apps/{quote(app_id)}"
    )
    return _page(f"{app_id} / {session_id}", body, back_href, "Group")

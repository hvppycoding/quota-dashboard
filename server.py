"""Private-tailnet dashboard: exactly two read routes, quota projection only."""
import datetime as dt
import hashlib
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time

from collector import read_codex, read_claude, utc_now, iso
from openrouter import read_openrouter, public_metrics
from manual import manual_claude_reset_tickets

REFRESH_SECONDS = 300
STALE_SECONDS = 600
BIND = ("127.0.0.1", 8081)
TAILNET_HOST = os.environ.get("QUOTA_DASHBOARD_HOST", "localhost").lower()
HTML = Path(__file__).with_name("index.html").read_bytes()
lock = threading.Lock()
latest = None
last_attempt = None
refresh_failed = False
latest_claude = None
last_attempt_claude = None
claude_refresh_failed = False
OPENROUTER_ENABLED = False
latest_openrouter = None
last_attempt_openrouter = None
openrouter_refresh_failed = False


def build_payload(observation, attempted_at, failed, now, claude_observation=None, claude_attempt=None, claude_failed=False, claude_enabled=False, openrouter_observation=None, openrouter_attempt=None, openrouter_failed=False, openrouter_enabled=False):
    rows = []
    codex = {"id":"codex", "name":"Codex", "status":"waiting", "observedAt":None, "lastAttemptAt":attempted_at, "stale":True, "windows":[]}
    if observation is not None:
        observed = dt.datetime.fromisoformat(observation["observedAt"].replace("Z", "+00:00"))
        codex.update(observedAt=observation["observedAt"], stale=(now-observed).total_seconds()>STALE_SECONDS or failed, status="refresh_pending" if failed else "connected")
        for w in observation["windows"]:
            # Explicit projection again; arbitrary upstream fields cannot cross this boundary.
            row = {k:w[k] for k in ("label","usedPercent","remainingPercent","windowMinutes","resetAt")}
            row["resetPassed"] = dt.datetime.fromisoformat(row["resetAt"].replace("Z","+00:00")) <= now
            codex["windows"].append(row)
    rows.append(codex)
    ticket_source=observation.get('resetTickets') if observation is not None else None
    if isinstance(ticket_source,dict):
        codex['resetTickets']={k:ticket_source[k] for k in ('availableCount','detailsAvailable','detailsComplete')}
        codex['resetTickets']['expirations']=[{k:e[k] for k in ('expiresAt','expirationKnown')} for e in ticket_source.get('expirations',[])]
    claude={'id':'claude','name':'Claude','status':'waiting' if claude_enabled else 'not_connected','observedAt':None,'lastAttemptAt':claude_attempt,'stale':claude_enabled,'windows':[]}
    if claude_observation is not None:
        observed=dt.datetime.fromisoformat(claude_observation['observedAt'].replace('Z','+00:00'))
        claude.update(observedAt=claude_observation['observedAt'],stale=(now-observed).total_seconds()>STALE_SECONDS or claude_failed,status='refresh_pending' if claude_failed else 'connected')
        for w in claude_observation['windows']:
            row={k:w[k] for k in ('label','usedPercent','remainingPercent','windowMinutes','resetAt')}
            row['resetPassed']=dt.datetime.fromisoformat(row['resetAt'].replace('Z','+00:00'))<=now
            claude['windows'].append(row)
    manual_ticket = manual_claude_reset_tickets(Path(__file__).with_name('manual-overrides.json'), now)
    if manual_ticket is not None:
        claude['manualResetTickets'] = manual_ticket
    rows.append(claude)
    openrouter={'id':'openrouter','name':'OpenRouter','status':'waiting' if openrouter_enabled else 'not_connected','observedAt':None,'lastAttemptAt':openrouter_attempt,'stale':openrouter_enabled,'windows':[]}
    if openrouter_enabled and openrouter_observation is not None:
        observed=dt.datetime.fromisoformat(openrouter_observation['observedAt'].replace('Z','+00:00'))
        openrouter.update(observedAt=iso(observed),stale=(now-observed).total_seconds()>STALE_SECONDS or openrouter_failed,status='refresh_pending' if openrouter_failed else 'connected',metrics=public_metrics(openrouter_observation['metrics']))
    if openrouter_enabled and openrouter_observation is not None:
        openrouter['activity'] = openrouter_observation.get('activity', {'status': 'access_required'})
    rows.append(openrouter)
    return {"schemaVersion":1,"servedAt":iso(now),"refreshIntervalSeconds":REFRESH_SECONDS,"staleAfterSeconds":STALE_SECONDS,"providers":rows}


def snapshot():
    with lock:
        return build_payload(latest, last_attempt, refresh_failed, utc_now(),latest_claude,last_attempt_claude,claude_refresh_failed,True,latest_openrouter,last_attempt_openrouter,openrouter_refresh_failed,OPENROUTER_ENABLED)


def collect_forever():
    global latest, last_attempt, refresh_failed
    while True:
        attempted = iso(utc_now())
        try:
            result = read_codex()
        except Exception:
            with lock:
                last_attempt, refresh_failed = attempted, True
            # No raw CLI stderr, response, identities, or exception text in logs.
            print("quota refresh unavailable", flush=True)
        else:
            with lock:
                latest, last_attempt, refresh_failed = result, attempted, False
            print("quota refreshed", flush=True)
        time.sleep(REFRESH_SECONDS)


def collect_claude_forever():
    global latest_claude,last_attempt_claude,claude_refresh_failed
    while True:
        attempted=iso(utc_now())
        try:result=read_claude()
        except Exception:
            with lock:last_attempt_claude,claude_refresh_failed=attempted,True
            print('claude quota refresh unavailable',flush=True)
        else:
            with lock:latest_claude,last_attempt_claude,claude_refresh_failed=result,attempted,False
            print('claude quota refreshed',flush=True)
        time.sleep(REFRESH_SECONDS)


def refresh_openrouter_once():
    global latest_openrouter,last_attempt_openrouter,openrouter_refresh_failed
    if not OPENROUTER_ENABLED:
        return
    attempted=iso(utc_now())
    try:result=read_openrouter(enabled=True)
    except Exception:
        with lock:last_attempt_openrouter,openrouter_refresh_failed=attempted,True
        print('openrouter usage refresh unavailable',flush=True)
    else:
        with lock:
            activity = result.get('activity', {})
            previous = (latest_openrouter or {}).get('activity', {})
            if activity.get('status') != 'available' and previous.get('status') == 'available':
                result['activity'] = {**previous, 'stale': True}
            latest_openrouter,last_attempt_openrouter,openrouter_refresh_failed=result,attempted,False
        print('openrouter usage refreshed',flush=True)


def collect_openrouter_forever():
    while OPENROUTER_ENABLED:
        refresh_openrouter_once()
        time.sleep(REFRESH_SECONDS)


def csp():
    text = HTML.decode()
    script = text.split("<script>",1)[1].split("</script>",1)[0]
    style = text.split("<style>",1)[1].split("</style>",1)[0]
    digest = lambda s: base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()
    return f"default-src 'none'; script-src 'sha256-{digest(script)}'; style-src 'sha256-{digest(style)}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log requests, URLs, cookies, or Authorization headers.

    def version_string(self):
        return "Quota Dashboard"

    def respond(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", csp())
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def dispatch(self):
        hosts = self.headers.get_all("Host", [])
        allowed = {"127.0.0.1:8081","localhost:8081",TAILNET_HOST,TAILNET_HOST+":8447"}
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            self.respond(403,b'{"error":"forbidden"}',"application/json")
        elif self.path == "/":
            self.respond(200,HTML,"text/html; charset=utf-8")
        elif self.path == "/api/usage":
            body = json.dumps(snapshot(),ensure_ascii=False,allow_nan=False,separators=(",",":")).encode()
            self.respond(200,body,"application/json; charset=utf-8")
        else:
            self.respond(404,b'{"error":"not_found"}',"application/json")

    do_GET = dispatch
    do_HEAD = dispatch

    def rejected_method(self):
        self.respond(405,b'{"error":"method_not_allowed"}',"application/json")

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = rejected_method


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser(description='Private quota dashboard')
    parser.add_argument('--enable-openrouter',action='store_true',help='Explicitly allow OpenRouter API reads using the existing exported key')
    OPENROUTER_ENABLED=parser.parse_args().enable_openrouter
    server = ThreadingHTTPServer(BIND,Handler)
    server.daemon_threads = True
    threading.Thread(target=collect_forever,daemon=True).start()
    threading.Thread(target=collect_claude_forever,daemon=True).start()
    if OPENROUTER_ENABLED:
        threading.Thread(target=collect_openrouter_forever,daemon=True).start()
    print("quota dashboard listening on loopback port 8081",flush=True)
    server.serve_forever()

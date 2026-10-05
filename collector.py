"""Quota-only reader using the installed official Codex CLI's stdio RPC."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import pwd
import selectors
import subprocess
import time
import signal


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def iso(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def project_windows(result):
    """Allowlist scalar quota fields; discard every upstream identity/metadata field."""
    limits = result.get("rateLimitsByLimitId", {}).get("codex") or result.get("rateLimits")
    if not isinstance(limits, dict):
        raise ValueError("quota_unavailable")
    windows = []
    for key in ("primary", "secondary"):
        row = limits.get(key)
        if not isinstance(row, dict):
            continue
        used, minutes, reset = (row.get(k) for k in ("usedPercent", "windowDurationMins", "resetsAt"))
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
            continue
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 < minutes <= 525600:
            continue
        if isinstance(reset, bool) or not isinstance(reset, (int, float)) or not math.isfinite(reset) or not 0 < reset < 4102444800:
            continue
        reset_at = dt.datetime.fromtimestamp(reset, dt.timezone.utc)
        label = {300: "5시간", 10080: "주간", 1440: "일간", 43200: "30일"}.get(minutes, f"{minutes}분")
        windows.append({"label": label, "usedPercent": used, "remainingPercent": 100-used, "windowMinutes": minutes, "resetAt": iso(reset_at)})
    if not windows:
        raise ValueError("quota_unavailable")
    return windows


def project_reset_tickets(result):
    summary=result.get('rateLimitResetCredits')
    unknown={'availableCount':None,'detailsAvailable':False,'detailsComplete':False,'expirations':[]}
    if not isinstance(summary,dict):return unknown
    count=summary.get('availableCount')
    if isinstance(count,bool) or not isinstance(count,int) or count<0:return unknown
    rows=summary.get('credits');expirations=[]
    if isinstance(rows,list):
        for row in rows:
            if not isinstance(row,dict) or row.get('status')!='available':continue
            known='expiresAt' in row;value=row.get('expiresAt');expires=None
            if value is not None:
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<value<4102444800:continue
                expires=iso(dt.datetime.fromtimestamp(value,dt.timezone.utc))
            expirations.append({'expiresAt':expires,'expirationKnown':known})
    return {'availableCount':count,'detailsAvailable':isinstance(rows,list),'detailsComplete':isinstance(rows,list) and len(expirations)==count,'expirations':expirations}


def read_codex():
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    env = {"HOME": str(home), "PATH": f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"}
    process = subprocess.Popen([str(home / ".local/bin/codex"), "app-server", "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=str(Path(__file__).resolve().parent))
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b""

    def request(method, request_id, params):
        nonlocal buffer
        process.stdin.write((json.dumps({"jsonrpc":"2.0", "id":request_id, "method":method, "params":params})+"\n").encode())
        process.stdin.flush()
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    response = json.loads(line)
                except ValueError:
                    continue
                if response.get("id") == request_id:
                    if "error" in response:
                        raise ValueError("cli_read_unavailable")
                    return response["result"]
            if not selector.select(max(0, deadline-time.monotonic())):
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > 1048576:
                raise ValueError("cli_response_limit")
        raise TimeoutError("cli_read_timeout")

    try:
        request("initialize", 1, {"clientInfo":{"name":"private_quota_dashboard", "version":"26.10.1"}})
        process.stdin.write(b'{"jsonrpc":"2.0","method":"initialized","params":{}}\n')
        process.stdin.flush()
        result = request("account/rateLimits/read", 2, {})
        windows = project_windows(result)
        return {"observedAt":iso(utc_now()), "windows":windows, "resetTickets":project_reset_tickets(result)}
    finally:
        selector.close()
        process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def project_claude_windows(usage):
    candidates=[(usage.get(key),label) for key,label in (('primary','5시간'),('secondary','주간'),('tertiary','추가 주간'))]
    for extra in usage.get('extraRateWindows') or []:
        if not isinstance(extra,dict) or extra.get('usageKnown') is False:continue
        title=(extra.get('title') or '').lower()
        model=next((name for name in ('Fable','Opus','Sonnet','Haiku') if name.lower() in title),None)
        candidates.append((extra.get('window'),(model+' 주간') if model else '추가 한도'))
    windows=[]
    for w,label in candidates:
        if not isinstance(w,dict) or w.get('isSyntheticPlaceholder') is True:continue
        used,reset,minutes=w.get('usedPercent'),w.get('resetsAt'),w.get('windowMinutes')
        if isinstance(used,bool) or not isinstance(used,(int,float)) or not math.isfinite(used) or not 0<=used<=100:continue
        if isinstance(minutes,bool) or not isinstance(minutes,int) or not 0<minutes<=525600:minutes=None
        if not isinstance(reset,str):continue
        try:parsed=dt.datetime.fromisoformat(reset.replace('Z','+00:00'))
        except ValueError:continue
        if parsed.tzinfo is None:continue
        windows.append({'label':label,'usedPercent':used,'remainingPercent':100-used,'windowMinutes':minutes,'resetAt':iso(parsed.astimezone(dt.timezone.utc))})
    if not windows:raise ValueError('claude_quota_unavailable')
    return windows


def read_claude():
    home=Path(pwd.getpwuid(os.getuid()).pw_dir)
    config=Path(__file__).with_name('config.cli.json')
    owner=pwd.getpwuid(os.getuid()).pw_name
    env={'HOME':str(home),'USER':owner,'LOGNAME':owner,'PATH':f'{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin','LANG':'en_US.UTF-8','DISABLE_AUTOUPDATER':'1','CLAUDE_CODE_SAFE_MODE':'1','CLAUDE_CLI_PATH':str(home/'.local/bin/claude'),'CODEXBAR_CONFIG':str(config)}
    process=subprocess.Popen(['/Applications/CodexBar.app/Contents/Helpers/CodexBarCLI','usage','--provider','claude','--source','cli','--format','json','--json-only'],env=env,cwd=str(home/'Library/Application Support/CodexBar/ClaudeProbe'),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
    try:
        raw,_=process.communicate(timeout=60)
        if process.returncode!=0 or len(raw)>1048576:raise ValueError('claude_usage_unavailable')
        rows=json.loads(raw)
        row=next((r for r in rows if r.get('provider')=='claude' and not r.get('error')),None)
        if row is None:raise ValueError('claude_usage_unavailable')
        return {'observedAt':iso(utc_now()),'windows':project_claude_windows(row.get('usage') or {})}
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)

"""Interactive local sign-in using Binged's published normal /session flow.

No PIN persistence, browser-session extraction, media proxy, or origin spoofing.
The loopback form requires a human to enter an existing PIN; no automatic retry.
Do not deploy this interactive helper to a public host.
"""
import argparse
import datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from refresh import HOSTS, SafeRedirect, atomic_write, source_id


def validate_session(data, now_ms):
    sid, exp = data.get('sid'), data.get('exp')
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', sid):
        raise ValueError('invalid_session_response')
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp <= now_ms + 60000:
        raise ValueError('invalid_session_expiry')
    return {'sid': sid, 'exp': exp}


def authenticate(origin, pin):
    request = urllib.request.Request(origin + '/session', method='POST',
        headers={'Content-Type': 'application/json', 'User-Agent': 'JOSH-IPTV-ALT/1.0'},
        data=json.dumps({'pin': pin, 'who': '', 'device': 'JOSH IPTV ALT authorization'}).encode())
    opener = urllib.request.build_opener(SafeRedirect())
    with opener.open(request, timeout=15) as response:
        data = response.read(65537)
        if len(data) > 65536:
            raise ValueError('invalid_session_response')
    return validate_session(json.loads(data), dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


def serve(origin, private_dir, port):
    u = urllib.parse.urlsplit(origin)
    if u.scheme != 'https' or u.hostname not in HOSTS or u.port not in (None, 443) or u.path or u.query or u.username:
        raise ValueError('invalid_provider_origin')
    nonce = secrets.token_urlsafe(32)
    local_origin = 'http://127.0.0.1:' + str(port)
    page = '''<!doctype html><meta charset="utf-8"><title>Authorize ALT with Binged</title>
<body style="background:#10151d;color:white;font:18px system-ui;padding:40px;max-width:750px">
<h1>Authorize ALT with Binged</h1><p>This local helper sends your existing house PIN only to <b>PROVIDER</b> using Binged's normal sign-in endpoint. It saves the returned session privately on this Mac. It never saves your PIN.</p>
<p>This tests interactive session renewal. Unattended hosted renewal has not been verified.</p>
<form><label>Existing Binged house PIN <input id="pin" type="password" autocomplete="off" required></label> <button>Sign in once</button></form><p id="result" role="status"></p>
<script>document.querySelector('form').onsubmit=async e=>{e.preventDefault();const b=document.querySelector('button');b.disabled=true;const p=document.querySelector('#pin');const pin=p.value;p.value='';try{const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json','X-ALT-CSRF':'NONCE'},body:JSON.stringify({pin})});const j=await r.json();document.querySelector('#result').textContent=j.message}catch{document.querySelector('#result').textContent='Connection failed; no automatic retry.'}b.disabled=false}</script>'''.replace('PROVIDER', origin).replace('NONCE', nonce)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, content_type='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != '/' + nonce:
                return self.send(404, b'{}')
            self.send(200, page.encode(), 'text/html; charset=utf-8')

        def do_POST(self):
            if (self.path != '/' + nonce or self.headers.get('Origin') != local_origin
                    or self.headers.get('X-ALT-CSRF') != nonce
                    or self.headers.get('Content-Type') != 'application/json'):
                return self.send(403, b'{"message":"Request not authorized."}')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 1 or length > 2048:
                    raise ValueError('invalid_request')
                pin = json.loads(self.rfile.read(length)).get('pin')
                if not isinstance(pin, str) or not pin or len(pin) > 128:
                    raise ValueError('invalid_pin')
                session = authenticate(origin, pin)
                pin = None
                sessions_path = private_dir / 'provider-sessions.json'
                sessions = json.loads(sessions_path.read_text()) if sessions_path.exists() else {}
                previous = private_dir / 'provider-session.json'
                if previous.exists():
                    prior = json.loads(previous.read_text())
                    sessions[prior['origin']] = {'sid': prior['sid'], 'exp': prior['exp']}
                sessions[origin] = session
                atomic_write(sessions_path, json.dumps(sessions).encode())
                atomic_write(private_dir / 'provider-session.json', json.dumps({'origin': origin, **session}).encode())
                refs = json.loads((private_dir / 'reference-urls.json').read_text())
                urls = [origin + '/play/' + session['sid'] + '/' + source_id(url) + '.m3u8' for url in refs]
                atomic_write(private_dir / 'renewed-reference-urls.json', json.dumps(urls).encode())
                expiry = dt.datetime.fromtimestamp(session['exp'] / 1000, dt.timezone.utc).isoformat()
                return self.send(200, json.dumps({'message': 'Authorized. Server-reported expiry: ' + expiry + '. PIN was not saved.'}).encode())
            except urllib.error.HTTPError as error:
                message = 'Binged rejected sign-in (HTTP ' + str(error.code) + '). No automatic retries.'
            except Exception:
                message = 'Sign-in could not be verified. No credentials were logged and no automatic retry was made.'
            self.send(400, json.dumps({'message': message}).encode())

    print('Interactive login ready at ' + local_origin + '/' + nonce, flush=True)
    HTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--origin', default='https://sofi.dulo.cc')
    p.add_argument('--private-dir', type=Path, default=Path(__file__).resolve().parent / 'private')
    p.add_argument('--port', type=int, default=8766)
    a = p.parse_args()
    serve(a.origin, a.private_dir, a.port)

"""Small localhost-only JSON client used by MCP; never forwards credentials remotely."""
from __future__ import annotations
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
                or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Use an explicit http://127.0.0.1:PORT origin')
        if len(token) < 32:
            raise ValueError('A dedicated agent credential is required')
        self.url, self.token = url.rstrip('/'), token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, payload=None):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() if payload is not None else None
        if data and len(data) > 65536:
            raise ValueError('JSON body exceeds 64 KiB')
        request = Request(self.url + path, data=data,
                          headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise RuntimeError('Response exceeds the supported 8 MiB limit')
                return json.loads(body)
        except HTTPError as exc:
            if exc.code in (400, 401, 403, 404, 409, 413, 422, 503):
                return json.loads(exc.read(65536))
            raise RuntimeError('Coordination request failed') from None

    def exchange(self, request):
        return self.request('/v1/exchange', request)

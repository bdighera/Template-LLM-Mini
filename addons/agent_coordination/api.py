"""Authenticated localhost HTTP/JSON interface with generated OpenAPI."""
from __future__ import annotations
import json
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from contracts import Operation, SCHEMA
from service import Fault, Service


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > 65536:
                return await JSONResponse({'error': {'code': 'body_limit', 'message': 'JSON body exceeds 64 KiB'}}, status_code=413)(scope, receive, send)
            chunks.append(chunk)
            if not message.get('more_body'):
                break
        body = b''.join(chunks)
        if body:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('Duplicate key')
                    result[key] = value
                return result
            def invalid_constant(value):
                raise ValueError('Non-finite number')
            try:
                json.loads(body, object_pairs_hook=unique, parse_constant=invalid_constant)
            except (ValueError, UnicodeError):
                return await JSONResponse({'error': {'code': 'schema', 'message': 'Strict JSON required'}}, status_code=422)(scope, receive, send)
        supplied = False
        async def replay():
            nonlocal supplied
            if not supplied:
                supplied = True
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()
        await self.app(scope, replay, send)


def create_app(service: Service):
    app = FastAPI(title='Agent coordination', version='1.0.0', openapi_url=None, docs_url=None, redoc_url=None)
    app.add_middleware(BodyLimit)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])
    bearer = HTTPBearer(auto_error=False)

    def credential(value: HTTPAuthorizationCredentials | None = Depends(bearer)):
        token = value.credentials if value and value.scheme.lower() == 'bearer' else ''
        service.authenticate(token)
        return token

    @app.exception_handler(Fault)
    async def fault_handler(request, exc):
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=exc.status)

    @app.exception_handler(HTTPException)
    async def http_handler(request, exc):
        return JSONResponse({'error': {'code': 'http_error', 'message': 'Request rejected'}}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Never echo the supplied input (it may contain a credential).
        return JSONResponse({'error': {'code': 'schema', 'message': 'Request does not match the versioned schema',
                                       'fields': [list(e['loc']) for e in exc.errors()]}}, status_code=422)

    @app.post('/v1/exchange')
    def exchange(operation: Operation, token: str = Depends(credential)):
        return service.exchange(token, operation.model_dump())

    @app.get('/v1/schema')
    def schema(token: str = Depends(credential)):
        return SCHEMA

    @app.get('/v1/openapi.json')
    def openapi(token: str = Depends(credential)):
        return app.openapi()

    return app

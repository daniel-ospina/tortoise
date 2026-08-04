"""Middleware stack for Tortoise Hosted API.

Phase 1a: Request-ID → Auth → Handler → Audit
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Callable

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

_logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers['X-Request-ID'] = request_id
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    SKIP_PATHS = {'/health', '/docs', '/openapi.json'}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.SKIP_PATHS or request.url.path.startswith('/internal'):
            return await call_next(request)
        try:
            from tortoise.auth import hash_api_key
            from tortoise.sdk import TortoiseSDK
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                raise HTTPException(status_code=401, detail='Missing Authorization header')
            token = auth_header[7:]
            if not token.startswith('tt_'):
                raise HTTPException(status_code=401, detail='Invalid API key format')
            sdk = TortoiseSDK(namespace='registry')
            key_result = sdk._get_proj().g.query(
                'MATCH (k:APIKey {key_hash: $hash}) WHERE k.revoked_at IS NULL RETURN k.team_id, k.id',
                params={'hash': hash_api_key(token)},
            )
            if not key_result.result_set:
                raise HTTPException(status_code=401, detail='Invalid API key')
            team_id, key_id = key_result.result_set[0]
            team = sdk._get_proj().g.query(
                'MATCH (t:Team {id: $id}) RETURN t.tier, t.max_users, t.max_graphs, t.max_teams',
                params={'id': team_id},
            )
            if not team.result_set:
                raise HTTPException(status_code=401, detail='Team not found')
            tier, max_users, max_graphs, max_teams = team.result_set[0]
            request.state.team_id = team_id
            request.state.key_id = key_id
            request.state.tier = tier or 'free'
            request.state.max_users = max_users or 1
            request.state.max_graphs = max_graphs or 1
            request.state.max_teams = max_teams or 1
            sdk._get_proj().g.query(
                'MATCH (k:APIKey {id: $id}) SET k.last_used_at = datetime()',
                params={'id': key_id},
            )
        except HTTPException:
            raise
        except Exception:
            _logger.exception('Auth middleware error')
            raise HTTPException(status_code=500, detail='Authentication error')
        return await call_next(request)


async def get_current_team(request: Request) -> dict:
    team_id = getattr(request.state, 'team_id', None)
    if team_id is None:
        raise HTTPException(status_code=401, detail='Not authenticated')
    return {
        'team_id': team_id,
        'key_id': getattr(request.state, 'key_id', None),
        'tier': getattr(request.state, 'tier', 'free'),
        'max_users': getattr(request.state, 'max_users', 1),
        'max_graphs': getattr(request.state, 'max_graphs', 1),
        'max_teams': getattr(request.state, 'max_teams', 1),
    }

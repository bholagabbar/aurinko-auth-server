from __future__ import annotations

"""Aurinko Auth Server - Open source OAuth server for email authentication using Aurinko.

This FastAPI micro-service implements the 2-hop OAuth flow with Aurinko for email connectivity.

1. /auth/init     - Kick off OAuth flow.
2. /auth/relay    - Google → relay (Hop 1). Forwards untouched query string to Aurinko.
3. /auth/callback - Aurinko → service (Hop 2). Swaps code→token, persists and notifies.
4. /email/connected - Test endpoint for OAuth completion (logs params and confirms success).
"""

import os
import base64
import uuid
import json
import logging
from typing import Dict, Any

import httpx
import asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv
from urllib.parse import quote
from hypercorn.asyncio import serve
from hypercorn.config import Config
import redis

# ---------------------------------------------------------------------------
# Configuration and logging setup
# ---------------------------------------------------------------------------

load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Redis client
redis_client = None

def get_redis_client():
    global redis_client
    if redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = redis.from_url(redis_url, decode_responses=True)
    return redis_client

# Default scopes for email access
# NOTE: Set your required Gmail scopes here
# Available scopes: https://developers.google.com/gmail/api/auth/scopes
DEFAULT_SCOPES = [
    "Mail.Read",
    "Mail.Send",
]

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def get_aurinko_config() -> Dict[str, Any]:
    """Get Aurinko configuration from environment variables."""
    client_id = os.getenv("AURINKO_CLIENT_ID")
    client_secret = os.getenv("AURINKO_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        logger.error("Missing AURINKO_CLIENT_ID or AURINKO_CLIENT_SECRET")
        raise HTTPException(status_code=500, detail="Server misconfiguration")
    
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": DEFAULT_SCOPES,
    }


app = FastAPI(
    title="Aurinko Auth Server",
    description="Open source OAuth server for email authentication using Aurinko",
    version="0.1.0",
)


###############################################################################
# Internal helpers
###############################################################################


def save_oauth_state(state: str, payload: Dict[str, Any], ttl: int = 600):
    """Persist CSRF state in Redis for 10 minutes."""
    try:
        client = get_redis_client()
        client.setex(state, ttl, json.dumps(payload, separators=(",", ":")))
        logger.debug(f"Saved OAuth state: {state}")
    except Exception as e:
        logger.error(f"Failed to save state in Redis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


def load_oauth_state(state: str) -> Dict[str, Any]:
    """Load OAuth state from Redis."""
    try:
        client = get_redis_client()
        raw = client.get(state)
        if not raw:
            logger.warning(f"OAuth state not found: {state}")
            raise KeyError("state not found or expired")
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in OAuth state {state}: {e}")
        raise HTTPException(status_code=400, detail="Corrupted state")
    except Exception as e:
        logger.warning(f"OAuth state lookup failed for {state}: {e}")
        raise HTTPException(status_code=400, detail="Invalid state")


def exchange_code_for_token(code: str) -> Dict[str, Any]:
    """Exchange authorization code for access token with Aurinko."""
    cfg = get_aurinko_config()
    basic = base64.b64encode(f"{cfg['client_id']}:{cfg['client_secret']}".encode()).decode()
    url = f"https://api.aurinko.io/v1/auth/token/{code}"
    headers = {"Authorization": f"Basic {basic}"}
    
    try:
        logger.info("Exchanging code for token at Aurinko...")
        resp = httpx.post(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        logger.error(f"Token exchange failed: {e}")
        raise HTTPException(status_code=502, detail="Token exchange with Aurinko failed")


###############################################################################
# Endpoints
###############################################################################


@app.get("/health")
async def health() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/auth/init")
async def init_oauth(request: Request, userId: str):
    """Initialize OAuth flow for a user."""
    try:
        cfg = get_aurinko_config()
        state = str(uuid.uuid4())
        
        # Store state for CSRF protection
        save_oauth_state(state, {"userId": userId})
        
        # Determine base URL
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.url.netloc)
        base_url = os.getenv("BASE_URL", f"{scheme}://{host}")
        
        # Prepare OAuth parameters
        client_id = cfg['client_id']
        service_type = "Google"
        scopes = quote(' '.join(cfg['scopes']))
        response_type = "code"
        return_url = quote(f'{base_url}/auth/callback')
        
        # Debug log
        logger.info(f"OAuth init for user={userId}, base_url={base_url}")
        
        # Construct authorize URL
        authorize_url = (
            "https://api.aurinko.io/v1/auth/authorize"
            f"?clientId={client_id}"
            f"&serviceType={service_type}"
            f"&scopes={scopes}"
            f"&responseType={response_type}"
            f"&returnUrl={return_url}"
            f"&state={state}"
        )
        
        return RedirectResponse(authorize_url)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in init_oauth: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/auth/relay")
async def relay_callback(request: Request):
    """Relay endpoint that forwards the query string to Aurinko callback."""
    query = request.url.query
    forward_url = f"https://api.aurinko.io/v1/auth/callback?{query}"
    logger.debug(f"Relay forward: {forward_url}")
    return RedirectResponse(forward_url)


@app.get("/auth/callback")
async def oauth_callback(request: Request, code: str, state: str):
    """Handle OAuth callback from Aurinko - exchange code and persist token."""
    try:
        # Load and validate state
        stored = load_oauth_state(state)
        user_id = stored.get("userId")
        if not user_id:
            logger.error("No userId in state")
            raise HTTPException(status_code=400, detail="Invalid state data")
        
        logger.info(f"OAuth callback for user={user_id}, code={code[:8]}...")
        
        # Exchange code for token
        token_res = exchange_code_for_token(code)
        
        # Persist token
        persist_token(user_id, token_res)
        
        # Notify webhook if configured
        notify_webhook(user_id)
        
        # Redirect to success URL
        success_url = os.getenv("OAUTH_SUCCESS_URL", f"{request.base_url}auth/success")
        logger.info(f"OAuth complete: redirecting user={user_id} to {success_url}")
        return RedirectResponse(success_url)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in oauth_callback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/auth/success")
async def auth_success():
    """Default success page after OAuth completion."""
    return {
        "status": "success",
        "message": "Email authentication completed successfully!",
    }


@app.get("/email/connected")
async def test_callback(request: Request):
    """Test endpoint to receive OAuth flow completion for testing purposes."""
    params = dict(request.query_params)
    logger.info(f"Test callback received - Query params: {params}")
    logger.info(f"Full URL: {request.url}")
    logger.info("OAuth flow successfully completed!")
    return {
        "status": "success",
        "message": "OAuth flow completed successfully",
        "received_params": params,
        "timestamp": asyncio.get_event_loop().time()
    }


###############################################################################
# Persistence and notifications
###############################################################################


def persist_token(user_id: str, token_res: Dict[str, Any]):
    """Persist access token in Redis."""
    try:
        client = get_redis_client()
        key = f"email-token:{user_id}"
        client.set(key, json.dumps(token_res, separators=(",", ":")))
        logger.info(f"Token persisted for user={user_id}")
    except Exception as e:
        logger.error(f"Failed to persist token for user {user_id}: {e}", exc_info=True)
        raise


def notify_webhook(user_id: str):
    """Send webhook notification if configured."""
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        return
    
    try:
        resp = httpx.post(webhook_url, json={"userId": user_id}, timeout=10)
        resp.raise_for_status()
        logger.info(f"Webhook notification sent for user {user_id}")
    except httpx.HTTPError as e:
        logger.error(f"Failed to send webhook notification: {e}")


###############################################################################
# Main entry point
###############################################################################


def main():
    """Run the server using Hypercorn."""
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    bind_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    
    config = Config()
    config.bind = [f"{bind_host}:{port}"]
    config.accesslog = "-"
    
    logger.info(f"Starting Aurinko Auth Server on {bind_host}:{port}")
    asyncio.run(serve(app, config))


if __name__ == "__main__":
    main()

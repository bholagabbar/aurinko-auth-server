# Aurinko Auth Server

Open source OAuth server for Gmail authentication using [Aurinko](https://aurinko.io). Handles the 2-hop OAuth flow, token exchange, and stores tokens in Redis.

## Deploy Directly with Docker

Just set your Aurinko credentials and you're ready to go:

```bash
# Create .env file with your credentials
echo "AURINKO_CLIENT_ID=your_client_id" >> .env
echo "AURINKO_CLIENT_SECRET=your_secret" >> .env

# Run with docker-compose (it reads from .env automatically)
docker-compose up -d
```

Test the OAuth flow by visiting `http://localhost:8000/auth/init?userId=123` and following the flow.

Ensure your callback and redirect URLs are correct. We use Redis for persistence. Refer to the end of this README for more explanation.


## Build & Run Locally

### Prerequisites
- [uv](https://github.com/astral-sh/uv) (for local development)
- Docker & Docker Compose (for deployment)
- Aurinko account with Google OAuth configured
- Google Cloud Console OAuth app

1. Clone and configure:
```bash
git clone https://github.com/yourusername/aurinko-auth-server.git
cd aurinko-auth-server

# Create .env file
echo "AURINKO_CLIENT_ID=your_client_id" >> .env
echo "AURINKO_CLIENT_SECRET=your_secret" >> .env
```

2. Install dependencies and run:
```bash
uv sync
uv run main.py
```

Server runs at `http://localhost:8000`

## Environment Variables

```env
AURINKO_CLIENT_ID=your_aurinko_client_id
AURINKO_CLIENT_SECRET=your_aurinko_client_secret
REDIS_URL=redis://localhost:6379
WEBHOOK_URL=https://your-app.com/webhook  # Optional: POST {userId} on success
OAUTH_SUCCESS_URL=https://your-app.com/success  # Optional: where to redirect after OAuth
```

## API Endpoints

- `GET /auth/init?userId=123` - Start OAuth flow
- `GET /auth/relay` - Google callback (set as redirect URI in Google)
- `GET /auth/callback` - Aurinko callback (set in Aurinko settings)
- `GET /health` - Health check
- `GET /email/connected` - Test endpoint for OAuth completion (logs params and confirms success)

## Setup Links & Explanation

### OAuth Flow URLs
* **Google Authorized Redirect URI** (configure in Google Cloud Console): `http://localhost:8000/auth/relay`
* **Aurinko Callback URL** (configure in Aurinko app settings): `http://localhost:8000/auth/callback`

**Note:** For testing, you can set `OAUTH_SUCCESS_URL=http://localhost:8000/email/connected` to use the built-in test endpoint that logs the OAuth completion details.

### Important Links
* [Google Cloud Console](https://console.cloud.google.com/) - Create OAuth credentials
* [Aurinko Dashboard](https://app.aurinko.io/) - Manage your Aurinko applications  
* [Aurinko Google OAuth Setup Guide](https://docs.aurinko.io/authentication/google-oauth-setup)
* [Aurinko OAuth Flow Documentation](https://docs.aurinko.io/authentication/oauth-flow/account-oauth-flow)

## Token Retrieval

Tokens are stored in Redis as `email-token:{userId}`:

```bash
# Quick CLI check for a user's token
redis-cli get "email-token:123"
```

## License

MIT 
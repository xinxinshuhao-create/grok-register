# grok-register

Automated account registration toolkit for x.ai (Grok) with SSO token extraction, OAuth Device Flow minting, and auto-replenish daemon for API gateway integration.

## Features

- **Account registration** (`grok.py`) — curl_cffi-based engine, supports YesCaptcha for Turnstile solving
- **SSO → CPA token minting** (`sso_to_cpa.py`) — OAuth Device Flow with corrected scope, converts SSO tokens to access/refresh tokens
- **Auto-replenish daemon** (`auto_replenish.py`) — monitors account pool, registers new accounts on demand, pushes to API gateway
- **Token refresh daemon** (`token_daemon.py`) — keeps tokens alive
- **Turnstile solver** (`turnstile_solver_local.py`) — local CAPTCHA solving service
- **Email service** (`email_service.py`) — multi-provider support (LuckMail, MailNest)
- **Clash proxy rotator** (`clash_rotator.py`) — proxy rotation for registration

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [YesCaptcha](https://yescaptcha.com/) API key (for Turnstile solving)
- Email provider account (LuckMail / MailNest)
- A running [grok2api](https://github.com/chenyme/grok2api) instance (for auto-replenish integration)

## Quick Start

```bash
# Clone
git clone https://github.com/xinxinshuhao-create/grok-register.git
cd grok-register

# Install dependencies
uv sync

# Configure
cp .env.example .env
# Edit .env with your API keys

# Create output directory
mkdir -p keys

# Run registration
uv run python grok.py
```

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `YESCAPTCHA_KEY` | Yes | YesCaptcha API key for Turnstile solving |
| `EMAIL_PROVIDER` | No | Email provider: `luckmail` / `mailnest` / `gptmail` (default: `gptmail`) |
| `LUCKMAIL_API_KEY` | If luckmail | LuckMail API key |
| `LUCKMAIL_PROJECT_CODE` | No | LuckMail project code (default: `grok`) |
| `LUCKMAIL_EMAIL_TYPE` | No | Email type (default: `ms_imap`) |
| `LUCKMAIL_DOMAIN` | No | Email domain (default: `outlook.com`) |
| `THREADS` | No | Concurrent registration threads (default: 1) |
| `GROK2API_BASE` | No | API gateway URL for auto-replenish (default: `http://127.0.0.1:8000`) |
| `GROK2API_USER` | No | API gateway admin user (default: `admin`) |
| `GROK2API_PASS` | No | API gateway admin password |
| `GROK_PROXY` | No | HTTP proxy for registration (default: `http://127.0.0.1:7897`) |

## Usage

### Register accounts

```bash
# Basic
uv run python grok.py

# With luckmail provider and 8 threads
uv run python grok.py --email-provider luckmail --threads 8
```

Output:
- `keys/grok.txt` — SSO token list
- `keys/accounts.txt` — `email:password:sso` format

### Mint CPA tokens from SSO

```bash
uv run python sso_to_cpa.py --all
```

Converts SSO tokens to OAuth access/refresh tokens via Device Flow.

### Run auto-replenish daemon

```bash
uv run python auto_replenish.py --daemon 600 --min 2
```

Monitors account pool every 600s, registers new accounts when pool drops below 2.

### Run token refresh daemon

```bash
uv run python token_daemon.py
```

### Start Turnstile solver

```bash
uv run python turnstile_solver_local.py
```

Local HTTP service for CAPTCHA solving.

## Supported Models

Registered accounts and minted tokens can be used with [grok2api](https://github.com/chenyme/grok2api) to access the following models.

### Free (Basic-tier accounts, no payment required)

These are the models you get immediately after registration — no SuperGrok subscription needed:

| Model | Capability | How to get |
|---|---|---|
| `grok-chat-fast` | Chat (fast mode) | SSO token → Web pool |
| `grok-imagine-image` | Image generation | SSO token → Web pool |
| `grok-4.5` | Chat + reasoning + search, 1M output tokens | SSO → Device Flow (`sso_to_cpa.py`) → Build pool |

> ✅ All three models above have been tested and confirmed working end-to-end.

### Paid (requires SuperGrok / Heavy subscription)

The following models are available in the codebase but require a paid account tier:

| Model | Capability | Tier |
|---|---|---|
| `grok-chat-auto` | Chat (auto mode) | Super |
| `grok-chat-expert` | Chat (expert mode) | Super |
| `grok-chat-heavy` | Chat (heavy mode) | Heavy |
| `grok-imagine-image-quality` | Image generation (HD) | Super |
| `grok-imagine-image-edit` | Image editing | Super |
| `grok-imagine-video` | Video generation | Super |

Other Build/Console models available via Device Flow: `grok-4.3`, `grok-4.20-0309-reasoning`, `grok-4.20-0309-non-reasoning`, `grok-4.20-multi-agent-0309`, `grok-build-0.1` (code/composer, 256K output).

## OAuth Device Flow

The `sso_to_cpa.py` script implements OAuth 2.0 Device Flow with the corrected scope:

```
openid profile email offline_access grok-cli:access api:access
```

This was validated against the upstream OAuth endpoint and successfully mints access/refresh tokens.

## Acknowledgments

This project builds upon and references work from:
- [AaronL725/grok-register](https://github.com/AaronL725/grok-register)
- [kaibush/grok-register](https://github.com/kaibush/grok-register)

## License

MIT

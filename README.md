# grok-register

Automated account registration toolkit for x.ai (Grok) with SSO token extraction, OAuth Device Flow minting, and auto-replenish daemon for API gateway integration.

## Features

- **Account registration** (`grok.py`) — curl_cffi-based engine, supports YesCaptcha for Turnstile solving
- **SSO → CPA token minting** (`sso_to_cpa.py`) — OAuth Device Flow with corrected scope, converts SSO tokens to access/refresh tokens
- **Auto-replenish daemon** (`auto_replenish.py`) — monitors account pool, registers new accounts on demand, pushes to API gateway
- **Token refresh daemon** (`token_daemon.py`) — keeps tokens alive
- **OAuth token re-minting** (`remint_oauth.py`) — re-mints revoked tokens via Device Flow when xAI invalidates refresh tokens
- **Turnstile solver** (`turnstile_solver_local.py`) — local CAPTCHA solving service
- **Email service** (`email_service.py`) — multi-provider support (LuckMail, MailNest)
- **Clash proxy rotator** (`clash_rotator.py`) — proxy rotation for registration

## Prerequisites

- Python 3.12+
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
| `EMAIL_PROVIDER` | No | Email provider: `luckmail` / `mailnest` / `gptmail` / `tmail` / `fce` / `gmail` (default: `luckmail`) |
| `LUCKMAIL_API_KEY` | If luckmail | LuckMail API key |
| `LUCKMAIL_PROJECT_CODE` | No | LuckMail project code (default: `grok`) |
| `LUCKMAIL_EMAIL_TYPE` | No | Email type (default: `ms_imap`) |
| `LUCKMAIL_DOMAIN` | No | Email domain (default: `outlook.com`) |
| `THREADS` | No | Concurrent registration threads (default: 1) |
| `GROK2API_BASE` | No | API gateway URL for auto-replenish (default: `http://127.0.0.1:8000`) |
| `GROK2API_USER` | No | API gateway admin user (default: `admin`) |
| `GROK2API_PASS` | No | API gateway admin password |
| `GROK_PROXY` | No | HTTP proxy for registration (default: `http://127.0.0.1:7897`) |

### Email providers (email domains matter for xAI)

| Provider | Cost | Email domain | Headed Chrome | Notes |
|---|---|---|---|---|
| `luckmail` (default) | Paid (¥0.02/inbox) | Outlook.com addresses | No | Verified for Grok codes (76-99s) |
| `gmail` | Free | Your own Gmail (+alias) | No | Highest trust; set `GMAIL_BASE_EMAIL` + `GMAIL_APP_PASSWORD` |
| `fce` | Free | Platform domains: `@ditapi.info`, `@fce.email` | No | Pure REST API; set `FCE_API_KEY`. **Gmail/Outlook addresses are NOT accepted as inboxes; custom domains require paid plans ($29/mo+)**. Free tier has rate limits; OTP endpoint returns `__DETECTED__` on free tier — codes are parsed from messages instead |
| `tmail` | Free | Shared eu.org domains | Yes | Anonymous, no key |
| `gptmail` | Free | Shared disposable domains | Yes | xAI does not deliver codes to these domains (use for other platforms) |
| `outlook` | Free | Your own Outlook/Hotmail (+tag alias) | No | High trust (personal domain). Set `OUTLOOK_ACCOUNTS` + `OUTLOOK_TOKENS_FILE`; requires one-time Microsoft OAuth authorization per account (see below) |
| `mailnest` | Paid | Outlook-based | No | Set `MAILNEST_API_KEY` + `MAILNEST_PROJECT_CODE` |

> ⚠️ xAI actively blocks shared disposable-mail domains (mail.tm, gptmail domains). For Grok
> registration, prefer `luckmail` (Outlook addresses) or `gmail`/`outlook` (your own accounts).
> **Maintainer's production setup uses `luckmail`.** The free providers above are provided as
> options for other users; their availability may change without notice.

### Outlook provider authorization (one-time per account)

The `outlook` provider reads codes via Microsoft OAuth (XOAUTH2 IMAP). Authorize each account once:

```bash
# uses the same authorization flow as unified-mail's graph_auth.py
uv run python outlook_auth.py your@outlook.com
```

The resulting refresh token is stored in `OUTLOOK_TOKENS_FILE` (default `./outlook_tokens.json`),
format: `{"your@outlook.com": {"refresh_token": "..."}}`. Only accounts with a valid refresh
token are used for registration.

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

### Re-mint revoked OAuth tokens

When xAI invalidates refresh tokens (happens on model releases or account policy changes), re-mint them:

```bash
uv run python remint_oauth.py
```

Re-runs Device Flow with existing SSO tokens to obtain fresh access/refresh tokens.

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
| `grok-4.5` | Chat + reasoning + search, 1M output tokens | SSO → Device Flow (`sso_to_cpa.py`) → OAuth direct |
| `grok-4.6` | Chat + reasoning + search, 500K context, long-running agents, `xhigh` reasoning | SSO → Device Flow (`sso_to_cpa.py`) → Build pool |

> ✅ All four models above have been tested and confirmed working end-to-end.
>
> **Note**: After the Grok 4.6 release, xAI removed `grok-4.5` from the Build pool — it now works via OAuth direct connection. `grok-4.6` is the current Build pool model. Use `remint_oauth.py` to re-mint tokens if xAI revokes them.

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

Other Build/Console models are available via Device Flow: `grok-4.3`, `grok-4.20-0309-reasoning`, `grok-4.20-0309-non-reasoning`, `grok-4.20-multi-agent-0309`, `grok-build-0.1` (code/composer, 256K output).

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

## ⚠️ Disclaimer / 免责声明

**本工具仅用于教育和技术研究目的。使用者须自行承担全部责任。**

- 本项目提供的账号注册与临时邮箱方案**可能违反相关平台的《服务条款》**（包括但不限于 xAI/Grok、Google、Microsoft 等），平台有权随时封禁账号、撤销凭据或追究责任。
- **在部分国家和地区，批量注册账号、使用临时邮箱、绕过验证机制等行为可能违反当地法律法规**。使用者有责任确认并遵守所在地法律，本项目作者不承担任何因使用本工具导致的直接或间接后果（包括但不限于账号损失、法律纠纷、经济损失）。
- 项目内各邮箱服务（LuckMail、FreeCustom.Email、Tmail、GPTMail 等）为第三方服务，其可用性、价格与合规性以其官方为准；本仓库不对第三方服务的任何行为负责。
- 示例中出现的账号、令牌均为占位或已撤销；请勿将任何真实凭据提交到本仓库或任何公开位置。
- 使用本仓库代码即表示你已阅读并同意上述条款。

**This project is for educational and research purposes only. Users assume all responsibility.**

- Account registration and temporary-email tooling may **violate the Terms of Service of the target platforms** (xAI/Grok, Google, Microsoft, etc.). Platforms may ban accounts, revoke credentials, or pursue other actions at any time.
- **In some jurisdictions, bulk account registration, disposable-email usage, or bypassing verification mechanisms may be illegal.** You are solely responsible for ensuring compliance with your local laws and regulations. The authors accept no liability for any direct or indirect consequences (including account loss, legal disputes, or financial damages).
- All third-party email services (LuckMail, FreeCustom.Email, Tmail, GPTMail, etc.) are provided by their respective operators. Availability, pricing, and compliance are subject to their official terms; this repository is not responsible for their actions.
- Sample accounts and tokens shown in this repository are placeholders or revoked. Never commit real credentials here or anywhere public.
- By using this code, you confirm that you have read and agree to the above.

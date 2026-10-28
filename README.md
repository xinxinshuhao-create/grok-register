# grok-register

Automated account registration toolkit for x.ai (Grok) with SSO token extraction, OAuth Device Flow minting, and auto-replenish daemon for API gateway integration.

## Features

- **Account registration** (`grok.py`) — curl_cffi-based engine, supports YesCaptcha for Turnstile solving
- **SSO → CPA token minting** (`sso_to_cpa.py`) — OAuth **PKCE** flow (with consent-form submit), converts SSO tokens to access/refresh tokens
- **Device Flow minting** (`device_mint.py`) — OAuth 2.0 Device Authorization Grant, the fallback path used by the re-mint helper when PKCE is blocked by Cloudflare
- **Auto-replenish daemon** (`auto_replenish.py`) — monitors account pool, registers new accounts on demand, pushes to API gateway
- **Token refresh daemon** (`token_daemon.py`) — keeps tokens alive
- **OAuth token re-minting** (`remint_oauth.py`) — re-mints revoked tokens when xAI invalidates them; delegates to `device_mint.py` (Device Flow)
- **Turnstile solver** (`turnstile_solver_local.py`) — local CAPTCHA solving service (Patchright)
- **CloakBrowser solver** (`cloakbrowser_solver.py`) — stealth-Chromium CAPTCHA solving service (CloakBrowser)
- **Email service** (`email_service.py`) — multi-provider support (LuckMail, MailNest)
- **Clash proxy rotator** (`clash_rotator.py`) — ⚠️ **no longer wired into the pipeline** (rotation removed 2026-09-18; registration/minting now use a single static proxy via `GROK_PROXY`). File retained for reference only.

## Architecture

> Deliberately abstract: this section explains **why the system is shaped this way**.
> Host addresses, node identifiers, proxy providers and pool sizes are intentionally
> omitted — see the source for operational detail.

### Separation of concerns

This repository owns **account acquisition and lifecycle**. A gateway
([grok2api](https://github.com/chenyme/grok2api)) owns **routing and load-balancing**,
and may itself sit behind a multi-channel proxy layer. The handoff between the two is a
directory of credential files: this repo writes them, the gateway imports them.

Keeping those apart means the acquisition logic never needs to know about request
routing, and the gateway never needs to know how an account was obtained.

### The two pools

Accounts land in one of two pools, and they are **not interchangeable**:

| Pool | Credential path | Serves | Notes |
|---|---|---|---|
| **Web** | SSO pushed directly | Chat + image models | No OAuth conversion required |
| **Build** | SSO → OAuth → token | Frontier reasoning models | Needs a successful OAuth exchange |

The split exists because the two upstream surfaces authenticate differently and expose
different capabilities. The practical consequence is isolation: a failure in the OAuth
path degrades the Build pool without touching the Web pool, and vice versa.

### Pipeline

```
  acquire                      mint                       pool
  ───────                      ────                       ────
  ┌────────────────────┐   ┌──────────────────────┐   ┌──────────────────┐
  │ registration engine│   │ OAuth exchange       │   │ gateway:         │
  │  · solver-backed   │──▶│  · PKCE (primary)    │──▶│   Build pool     │
  │  · browser-backed  │   │  · Device (fallback) │   └──────────────────┘
  └────────────────────┘   └──────────────────────┘
            │                                                  ▲
            │  SSO — usable as-is, no OAuth needed             │
            └──────────────────────────────────────────────────┘
                    direct push → Web pool  (+ egress binding)

  ┌──────────────────────────────────────────────────────────┐
  │ replenisher: counts both pools → decides whether to act   │
  │ token daemon: refreshes before expiry                     │
  │ re-mint: rebuilds credentials whose refresh token died    │
  └──────────────────────────────────────────────────────────┘
```

### Design decisions and tradeoffs

**Fail-soft ordering — the cheap path runs first.**
The pipeline pushes the Web pool before attempting OAuth. The Web push only needs the
SSO token, which registration already produced; the Build push depends on an OAuth
exchange that can fail. Ordering it this way means a partial failure still yields usable
accounts instead of nothing. The cost is that a silently failing OAuth step leaves the
Build pool lagging while the Web pool looks healthy — the two counts must be read
separately to notice.

**Two registration engines — a cost/reliability dial.**
A solver-backed engine (paid CAPTCHA API, HTTP-only) is the default because it has the
higher success rate and runs faster. A browser-backed engine (headless-capable stealth
browser, solves the challenge in-page) exists as a free fallback. Neither is strictly
better: the paid path costs money per attempt, the browser path is several times slower
and needs a real rendering environment. The choice is exposed as a flag rather than
hard-coded, so the operator can trade cost against reliability per situation.

**Two OAuth flows — one is fragile, the other is heavier.**
The PKCE flow is lighter but was blocked by the upstream bot filter, so a Device
Authorization Grant flow was added as the primary path: it drives a real browser session
through the consent screen. Maintaining two flows is more code, but the alternative —
depending on the one the filter blocks — was a hard outage. The selection falls back
automatically when the preferred module is unavailable.

**Egress binding is a hard precondition, not an optimisation.**
A Web-pool account is only counted as *available* once it has an egress node assigned.
An imported-but-unbound account exists in the database yet is invisible to the
replenisher, so it will never be used and never be counted — the pool looks smaller than
it is. Binding is therefore part of the import path, not a separate maintenance step.

**Thresholds carry headroom, and batches are capped.**
The replenisher triggers on the *worse* of the two pools rather than either one, so a
healthy pool cannot mask a starving one. It then registers one more than the shortfall —
attrition between checks is expected — and caps the batch size regardless of how large
the shortfall is. The cap is deliberate: registering in bulk is exactly the pattern
upstream anti-abuse systems look for.

**Two-tier token recovery.**
Expiring credentials are refreshed in place before they lapse; credentials whose
*refresh* token has been revoked cannot be refreshed at all and are re-minted from the
original SSO. The second path is strictly more expensive, which is why refresh runs
continuously and re-minting is an explicit operation.

**Imports are sequential.**
Batch uploads were measured to fail far more often than one-at-a-time uploads against
the same endpoint, so the pipeline trades round-trips for reliability.

### Removed by choice: proxy rotation

An earlier version rotated the outbound proxy between registrations. It was removed
because rotation depended on a separate proxy controller and its subscription groups —
infrastructure with no relationship to account registration — making the pipeline
sensitive to failures in a system it did not own. Registration and minting now share one
static outbound proxy.

The tradeoff is real and accepted: less IP diversity during registration means more
exposure to anti-abuse heuristics. It was judged worth removing a whole class of
unrelated failure modes.

### What this repository does not do

- **No request routing.** It does not proxy inference traffic; the gateway does.
- **No load balancing.** Choosing which account serves a request is the gateway's job.
- **No model-name logic.** Model availability is discovered by the gateway at runtime,
  so a new upstream model needs no change here.


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
| `DEVICE_PROXY` | No | Proxy used by `device_mint.py` (falls back to `GROK_PROXY`) |
| `CPA_AUTHS_DIR` | No | Output dir for minted CPA auth files (default: `D:\CLIProxyAPIPlus\auths`) |
| `GROK_MAX_ATTEMPTS` | No | Per-thread attempt cap; **each attempt buys one mailbox**. Default `20`. Set `0` to disable the cap (pre-2026-09-27 behaviour — dangerous with paid providers: an unbounded retry loop once burned 250 paid LuckMail inboxes in 43 minutes) |

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
> registration prefer `luckmail` (Outlook addresses) or `gmail`/`outlook` (your own accounts).
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

Local HTTP service for CAPTCHA solving (Patchright + system Chrome).

### Start CloakBrowser solver (stealth)

```bash
# Install CloakBrowser (once)
pip install cloakbrowser
cloakbrowser install

# Start the HTTP service
uv run python cloakbrowser_solver.py
```

Uses CloakBrowser's stealth Chromium to pass Cloudflare Turnstile without a paid API.
Set `GROK_PROXY` env var to route through a proxy (optional).

### One-shot solve with CloakBrowser

```bash
uv run python cloakbrowser_solver.py --once --url https://example.com --key YOUR_SITE_KEY
```

## Supported Models

Registered accounts and minted tokens can be used with [grok2api](https://github.com/chenyme/grok2api) to access the following models.

### Free (Basic-tier accounts, no payment required)

These are the models you get immediately after registration — no SuperGrok subscription needed:

| Model | Capability | How to get |
|---|---|---|
| `grok-chat-fast` | Chat (fast mode) | SSO token → Web pool |
| `grok-imagine-image` | Image generation (lite) | SSO token → Web pool |
| `grok-4.5` | Chat + reasoning + search, 1M output tokens | SSO → Device Flow (`sso_to_cpa.py`) → Build pool |
| `grok-4.6` | Chat + reasoning + search, 500K context, long-running agents, `xhigh` reasoning | SSO → Device Flow (`sso_to_cpa.py`) → Build pool |
| `grok-4.7` | Chat + reasoning + search, 500K context, `xhigh` reasoning, 2.1T params (released 2026-09-21) | SSO → Device Flow (`sso_to_cpa.py`) → Build pool |

> ✅ Models above verified working end-to-end as of 2026-09-22 (Build pool: `grok-4.7` / `grok-4.6` / `grok-4.5`).
>
> **Note**: After the Grok 4.6 release, xAI temporarily removed `grok-4.5` from the Build pool (8/14), but it was **reinstated** via the CPA auths import API (2026-09-05). `grok-4.7` was added on 2026-09-21 and needs no client-side upgrade — grok2api discovers the model catalog from upstream at runtime. Use `remint_oauth.py` or the grok2api admin import API to re-mint tokens if xAI revokes them.
>
> **Image generation** (re-verified 2026-09-22): `grok-imagine-image-lite`, `grok-imagine-image` and `grok-imagine-image-2.0` all return real JPEGs (HTTP 200, `ffd8ff` magic). **`grok-imagine-image-quality-lite` no longer exists — the model id returns HTTP 404 `model_not_found`.** Image editing / HD / video require a Super subscription. Output is portrait ~2:3 (832×1248) with basic prompt following.

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

## OAuth flows

Two flows are implemented; pick based on whether Cloudflare blocks the consent page:

| Flow | Script | Mechanism | When to use |
|---|---|---|---|
| **PKCE** (primary) | `sso_to_cpa.py` | `response_type=code` + `code_challenge` (S256), submits the OAuth consent form via `curl_cffi` | Default path |
| **Device Authorization Grant** (fallback) | `device_mint.py` | `urn:ietf:params:oauth:grant-type:device_code` with headed Chrome to auto-approve | When PKCE is CF-blocked |

`remint_oauth.py` re-mints revoked tokens through the Device Flow path (`device_mint.sso_to_device`).

Both flows request the corrected scope:

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

"""
邮箱服务类
对外保持接口不变：
- create_email() -> (token_like, email)
- fetch_first_email(token_like) -> str | None

支持 provider:
- gptmail（默认，GPTMail 免费 API）
- mailtm（免费，mail.tm API，无需 Key，推荐）
- luckmail（购买邮箱 + token 轮询）
- mailnest（MailNest 付费 API）
"""

import json
import os
import re
import random
import string as _string
import threading
import urllib.parse
from typing import Any, Dict, List, Optional

from curl_cffi import requests

# 标准 requests 用于 mail.tm（避免 curl_cffi TLS 兼容问题）
import requests as std_requests

try:
    from luckmail import LuckMailClient
    from luckmail.exceptions import LuckMailError
except Exception:
    LuckMailClient = None
    class LuckMailError(Exception):
        pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"


def _luckmail_settings() -> dict:
    return {
        "base_url": str(os.getenv("LUCKMAIL_BASE_URL") or "https://mails.luckyous.com").strip().rstrip("/"),
        "api_key": str(os.getenv("LUCKMAIL_API_KEY") or "").strip(),
        "api_secret": str(os.getenv("LUCKMAIL_API_SECRET") or "").strip(),
        "use_hmac": str(os.getenv("LUCKMAIL_USE_HMAC") or "").strip().lower() in {"1", "true", "yes", "y", "on"},
        "project_code": str(os.getenv("LUCKMAIL_PROJECT_CODE") or "grok").strip(),
        "email_type": str(os.getenv("LUCKMAIL_EMAIL_TYPE") or "ms_imap").strip(),
        "domain": str(os.getenv("LUCKMAIL_DOMAIN") or "outlook.com").strip(),
    }


def _mailnest_settings() -> dict:
    return {
        "api_key": str(os.getenv("MAILNEST_API_KEY") or "").strip(),
        "project_code": str(os.getenv("MAILNEST_PROJECT_CODE") or "x-ai001").strip(),
    }


class GPTMailClient:
    """与现有 grok-register 保持一致的 GPTMail 访问方式"""

    def __init__(self, proxies: Any = None):
        self.base_url = "https://mail.chatgpt.org.uk"
        self.session = requests.Session(proxies=proxies, impersonate="chrome")
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": f"{self.base_url}/",
            }
        )

    def _init_browser_session(self):
        try:
            resp = self.session.get(self.base_url, timeout=15)
            gm_sid = self.session.cookies.get("gm_sid")
            if gm_sid:
                self.session.headers.update({"Cookie": f"gm_sid={gm_sid}"})
            token_match = re.search(r"(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)", resp.text)
            if token_match:
                self.session.headers.update({"x-inbox-token": token_match.group(1)})
        except Exception:
            pass

    def generate_email(self) -> str:
        self._init_browser_session()
        resp = self.session.get(f"{self.base_url}/api/generate-email", timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"GPTMail 生成失败: {resp.status_code}")
        data = resp.json()
        email = str(((data.get("data") or {}).get("email") or "")).strip()
        token = str(((data.get("auth") or {}).get("token") or "")).strip()
        if token:
            self.session.headers.update({"x-inbox-token": token})
        if not email:
            raise RuntimeError("GPTMail 返回邮箱为空")
        return email

    def list_emails(self, email: str) -> List[Dict[str, Any]]:
        encoded_email = urllib.parse.quote(email)
        url = f"{self.base_url}/api/emails?email={encoded_email}"
        resp = self.session.get(url, timeout=15)
        if resp.status_code == 200:
            return ((resp.json() or {}).get("data") or {}).get("emails") or []
        return []


class MailTMClient:
    """mail.tm 免费临时邮箱 — 无需 API Key，无需注册
    API 文档: https://docs.mail.tm/

    用法:
        client = MailTMClient()
        email = client.create_email()
        content = client.fetch_first_email()  # 阻塞轮询，最多 60s
    """

    BASE = "https://api.mail.tm"

    def __init__(self, proxies: Any = None):
        self.session = std_requests.Session()
        if proxies:
            self.session.proxies.update(proxies)
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
        })
        self.address = ""
        self._token = ""
        self._password = ""

    def _random_user(self) -> str:
        return "grok" + "".join(random.choices(_string.ascii_lowercase + _string.digits, k=10))

    def create_email(self) -> str:
        """创建临时邮箱，返回邮箱地址"""
        # 1. 获取可用域名
        r = self.session.get(f"{self.BASE}/domains", timeout=15)
        r.raise_for_status()
        data = r.json()
        # API 有时返回 list，有时返回 hydra:Collection 包装
        if isinstance(data, list):
            domains = data
        else:
            domains = data.get("hydra:member", [])
        if not domains:
            raise RuntimeError("mail.tm: 无可用域名")
        domain = domains[0]["domain"]

        # 2. 注册账号
        self._password = "Gk" + "".join(random.choices(_string.ascii_letters + _string.digits, k=14)) + "!1"
        self.address = f"{self._random_user()}@{domain}"
        r2 = self.session.post(f"{self.BASE}/accounts", json={
            "address": self.address,
            "password": self._password,
        }, timeout=15)
        if r2.status_code != 201:
            raise RuntimeError(f"mail.tm 创建账号失败: {r2.status_code} {r2.text[:200]}")
        account = r2.json()
        self.address = account["address"]

        # 3. 获取 JWT token
        r3 = self.session.post(f"{self.BASE}/token", json={
            "address": self.address,
            "password": self._password,
        }, timeout=15)
        r3.raise_for_status()
        self._token = r3.json()["token"]

        return self.address

    def fetch_first_email(self) -> Optional[str]:
        """单次检查收件箱，返回第一封邮件内容（或 None）。
        调用方负责轮询（grok.py 外层每 5s 调用一次，共 12 次 = 60s）。"""
        if not self._token:
            return None
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            r = self.session.get(f"{self.BASE}/messages", headers=headers, timeout=10)
            if r.status_code == 401:
                # token 过期，尝试刷新
                r2 = self.session.post(f"{self.BASE}/token", json={
                    "address": self.address, "password": self._password,
                }, timeout=10)
                if r2.status_code == 200:
                    self._token = r2.json()["token"]
                    headers = {"Authorization": f"Bearer {self._token}"}
                    r = self.session.get(f"{self.BASE}/messages", headers=headers, timeout=10)
            if r.status_code != 200:
                return None
            _msg_data = r.json()
            messages = _msg_data if isinstance(_msg_data, list) else _msg_data.get("hydra:member", [])
            if not messages:
                return None
            msg_id = messages[0]["id"]
            # 获取完整邮件内容
            r3 = self.session.get(f"{self.BASE}/messages/{msg_id}", headers=headers, timeout=10)
            if r3.status_code != 200:
                return None
            msg = r3.json()
            parts = []
            if msg.get("subject"):
                parts.append(msg["subject"])
            if msg.get("text"):
                parts.append(msg["text"])
            if msg.get("html"):
                parts.append("\n".join(msg["html"]) if isinstance(msg["html"], list) else str(msg["html"]))
            if not parts:
                parts.append(msg.get("intro", ""))
                parts.append(str(msg))
            return "\n".join(parts) if parts else str(msg)
        except Exception:
            return None


class LuckMailInbox:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str = "",
        use_hmac: bool = False,
        project_code: str = "grok",
        email_type: str = "ms_imap",
        domain: str = "outlook.com",
        timeout: int = 30,
    ):
        if LuckMailClient is None:
            raise RuntimeError("LuckMail SDK 不可用")
        if not base_url:
            raise RuntimeError("缺少 LUCKMAIL_BASE_URL")
        if not api_key:
            raise RuntimeError("缺少 LUCKMAIL_API_KEY")

        self.client = LuckMailClient(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret or None,
            use_hmac=bool(use_hmac),
            timeout=float(timeout),
        )
        self.project_code = project_code or "grok"
        self.email_type = email_type or "ms_imap"
        self.domain = domain or "outlook.com"
        self.address = ""
        self.token = ""

    def create_email(self):
        try:
            result = self.client.user.purchase_emails(
                project_code=self.project_code,
                quantity=1,
                email_type=self.email_type,
                domain=self.domain,
            )
        except LuckMailError as e:
            raise RuntimeError(f"LuckMail 购买邮箱失败: {e}") from e
        except Exception as e:
            raise RuntimeError(f"LuckMail 初始化失败: {e}") from e

        purchases = list((result or {}).get("purchases") or [])
        if not purchases:
            raise RuntimeError("LuckMail 购买邮箱失败：未返回 purchases")

        purchase = purchases[0] or {}
        self.address = str(purchase.get("email_address") or "").strip()
        self.token = str(purchase.get("token") or "").strip()
        if not self.address or not self.token:
            raise RuntimeError("LuckMail 购买邮箱失败：缺少 email_address 或 token")
        return {"provider": "luckmail", "token": self.token, "email": self.address, "client": self}, self.address

    def fetch_first_email(self) -> Optional[str]:
        if not self.token:
            return None
        try:
            result = self.client.user.get_token_code(self.token)
            chunks: List[str] = []
            if getattr(result, "verification_code", None):
                chunks.append(str(getattr(result, "verification_code", "") or ""))
            if getattr(result, "mail", None):
                chunks.append(json.dumps(getattr(result, "mail", None) or {}, ensure_ascii=False))

            mail_list = self.client.user.get_token_mails(self.token)
            mails = list(getattr(mail_list, "mails", []) or [])
            if mails:
                mail = mails[0]
                message_id = str(getattr(mail, "message_id", "") or "").strip()
                chunks.extend([
                    str(getattr(mail, "subject", "") or ""),
                    str(getattr(mail, "body", "") or ""),
                    str(getattr(mail, "html_body", "") or ""),
                ])
                if message_id:
                    try:
                        detail = self.client.user.get_token_mail_detail(self.token, message_id)
                        chunks.extend([
                            str(getattr(detail, "subject", "") or ""),
                            str(getattr(detail, "body_text", "") or ""),
                            str(getattr(detail, "body_html", "") or ""),
                            str(getattr(detail, "verification_code", "") or ""),
                        ])
                    except Exception:
                        pass
            text = "\n".join([c for c in chunks if c])
            return text or None
        except Exception as e:
            print(f"获取 LuckMail 邮件失败: {e}")
            return None


class MailNestInbox:
    def __init__(
            self,
            api_key: str,
            project_code: str = "z-ai001",
            timeout: int = 30,
    ):
        if not api_key:
            raise RuntimeError("缺少 MailNest_API_KEY")

        self.api_key = api_key
        self.project_code = project_code
        self.timeout = timeout

    def __req(self, method, url, params=None, json=None):
        resp = requests.request(
            method,
            url,
            params=params,
            json=json,
            headers={
                "Authorization": f"Bearer {self.api_key}",
            },
            verify=False,
        )
        if resp.status_code == 401:
            print('api-key 无效')
            raise Exception('api-key 无效')
        resp.raise_for_status()
        resp_json = resp.json()
        print(resp_json)
        if resp_json['code'] != '00000':
            raise Exception(f'{resp_json}')
        return resp_json['data']

    def create_email(self):
        email = ''
        try:
            email = self.__req(
                'POST',
                "https://mailnest.top/api/v1/email/temporary/buy",
                json={
                    "project_code": self.project_code,
                    "count": 1,
                }
            )[0]['email']
        except:
            pass
        # 当项目邮箱数量不足时会获取失败 或 没有这个项目 购买独占邮箱
        if not email:
            try:
                email = self.__req(
                    'POST',
                    "https://mailnest.top/api/v1/email/exclusive/buy",
                    json={
                        "count": 1,
                    }
                )[0]['email']
            except:
                pass
        if not email:
            raise RuntimeError("MailNest 购买邮箱失败")
        return {"provider": "mailnest", "token": self.api_key, "email": email, "client": self}, email

    def fetch_first_email(self, email) -> Optional[str]:
        if not self.api_key:
            return None
        try:
            mails = self.__req(
                'POST',
                f'https://mailnest.top/api/v1/email/receive',
                json={
                    "email": email,
                },
            )
            if not mails:
                return None
            return '\n'.join([
                mails[0]['subject'],
                mails[0]['body_preview'],
                mails[0]['body'],
            ]) or None
        except Exception as e:
            print(f"获取 MailNest 邮件失败: {e}")
            return None


class GPTMailInboxV2:
    """GPTMail V2 客户端 — 使用新版 API（2026-07）

    API 流程:
      1. GET  /api/domains/public  → 获取活跃域名列表
      2. 客户端拼邮箱: prefix@random_domain
      3. POST /api/inbox-token     → 注册邮箱，获取 JWT token
      4. GET  /api/emails?email=.. → 轮询邮件
      5. GET  /api/email/{id}      → 获取邮件详情

    2026-08-14 修复: /api/inbox-token 增加 CF Turnstile 浏览器验证
    (428 browser_verification_required), 纯 requests 调用被拒。
    实测必须用有头 Chrome (headless 无法过挑战), 在页面上下文 fetch 同源
    API 即 200。收信轮询无需浏览器验证, 用 requests + x-inbox-token 即可。
    """

    def __init__(self, proxies: Any = None):
        self.base_url = "https://mail.chatgpt.org.uk"
        self.session = requests.Session()
        if proxies:
            self.session.proxies.update(proxies)
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
        })
        self.email = ""
        self.token = ""
        self._domains = []
        self._page = None
        self._browser = None
        self._pw = None
        self._loop = None
        self._loop_thread = None

    def _warmup(self):
        try:
            self.session.get(f"{self.base_url}/", timeout=10)
        except Exception:
            pass

    def _ensure_loop(self):
        """patchright 异步对象绑定创建时的 event loop, 必须用常驻线程 loop。"""
        import asyncio
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._loop_thread.start()
        return self._loop

    def _run(self, coro, timeout=90):
        import asyncio
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    def _get_page(self):
        """启动无头 Chrome 打开站点, CF Turnstile 自动通过后 page 常驻复用。"""
        if self._page is not None:
            return self._page
        from patchright.async_api import async_playwright

        async def _launch():
            self._pw = await async_playwright().start()
            # ⚠️ 必须 headless=False: CF Turnstile 在无头浏览器里过不了 (实测 428)
            browser = await self._pw.chromium.launch(headless=False, channel="chrome",
                args=["--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(viewport={"width": 1000, "height": 700})
            page = await ctx.new_page()
            # 挑战通过后前端会自动创建邮箱, URL 跳到 /zh/{email}; 以此作为通过信号
            passed = False
            for attempt in range(2):
                await page.goto(f"{self.base_url}/", timeout=40000, wait_until="domcontentloaded")
                for _ in range(25):
                    await _asyncio.sleep(1)
                    if "/zh/" in page.url and "@" in page.url:
                        passed = True
                        break
                if passed:
                    break
            if not passed:
                raise RuntimeError("CF 挑战未通过 (可能被限流, 稍后重试)")
            self._browser = browser
            self._page = page
            return page

        import asyncio as _asyncio
        try:
            return self._run(_launch())
        except Exception as e:
            raise RuntimeError(f"GPTMail 浏览器会话启动失败: {e}") from e

    def close(self):
        async def _close():
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass

        if self._loop is not None and not self._loop.is_closed():
            try:
                self._run(_close(), timeout=30)
            except Exception:
                pass
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        self._browser = None
        self._page = None
        self._pw = None

    def _get_domains(self):
        if self._domains:
            return self._domains
        self._warmup()
        r = self.session.get(f"{self.base_url}/api/domains/public", timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"获取域名失败: {r.status_code}")
        data = r.json()
        domains_list = (data.get("data") or {}).get("domains") or []
        self._domains = [d["domain_name"] for d in domains_list if d.get("is_active")]
        if not self._domains:
            raise RuntimeError("无活跃域名")
        return self._domains

    def create_email(self) -> str:
        """在浏览器上下文里调 inbox-token (过 CF 验证), 返回邮箱地址。"""
        import time as _time
        self._get_page()
        js = """
        async () => {
            const dr = await fetch("/api/domains/public");
            const dj = await dr.json();
            const domains = ((dj.data || {}).domains || [])
                .filter(d => d.is_active).map(d => d.domain_name);
            if (!domains.length) return {err: "no active domains"};
            const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
            let prefix = "";
            for (let i = 0; i < 10; i++) prefix += chars[Math.floor(Math.random() * 36)];
            const email = prefix + "@" + domains[Math.floor(Math.random() * domains.length)];
            const r = await fetch("/api/inbox-token", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({email}),
            });
            const j = await r.json();
            if (r.status !== 200 || !j.success) {
                return {err: "inbox-token failed: " + r.status + " " + JSON.stringify(j)};
            }
            return {email, token: (j.auth || {}).token || ""};
        }
        """
        # 挑战通过有延迟, 428 时重试 (最多 4 次, 每次等 6s)
        result = None
        for attempt in range(4):
            try:
                result = self._run(self._page.evaluate(js), timeout=60)
            except Exception as e:
                raise RuntimeError(f"GPTMail inbox-token 浏览器调用失败: {e}") from e
            if isinstance(result, dict) and not result.get("err"):
                break
            if attempt < 3:
                _time.sleep(6)
        if not isinstance(result, dict) or result.get("err"):
            raise RuntimeError(f"inbox-token 失败: {result}")
        self.email = str(result.get("email") or "")
        self.token = str(result.get("token") or "")
        if not self.email or not self.token:
            raise RuntimeError("未获取到 inbox token")
        return self.email

    def fetch_first_email(self) -> Optional[str]:
        if not self.token:
            return None
        try:
            encoded = urllib.parse.quote(self.email)
            r = self.session.get(
                f"{self.base_url}/api/emails?email={encoded}",
                headers={"x-inbox-token": self.token},
                timeout=15,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            emails = (data.get("data") or {}).get("emails") or data.get("data") or []
            if isinstance(emails, dict):
                emails = [emails]
            for msg in (emails if isinstance(emails, list) else []):
                subject = str(msg.get("subject") or "")
                body = str(msg.get("text") or msg.get("html") or msg.get("body") or "")
                text = subject + " " + body
                # 先检查是否有验证码
                m = re.search(r"([A-Z0-9]{3})-?([A-Z0-9]{3})", text)
                if m:
                    return text
                # 有内容就返回
                if len(text) > 10:
                    return text
            # 尝试获取详情
            for msg in (emails if isinstance(emails, list) else []):
                msg_id = msg.get("id") or msg.get("message_id")
                if msg_id:
                    r2 = self.session.get(
                        f"{self.base_url}/api/email/{urllib.parse.quote(str(msg_id))}",
                        headers={"x-inbox-token": self.token},
                        timeout=15,
                    )
                    if r2.status_code == 200:
                        detail = r2.json()
                        d = (detail.get("data") or detail)
                        text = str(d.get("subject") or "") + " " + str(d.get("text") or d.get("html") or d.get("body") or "")
                        if len(text) > 10:
                            return text
        except Exception:
            pass
        return None


class TmailInbox:
    """Tmail (mail.sunls.de) 免费临时邮箱 — 2026-08-14 接入

    API 流程 (全部需在浏览器上下文内, 站点有 CF Turnstile 门禁):
      1. 有头 Chrome 打开站点, Turnstile 自动通过
      2. GET /api/domain → 域名池 (isco/sunix/chato.eu.org)
      3. 客户端拼地址: 随机前缀@随机域名 (无 create 步骤, 地址即键)
      4. GET /api/fetch?to={addr}&limit=30 → {code:0, data:[邮件]}
    邮件字段: id/from/to/subject/body/created_at

    ⚠️ eu.org 共享域名, 对严格风控平台 (如 xAI) 可能被拒投,
    适合对域名不敏感的平台, 或作为免费备选源。
    """

    def __init__(self, proxies: Any = None):
        self.base_url = "https://mail.sunls.de"
        self.proxies = proxies
        self.email = ""
        self._page = None
        self._browser = None
        self._pw = None
        self._loop = None
        self._loop_thread = None
        self._domains = []

    def _ensure_loop(self):
        import asyncio
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._loop_thread.start()
        return self._loop

    def _run(self, coro, timeout=90):
        import asyncio
        fut = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        return fut.result(timeout=timeout)

    def _get_page(self):
        if self._page is not None:
            return self._page
        from patchright.async_api import async_playwright
        import asyncio as _asyncio

        async def _launch():
            self._pw = await async_playwright().start()
            # ⚠️ 必须 headless=False: Turnstile 在无头浏览器里过不了
            browser = await self._pw.chromium.launch(headless=False, channel="chrome",
                args=["--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(viewport={"width": 1000, "height": 700})
            page = await ctx.new_page()
            await page.goto(f"{self.base_url}/", timeout=40000, wait_until="domcontentloaded")
            # 等 Turnstile 通过: 前端验证完成后会写入 localStorage.address
            for _ in range(45):
                await _asyncio.sleep(1)
                addr = await page.evaluate("localStorage.getItem('address')")
                if addr:
                    self._browser = browser
                    self._page = page
                    return page
            raise RuntimeError("Turnstile 验证超时未通过")

        try:
            return self._run(_launch(), timeout=120)
        except Exception as e:
            raise RuntimeError(f"Tmail 浏览器会话启动失败: {e}") from e

    def close(self):
        async def _close():
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass

        if self._loop is not None and not self._loop.is_closed():
            try:
                self._run(_close(), timeout=30)
            except Exception:
                pass
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        self._browser = None
        self._page = None
        self._pw = None

    def create_email(self):
        """浏览器内拿域名池 + 随机拼地址, 返回 (token_like, email)。"""
        self._get_page()
        js = """
        async () => {
            const dr = await fetch("/api/domain");
            const dj = await dr.json();
            if (dj.code !== 0 || !(dj.data || []).length) return {err: "no domains"};
            const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
            let prefix = "";
            for (let i = 0; i < 10; i++) prefix += chars[Math.floor(Math.random() * 36)];
            const email = prefix + "@" + dj.data[Math.floor(Math.random() * dj.data.length)];
            return {email};
        }
        """
        try:
            result = self._run(self._page.evaluate(js), timeout=60)
        except Exception as e:
            raise RuntimeError(f"Tmail 创建邮箱失败: {e}") from e
        if not isinstance(result, dict) or result.get("err") or not result.get("email"):
            raise RuntimeError(f"Tmail 创建邮箱失败: {result}")
        self.email = str(result["email"])
        return {"provider": "tmail", "token": self.email, "email": self.email, "client": self}, self.email

    def fetch_first_email(self) -> Optional[str]:
        """轮询 /api/fetch?to={addr}&limit=30, 返回邮件文本 (subject+body)。"""
        if not self.email or self._page is None:
            return None
        js = """
        async (email) => {
            const r = await fetch("/api/fetch?to=" + encodeURIComponent(email) + "&limit=30");
            const j = await r.json();
            if (j.code !== 0 || !(j.data || []).length) return null;
            const parts = [];
            for (const m of j.data) {
                if (m.subject) parts.push(m.subject);
                if (m.body) parts.push(String(m.body).slice(0, 2000));
            }
            return parts.join("\\n");
        }
        """
        try:
            result = self._run(self._page.evaluate(js, self.email), timeout=60)
        except Exception:
            return None
        if not result:
            return None
        return str(result)


class FCEInbox:
    """FreeCustom.Email 免费临时邮箱 — 2026-08-14 接入

    纯 REST API, 无浏览器依赖:
      1. POST /v1/inboxes {"inbox": "前缀@域名"} → 注册地址
      2. GET  /v1/inboxes/{inbox}/messages → {success, data:[...], count}
    环境变量: FCE_API_KEY (必填), FCE_BASE_URL (默认 https://api2.freecustom.email/v1)
    域名池: ditapi.info / ditmail.info / fce.email
    免费套餐注意: isTesting=true 需付费; OTP 端点免费档只返回 __DETECTED__,
    取码走 messages 端点自行正则提取。共享平台域名, 严格风控平台 (如 xAI)
    可能拒投, 适合对域名不敏感的平台。
    """

    DOMAINS = ["ditapi.info", "fce.email"]  # ditmail.info 免费档不支持 (403)

    def __init__(self, proxies: Any = None):
        self.proxies = proxies
        self.base_url = str(os.getenv("FCE_BASE_URL") or "https://api2.freecustom.email/v1").strip().rstrip("/")
        self.api_key = str(os.getenv("FCE_API_KEY") or "").strip()
        self.email = ""
        self._session = None

    def _get_session(self):
        if self._session is None:
            import requests as _requests
            self._session = _requests.Session()
            if self.proxies:
                self._session.proxies.update(self.proxies)
        return self._session

    def create_email(self):
        if not self.api_key:
            raise RuntimeError("缺少 FCE_API_KEY 环境变量 (freecustom.email 免费注册获取)")
        prefix = "".join(random.choices(_string.ascii_lowercase + _string.digits, k=10))
        self.email = f"{prefix}@{random.choice(self.DOMAINS)}"
        r = self._get_session().post(
            f"{self.base_url}/inboxes",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"inbox": self.email},
            timeout=20,
        )
        data = r.json() if r.status_code < 500 else {}
        if r.status_code not in (200, 201) or not data.get("success"):
            raise RuntimeError(f"FCE 注册邮箱失败: HTTP {r.status_code} {str(data)[:150]}")
        return {"provider": "fce", "token": self.email, "email": self.email, "client": self}, self.email

    def fetch_first_email(self) -> Optional[str]:
        if not self.email:
            return None
        try:
            r = self._get_session().get(
                f"{self.base_url}/inboxes/{urllib.parse.quote(self.email)}/messages",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=20,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            msgs = data.get("data") or []
            if not msgs:
                return None
            parts = []
            for m in msgs:
                subject = str(m.get("subject") or "")
                body = str(m.get("body") or m.get("text") or m.get("html") or "")
                if subject:
                    parts.append(subject)
                if body:
                    parts.append(body[:2000])
            text = "\n".join(parts)
            return text if len(text) > 5 else None
        except Exception:
            return None


class GmailIMAPClient:
    """Gmail IMAP 客户端 — 用 +alias 无限衍生邮箱，IMAP 轮询收验证码"""

    def __init__(self, proxies: Any = None):
        import imaplib
        import email as _email_mod
        self.proxies = proxies
        self.email = ""
        self._imap = None
        self._base_email = str(os.getenv("GMAIL_BASE_EMAIL") or "").strip()
        self._app_password = str(os.getenv("GMAIL_APP_PASSWORD") or "").strip()
        self._imap_host = "imap.gmail.com"
        self._imap_port = 993
        self._last_uid = 0  # 记录上次检查到的最新邮件 UID

    def _connect(self):
        import imaplib
        if not self._base_email or not self._app_password:
            raise RuntimeError("缺少 GMAIL_BASE_EMAIL / GMAIL_APP_PASSWORD 环境变量")
        if self._imap:
            try:
                self._imap.noop()
                return
            except Exception:
                pass
        self._imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
        self._imap.login(self._base_email, self._app_password)
        self._imap.select("INBOX")

    def create_email(self) -> str:
        tag = "".join(random.choice(_string.ascii_lowercase + _string.digits) for _ in range(8))
        self.email = f"{self._base_email.split('@')[0]}+grok{tag}@{self._base_email.split('@')[1]}"
        # 连 IMAP 获取当前最新 UID 作为基准
        try:
            self._connect()
            _, data = self._imap.uid("SEARCH", None, "ALL")
            uids = data[0].split()
            self._last_uid = int(uids[-1]) if uids else 0
        except Exception:
            self._last_uid = 0
        return self.email

    def fetch_first_email(self) -> Optional[str]:
        import email as _email_mod
        try:
            self._connect()
            # 只搜 From x.ai 的邮件（避免 Windsurf 等干扰）
            _, data = self._imap.uid("SEARCH", None, f'(FROM "x.ai" UID {self._last_uid + 1}:*)')
            new_uids = data[0].split()
            if not new_uids:
                return None
            # 取最新一封
            latest_uid = int(new_uids[-1])
            self._last_uid = max(self._last_uid, latest_uid)
            _, msg_data = self._imap.uid("FETCH", str(latest_uid).encode(), "(BODY.PEEK[])")
            raw = msg_data[0][1]
            msg = _email_mod.message_from_bytes(raw)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if ctype in ("text/plain", "text/html"):
                        payload = part.get_payload(decode=True)
                        if payload:
                            body += payload.decode("utf-8", errors="replace") + "\n"
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
            return body
        except Exception as e:
            print(f"[Gmail] fetch error: {e}")
            return None


class EmailService:
    """统一邮箱服务门面，兼容旧调用方"""

    def __init__(self, proxies: Any = None, provider: str = "luckmail"):
        self.proxies = proxies
        self.provider = str(provider or os.getenv("EMAIL_PROVIDER") or "luckmail").strip().lower()
        if self.provider not in {"gptmail", "mailtm", "luckmail", "mailnest", "gmail", "tmail", "fce"}:
            raise ValueError(f"不支持的邮箱提供商: {self.provider}")

    def create_email(self):
        if self.provider == "mailtm":
            try:
                client = MailTMClient(self.proxies)
                email = client.create_email()
                token_like = {"provider": "mailtm", "client": client, "email": email}
                print(f"[+] mail.tm 邮箱已创建: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 请求 mail.tm 出错: {e}")
                return None, None
        elif self.provider == "luckmail":
            try:
                settings = _luckmail_settings()
                inbox = LuckMailInbox(
                    base_url=settings["base_url"],
                    api_key=settings["api_key"],
                    api_secret=settings["api_secret"],
                    use_hmac=settings["use_hmac"],
                    project_code=settings["project_code"],
                    email_type=settings["email_type"],
                    domain=settings["domain"],
                )
                token_like, email = inbox.create_email()
                print(f"[+] LuckMail 邮箱已购买: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 请求 LuckMail 出错: {e}")
                return None, None
        elif self.provider == 'mailnest':
            try:
                settings = _mailnest_settings()
                inbox = MailNestInbox(
                    api_key=settings["api_key"],
                    project_code=settings["project_code"],
                )
                token_like, email = inbox.create_email()
                print(f"[+] MailNest 邮箱已购买: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 请求 MailNest 出错: {e}")
                return None, None
        elif self.provider == "gmail":
            try:
                client = GmailIMAPClient(self.proxies)
                email = client.create_email()
                token_like = {"provider": "gmail", "client": client, "email": email}
                print(f"[+] Gmail 别名已创建: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] Gmail 出错: {e}")
                return None, None
        elif self.provider == "tmail":
            try:
                client = TmailInbox(self.proxies)
                token_like, email = client.create_email()
                print(f"[+] Tmail 邮箱已创建: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 请求 Tmail 出错: {e}")
                return None, None
        elif self.provider == "fce":
            try:
                client = FCEInbox(self.proxies)
                token_like, email = client.create_email()
                print(f"[+] FCE 邮箱已创建: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 请求 FCE 出错: {e}")
                return None, None
        # gptmail: 使用 V2 API
        try:
            client = GPTMailInboxV2(self.proxies)
            email = client.create_email()
            token_like = {"provider": "gptmail-v2", "client": client, "email": email}
            print(f"[+] GPTMail 邮箱已创建: {email}")
            return token_like, email
        except Exception as e:
            print(f"[Error] 请求 GPTMail 出错: {e}")
            return None, None

    def fetch_first_email(self, token_like):
        try:
            if not isinstance(token_like, dict):
                return None

            provider = str(token_like.get("provider") or "gptmail").strip().lower()
            client = token_like.get("client")
            if not client:
                return None

            if provider in ("mailtm", "luckmail", "gptmail-v2", "gmail", "tmail", "fce"):
                return client.fetch_first_email()
            elif provider == "mailnest":
                return client.fetch_first_email(token_like.get("email"))

            # legacy gptmail
            email = str(token_like.get("email") or "").strip()
            if not email:
                return None

            emails = client.list_emails(email)
            if not emails:
                return None

            first = emails[0] or {}
            subject = str(first.get("subject") or "")
            from_name = str(((first.get("from") or {}).get("name") or ""))
            from_email = str(((first.get("from") or {}).get("address") or first.get("from_address") or ""))
            body_text = str(first.get("text") or first.get("content") or "")
            body_html = str(first.get("html") or first.get("html_content") or "")
            return "\n".join([f">{subject}<", subject, from_name, from_email, body_text, body_html])
        except Exception as e:
            print(f"获取邮件失败: {e}")
            return None

"""
SSO → OAuth Device Flow 铸造模块 (2026-08-06)
替换被 CF 拦截的 PKCE 方案 (sso_to_cpa.py)。

原理: x.ai OAuth Device Authorization Grant
- 关键: scope 必须为 7 个, 不能加 conversations/workspaces (该 client 未授权, 加了 Access denied)
- 授权: 有头 Chrome 注入 SSO cookie → 打开授权页自动点"继续/允许" → 轮询 token

接口: sso_to_device(sso, email) -> cpa_data (与 sso_to_cpa.py 返回结构一致)
"""
import json, sys, time, base64, asyncio, urllib.request, urllib.error, urllib.parse, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
PROXY = os.getenv("GROK_PROXY") or "http://127.0.0.1:7897"


def _http_json(url, method="GET", form=None, timeout=40):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": PROXY, "http": PROXY}))
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "grok-register-cpa/1.0")
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


async def _browser_authorize(vuc, sso_jwt):
    """有头 Chrome + 代理, 注入 SSO cookie, 自动点授权按钮"""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome",
            proxy={"server": PROXY},
            args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(viewport={"width": 1000, "height": 700})
        await ctx.add_cookies([{
            "name": "sso", "value": sso_jwt, "domain": ".x.ai", "path": "/",
        }])
        page = await ctx.new_page()
        try:
            await page.goto(vuc, timeout=40000, wait_until="domcontentloaded")
            for _ in range(20):
                await asyncio.sleep(2)
                if "/device/done" in page.url:
                    return True
                for sel in ["button:has-text('继续')", "button:has-text('Allow')",
                            "button:has-text('允许')", "button:has-text('Continue')",
                            "button[type=submit]"]:
                    try:
                        btn = await page.query_selector(sel)
                        if btn and await btn.is_visible():
                            await btn.click()
                            break
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            await browser.close()
    return False


def sso_to_device(sso_token, email=""):
    """Device Flow 铸造: SSO cookie → OAuth AT/RT。
    返回 dict(access_token/refresh_token/expires_in/token_type) 或 None"""
    try:
        s, p = _http_json("https://auth.x.ai/oauth2/device/code", "POST",
                          {"client_id": CLIENT_ID, "scope": SCOPE})
        if s != 200 or not isinstance(p, dict) or "device_code" not in p:
            print(f"  [DEVICE] device/code 失败 HTTP {s}: {str(p)[:150]}")
            return None
        dc = p["device_code"]
        vuc = p.get("verification_uri_complete")
        ok = asyncio.run(_browser_authorize(vuc, sso_token))
        if not ok:
            print("  [DEVICE] 授权未确认, 继续轮询")
        deadline = time.time() + 90
        interval = max(int(p.get("interval", 5)), 1)
        while time.time() < deadline:
            time.sleep(interval)
            s, t = _http_json("https://auth.x.ai/oauth2/token", "POST", {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": dc, "client_id": CLIENT_ID})
            if s == 200 and isinstance(t, dict) and t.get("access_token"):
                return {
                    "access_token": t["access_token"],
                    "refresh_token": t.get("refresh_token", ""),
                    "expires_in": int(t.get("expires_in") or 21600),
                    "token_type": t.get("token_type", "Bearer"),
                }
            err = t.get("error") if isinstance(t, dict) else None
            if err in ("access_denied", "expired_token"):
                print(f"  [DEVICE] token 失败: {err}: {t.get('error_description', '')[:80]}")
                return None
            if err == "slow_down":
                interval += 5
        print("  [DEVICE] 轮询超时")
        return None
    except Exception as e:
        print(f"  [DEVICE] 异常: {str(e)[:120]}")
        return None


if __name__ == "__main__":
    # 单测: 从 accounts.txt 取一个未使用账号测试
    sso_map = {}
    for l in open(os.path.join(os.path.dirname(__file__), "keys/accounts.txt"),
                  encoding="utf-8-sig").read().splitlines():
        parts = l.split(":")
        if len(parts) >= 3:
            sso_map[parts[0]] = ":".join(parts[2:])
    email = sys.argv[1] if len(sys.argv) > 1 else list(sso_map.keys())[0]
    print(f"测试铸造: {email}")
    result = sso_to_device(sso_map.get(email, ""), email)
    if result:
        print(f"✅ 成功: AT={result['access_token'][:30]}... RT={result['refresh_token'][:20]}...")
    else:
        print("❌ 失败")

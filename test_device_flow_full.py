"""
Device Code Flow 完整测试
Step 1: 请求 device_code (curl_cffi)
Step 2: 用 ruyipage 授权
Step 3: 轮询 token (curl_cffi)
"""
import sys, time, json, hashlib, base64, secrets
sys.path.insert(0, "D:/ruyipage")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ruyipage import FirefoxPage, FirefoxOptions
from curl_cffi import requests as cf_req

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
PROXY = "http://127.0.0.1:7897"

# 取 SSO
with open("keys/accounts.txt") as f:
    lines = [l.strip() for l in f if l.strip()]
for line in lines:
    parts = line.split(":")
    if len(parts) >= 3 and "outlook.com" not in parts[0]:
        sso = parts[2]
        break

# === Step 1: 请求 device_code ===
print("=== Step 1: 请求 device_code ===", flush=True)
sess = cf_req.Session()
sess.proxies = {"http": PROXY, "https": PROXY}
r = sess.post("https://auth.x.ai/oauth2/device/code", data={
    "client_id": CLIENT_ID, "scope": SCOPE
}, timeout=15)
d = r.json()
device_code = d["device_code"]
user_code = d["user_code"]
verification_uri = d["verification_uri_complete"]
print(f"user_code: {user_code}", flush=True)
print(f"verification_uri: {verification_uri}", flush=True)

# === Step 2: 用 ruyipage 授权 ===
print("\n=== Step 2: 浏览器授权 ===", flush=True)
opts = FirefoxOptions()
opts.set_browser_path(r"C:\Program Files\Mozilla Firefox\firefox.exe")
opts.headless(True)

page = FirefoxPage(opts)
try:
    page.get("https://auth.x.ai/")
    time.sleep(2)
    page.set_cookies({"name": "sso", "value": sso, "domain": ".x.ai"})

    page.get(verification_uri)
    time.sleep(3)
    print(f"URL: {page.url}", flush=True)
    print(f"Title: {page.title}", flush=True)

    # 点击 Authorize 按钮（第一次跳转到 consent 页面）
    for click_round in range(3):
        clicked = page.run_js("""
            (() => {
                var btn = Array.from(document.querySelectorAll('button'))
                    .find(b => /allow|authorize|confirm|continue|sign/i.test(b.textContent));
                if (btn) { btn.click(); return true; }
                var btn2 = document.querySelector('input[type=submit]');
                if (btn2) { btn2.click(); return true; }
                return false;
            })()
        """)
        print(f"  Round {click_round+1}: clicked={clicked}, URL: {page.url}", flush=True)
        time.sleep(3)
        if "consent" in page.url or "code=" in page.url or "callback" in page.url:
            break

    print(f"Final URL: {page.url}", flush=True)
    page.quit()
except Exception as e:
    print(f"Browser error: {e}", flush=True)
    import traceback; traceback.print_exc()
    try: page.quit()
    except: pass

# === Step 3: 轮询 token ===
print("\n=== Step 3: 轮询 token ===", flush=True)
for i in range(36):
    r = sess.post("https://auth.x.ai/oauth2/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": CLIENT_ID,
    }, timeout=15)
    if r.status_code == 200:
        token = r.json()
        print("AA SUCCESS! Token obtained!", flush=True)
        print(json.dumps({k: (v[:30]+"..." if isinstance(v, str) and len(v) > 30 else v) for k, v in token.items()}, indent=2), flush=True)
        break
    elif r.status_code == 400:
        err = r.json().get("error", "")
        if err == "authorization_pending":
            print(f"  [{i+1}] authorization_pending, waiting...", flush=True)
            time.sleep(5)
        elif err == "slow_down":
            print(f"  [{i+1}] slow_down, waiting longer...", flush=True)
            time.sleep(10)
        else:
            print(f"  [{i+1}] Error: {err} - {r.text[:200]}", flush=True)
            break
    else:
        print(f"  [{i+1}] Status: {r.status_code} - {r.text[:200]}", flush=True)
        break
"""
Device Code Flow 完整测试 v2
"""
import sys, time, json
sys.path.insert(0, "D:/ruyipage")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ruyipage import FirefoxPage, FirefoxOptions
from curl_cffi import requests as cf_req

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
PROXY = "http://127.0.0.1:7897"

with open("keys/accounts.txt") as f:
    lines = [l.strip() for l in f if l.strip()]
for line in lines:
    parts = line.split(":")
    if len(parts) >= 3 and "outlook.com" not in parts[0]:
        sso = parts[2]
        break

# Step 1: device_code
print("=== Step 1: device_code ===", flush=True)
sess = cf_req.Session()
sess.proxies = {"http": PROXY, "https": PROXY}
r = sess.post("https://auth.x.ai/oauth2/device/code", data={
    "client_id": CLIENT_ID, "scope": SCOPE
}, timeout=15)
d = r.json()
device_code = d["device_code"]
verification_uri = d["verification_uri_complete"]
print(f'user_code: {d["user_code"]}', flush=True)

# Step 2: 浏览器授权
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

    # 点 Continue 按钮
    try:
        btn = page.ele("@text()=Continue", timeout=3)
        if btn:
            btn.click()
            print("点击 Continue", flush=True)
            time.sleep(5)
            print(f"After Continue: {page.url}", flush=True)
    except:
        print("Continue button not found", flush=True)

    # 如果跳转到设备 consent 页面，点 Allow
    if "device/consent" in page.url:
        print("设备 consent 页面，点击 Allow...", flush=True)
        try:
            btn = page.ele("@text()=Allow", timeout=3)
            if btn:
                btn.click()
                print("点击 Allow", flush=True)
                time.sleep(3)
                print(f"Final: {page.url}", flush=True)
        except:
            print("Allow button not found", flush=True)

    page.quit()
except Exception as e:
    print(f"ERR: {e}", flush=True)
    import traceback
    traceback.print_exc()
    try:
        page.quit()
    except:
        pass

# Step 3: 轮询 token
print("\n=== Step 3: 轮询 token ===", flush=True)
for i in range(36):
    r = sess.post("https://auth.x.ai/oauth2/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": CLIENT_ID,
    }, timeout=15)
    if r.status_code == 200:
        token = r.json()
        print("AA SUCCESS!", flush=True)
        print(json.dumps({k: str(v)[:40] for k, v in token.items()}, indent=2), flush=True)
        break
    elif r.status_code == 400:
        err = r.json().get("error", "")
        if err == "authorization_pending":
            print(f"  [{i+1}] pending", flush=True)
            time.sleep(5)
        elif err == "slow_down":
            time.sleep(10)
        else:
            print(f"  [{i+1}] {err}", flush=True)
            break
    else:
        print(f"  [{i+1}] {r.status_code}", flush=True)
        break
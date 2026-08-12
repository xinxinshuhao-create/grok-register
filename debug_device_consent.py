"""
检查设备 consent 页面的按钮
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

# device_code
sess = cf_req.Session()
sess.proxies = {"http": PROXY, "https": PROXY}
r = sess.post("https://auth.x.ai/oauth2/device/code", data={
    "client_id": CLIENT_ID, "scope": SCOPE
}, timeout=15)
d = r.json()
verification_uri = d["verification_uri_complete"]

# 浏览器
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
    # 点 Continue
    btn = page.ele("@text()=Continue", timeout=3)
    if btn:
        btn.click()
        time.sleep(5)
    # 查看设备 consent 页面的按钮
    btns = page.run_js("""
        JSON.stringify(Array.from(document.querySelectorAll("button")).map(b => ({
            text: b.textContent.trim().substring(0, 40),
            type: b.type,
            id: b.id
        })))
    """)
    print(f"URL: {page.url}", flush=True)
    print(f"Title: {page.title}", flush=True)
    print(f"Buttons: {btns}", flush=True)
    page.quit()
except Exception as e:
    print(f"ERR: {e}", flush=True)
    import traceback
    traceback.print_exc()
    try:
        page.quit()
    except:
        pass
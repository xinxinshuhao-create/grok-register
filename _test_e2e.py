import sys, urllib.parse, re, secrets, hashlib, base64, requests as std_req
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from curl_cffi import requests as cf_req

with open("keys/accounts.txt") as f:
    lines = [l.strip() for l in f if l.strip()]
for line in reversed(lines):
    parts = line.split(":")
    if len(parts) >= 3 and "outlook.com" in parts[0]:
        email = parts[0]
        sso = parts[2]
        break

sess = cf_req.Session()
sess.proxies = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
sess.cookies.set("sso", sso, domain=".x.ai")

code_verifier = secrets.token_urlsafe(64)[:128]
code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()

params = {
    "response_type": "code",
    "client_id": "b1a00492-073a-47ea-816f-4c329264a828",
    "redirect_uri": "http://127.0.0.1:56121/callback",
    "code_challenge": code_challenge,
    "code_challenge_method": "S256",
    "scope": "openid profile email offline_access grok-cli:access api:access",
    "state": secrets.token_urlsafe(16),
}
auth_url = "https://auth.x.ai/oauth2/authorize?" + urllib.parse.urlencode(params)
r = sess.get(auth_url, allow_redirects=False, timeout=30,
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
consent_url = r.headers.get("Location", "")
print(f"Auth: {r.status_code} -> {consent_url[:80]}", flush=True)

r2 = sess.get(consent_url, allow_redirects=False, timeout=30,
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
html = r2.text
if "Cloudflare" in html[:500]:
    print("CF BLOCKED", flush=True)
else:
    print("Real consent page", flush=True)

hidden = {}
for m in re.finditer(r'<input[^>]*type="hidden"[^>]*>', html, re.I):
    name_m = re.search(r'name="([^"]+)"', m.group(0))
    value_m = re.search(r'value="([^"]*)"', m.group(0))
    if name_m:
        hidden[name_m.group(1)] = value_m.group(1) if value_m else ""

action = consent_url
fam = re.search(r'<form[^>]*action="([^"]+)"', html, re.I)
if fam:
    act = fam.group(1)
    if act.startswith("/"):
        parsed = urllib.parse.urlparse(consent_url)
        action = f"{parsed.scheme}://{parsed.netloc}{act}"
    else:
        action = act

print(f"Action: {action[:80]}", flush=True)
print(f"Fields: {list(hidden.keys())}", flush=True)

s = std_req.Session()
s.proxies = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
s.cookies.set("sso", sso, domain=".x.ai")

form_data = dict(hidden)
form_data["decision"] = "allow"
r3 = s.post(action, data=form_data, allow_redirects=False, timeout=15,
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
             "Content-Type": "application/x-www-form-urlencoded",
             "Referer": consent_url,
             "Origin": "https://accounts.x.ai"})
print(f"POST: {r3.status_code}", flush=True)
print(f"Location: {r3.headers.get('Location', 'none')[:120]}", flush=True)
if r3.status_code >= 400:
    print(f"Body: {r3.text[:200]}", flush=True)
print(f"Cookies before POST: {dict(s.cookies)}", flush=True)

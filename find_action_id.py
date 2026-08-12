import requests, re
from urllib.parse import urljoin

s = requests.Session()
s.proxies = {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}

start_url = 'https://accounts.x.ai/sign-up'
print("GET sign-up page...")
html = s.get(start_url, timeout=15).text
print(f"HTML: {len(html)} bytes")

# Find JS URLs
js_urls = list(set(urljoin(start_url, m.group(0)) for m in re.finditer(r'/_next/static/chunks/[^\"\'\s>]+\.js', html)))
print(f"JS files: {len(js_urls)}")

# Search for action ID (try ALL JS files)
action_id = None
for i, js_url in enumerate(js_urls):
    try:
        print(f"  [{i+1}/{len(js_urls)}] {js_url[-50:]}")
        js = s.get(js_url, timeout=10).text
        m = re.search(r'7f[a-fA-F0-9]{40}', js)
        if m:
            action_id = m.group(0)
            print(f"  >> FOUND: {action_id}")
            break
    except Exception as e:
        print(f"  SKIP: {e}")
        continue

if action_id:
    print(f"\nAction ID: {action_id}")
else:
    print("\nAction ID NOT FOUND")

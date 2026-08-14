"""Device code flow auto-approve via CDP browser"""
import json, time, urllib.parse, os, sys
sys.stdout.reconfigure(encoding='utf-8')
from DrissionPage import ChromiumPage, ChromiumOptions
from curl_cffi import requests as cf_req

CLIENT_ID = 'b1a00492-073a-47ea-816f-4c329264a828'
PROXY = os.getenv('CLIPROXY') or ''
if not PROXY:
    raise SystemExit('缺少 CLIPROXY 环境变量 (真实代理 IP)')
OUTPUT = os.getenv('CPA_AUTHS_DIR') or r'D:\CLIProxyAPIPlus\auths'
KEYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'keys')

lines = [l.strip() for l in open(os.path.join(KEYS_DIR, 'accounts.txt'), encoding='utf-8')
         if 'outlook.com' in l and l.strip()]

for line in lines:
    parts = line.split(':')
    if len(parts) < 3: continue
    email, password, sso = parts[0], parts[1], parts[2]
    safe_email = email.replace('@', '_').replace('.', '_')

    if os.path.exists(f'{OUTPUT}/xai-{safe_email}.json'):
        print(f'[{email}] SKIP')
        continue

    print(f'[{email}]')

    # Get device code
    sess = cf_req.Session(impersonate='chrome120', proxies={'http': PROXY, 'https': PROXY})
    sess.cookies.set('sso', sso, domain='.x.ai', path='/')
    r = sess.post('https://auth.x.ai/oauth2/device/code', data={
        'client_id': CLIENT_ID,
        'scope': 'openid profile email offline_access grok-cli:access api:access',
    }, timeout=30)
    d = r.json()
    device_code = d['device_code']
    verify_url = d['verification_uri_complete']
    print(f'  Code: {d["user_code"]}')

    # Browser auto-approve
    co = ChromiumOptions()
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument(f'--proxy-server={PROXY.replace("http://", "")}')
    page = ChromiumPage(co)

    try:
        # Inject SSO cookie via CDP
        page.set.cookies([{
            'name': 'sso', 'value': sso,
            'domain': '.x.ai', 'path': '/',
            'secure': True, 'httpOnly': True, 'sameSite': 'Lax',
        }])

        # Navigate to verify URL
        page.get(verify_url)
        time.sleep(4)

        # Step 1: Click "继续" using DrissionPage selector with by_js
        btn1 = page.ele('tag:button@@text():继续', timeout=5)
        if btn1:
            btn1.click(by_js=True)
            print(f'  Step1: 继续 clicked')
            time.sleep(5)

        # Step 2: Click "全部允许" then "确认我的选择"
        if 'consent' in page.url:
            btn2 = page.ele('tag:button@@text():全部允许', timeout=5)
            if btn2:
                btn2.click(by_js=True)
                print(f'  Step2: 全部允许 clicked')
                time.sleep(1)

            btn3 = page.ele('tag:button@@text():确认我的选择', timeout=5)
            if btn3:
                btn3.click(by_js=True)
                print(f'  Step2: 确认 clicked')
            time.sleep(3)

        final_url = page.url
        print(f'  Final URL: {final_url[:120]}')

        # Poll for token
        print(f'  Polling..', end='')
        interval = 5
        ok_flag = False
        for i in range(36):
            time.sleep(interval)
            r2 = sess.post('https://auth.x.ai/oauth2/token', data={
                'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                'device_code': device_code, 'client_id': CLIENT_ID,
            }, timeout=30)

            if r2.status_code == 200:
                d2 = r2.json()
                if d2.get('access_token'):
                    # Save CPA token
                    record = {
                        'type': 'xai', 'auth_kind': 'oauth',
                        'access_token': d2['access_token'],
                        'refresh_token': d2.get('refresh_token', ''),
                        'token_type': d2.get('token_type', 'Bearer'),
                        'expires_in': d2.get('expires_in', 21600),
                        'expired': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + 21600)),
                        'last_refresh': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time())),
                        'email': email,
                        'base_url': 'https://cli-chat-proxy.grok.com/v1',
                        'token_endpoint': 'https://auth.x.ai/oauth2/token',
                        'redirect_uri': 'http://127.0.0.1:56121/callback',
                        'client_id': CLIENT_ID,
                        'disabled': False,
                        'mint_method': 'device_code',
                        'protocol_flow': 'device_code',
                        'headers': {
                            'X-XAI-Token-Auth': 'xai-grok-cli',
                            'x-grok-client-version': '0.2.93',
                            'x-grok-client-identifier': 'grok-shell',
                        },
                    }
                    if d2.get('id_token'):
                        record['id_token'] = d2['id_token']

                    path = f'{OUTPUT}/xai-{safe_email}.json'
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(record, f, ensure_ascii=False, indent=2)
                    print(f' OK!')
                    ok_flag = True
                    break

            err = (r2.json() or {}).get('error', '')
            if err == 'authorization_pending':
                print('.', end='', flush=True)
            elif err == 'slow_down':
                interval += 5
                print('s', end='')
            else:
                print(f' {err}')
                break

        if not ok_flag:
            print(' timeout')
    finally:
        page.quit()

print('Done')

"""Poll for device code tokens and save as CPA auth files"""
import json, time, os, sys
sys.stdout.reconfigure(encoding='utf-8')
from curl_cffi import requests as cf_req

CLIENT_ID = 'b1a00492-073a-47ea-816f-4c329264a828'
PROXY = os.getenv('CLIPROXY') or ''
if not PROXY:
    raise SystemExit('缺少 CLIPROXY 环境变量 (真实代理 IP)')
OUTPUT = os.getenv('CPA_AUTHS_DIR') or r'D:\CLIProxyAPIPlus\auths'

with open('pending_codes.json', encoding='utf-8') as f:
    codes = json.load(f)

success = 0
for c in codes:
    email = c['email']
    safe_email = email.replace('@', '_').replace('.', '_')
    if os.path.exists(f'{OUTPUT}/xai-{safe_email}.json'):
        print(f'[{email}] SKIP (already exists)')
        continue

    print(f'[{email}] polling..', end='', flush=True)
    interval = 5
    ok = False
    for i in range(48):  # 4 min max
        time.sleep(interval)
        try:
            r = cf_req.Session(impersonate='chrome120', proxies={'http': PROXY, 'https': PROXY}).post(
                'https://auth.x.ai/oauth2/token',
                data={
                    'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                    'device_code': c['device_code'],
                    'client_id': CLIENT_ID,
                }, timeout=30)
        except Exception as e:
            print(f' net:{e}', end='')
            continue

        if r.status_code == 200:
            d2 = r.json()
            if d2.get('access_token'):
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
                    'mint_method': 'device_code', 'protocol_flow': 'device_code',
                    'headers': {
                        'X-XAI-Token-Auth': 'xai-grok-cli',
                        'x-grok-client-version': '0.2.93',
                        'x-grok-client-identifier': 'grok-shell',
                    },
                }
                if d2.get('id_token'): record['id_token'] = d2['id_token']
                path = f'{OUTPUT}/xai-{safe_email}.json'
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(record, f, ensure_ascii=False, indent=2)
                print(' OK!')
                ok = True
                success += 1
                break

        err = (r.json() or {}).get('error', '')
        if err == 'authorization_pending':
            print('.', end='', flush=True)
        elif err == 'slow_down':
            interval += 5
            print('s', end='', flush=True)
        else:
            print(f' {err}')
            break

    if not ok:
        print(' timeout')

print(f'\nDone: {success}/{len(codes)} converted')

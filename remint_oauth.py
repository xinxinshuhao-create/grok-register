"""一次性重铸 4 个被撤销的 xai OAuth token (2026-08-13)"""
import os, sys, json, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GROK_PROXY", "http://127.0.0.1:7891")  # nexitally 干净 IP

from device_mint import sso_to_device
from sso_to_cpa import save_auth

NEED = [
    "9motusij1h@aws-mail-free-9283.dynv6.net",
    "b8r5yfskag@martenson-mail.mywire.org",
    "v3cj4uvcoh@msn-mail-free-1268.dynv6.net",
    "y1ys3nvcbn@njwzzvmtin.my",
]

# 从 accounts.txt 读 SSO (带 BOM)
sso_map = {}
with open(os.path.join(os.path.dirname(__file__), "keys/accounts.txt"),
          encoding="utf-8-sig") as f:
    for line in f.read().splitlines():
        parts = line.split(":")
        if len(parts) >= 3:
            sso_map[parts[0]] = ":".join(parts[2:])

ok, fail = 0, 0
for email in NEED:
    sso = sso_map.get(email, "")
    if not sso:
        print(f"[SKIP] {email}: accounts.txt 无 SSO")
        fail += 1
        continue
    print(f"\n=== 重铸: {email} ===")
    result = sso_to_device(sso, email)
    if result:
        save_auth(email, result)
        print(f"[OK] {email} 已写入 auths/")
        ok += 1
    else:
        print(f"[FAIL] {email} 铸造失败")
        fail += 1
    time.sleep(5)

print(f"\n完成: OK={ok} FAIL={fail}")

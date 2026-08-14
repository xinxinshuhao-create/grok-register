#!/usr/bin/env python3
"""Microsoft OAuth 授权 — 给 Outlook 号池账号换 refresh token (移植自 unified-mail/graph_auth.py)

用法:
  python outlook_auth.py [account_hint]

流程: 开本地回调服务 → 打开浏览器 → 用户用 Outlook 号登录并同意 →
      回调拿到 code → PKCE 换 token → 存 outlook_tokens.json
      最后 IMAP XOAUTH2 直连验证
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

# Windows GBK 兼容: 强制 stdout/stderr 用 utf-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Thunderbird 公共客户端 ID（第三方应用，个人账户可同意，无需自建 Azure 应用）
# 来源: email-oauth2-proxy 社区 — https://github.com/simonrob/email-oauth2-proxy/discussions/301
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
REDIRECT_URI = os.environ.get("OUTLOOK_REDIRECT", "http://localhost:8228")  # localhost 回环任意端口
AUTHORITY = "https://login.microsoftonline.com/common"
# 个人账户 IMAP 认证要求 outlook.office.com 版 scope（graph 版 token 会被 IMAP 拒绝）
SCOPE = "offline_access openid email https://outlook.office.com/IMAP.AccessAsUser.All"
TOKENS_FILE = os.environ.get("OUTLOOK_TOKENS_FILE") or \
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "outlook_tokens.json")


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _post_token(data: dict) -> dict:
    req = urllib.request.Request(
        f"{AUTHORITY}/oauth2/v2.0/token",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"token 端点返回 {e.code}: {body}") from e


def decode_id_token_account(id_token: str, fallback: str = "") -> str:
    """从 id_token 解出登录账号(不校验签名，仅本地记录用)"""
    if not id_token or "." not in id_token:
        return fallback
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return claims.get("preferred_username") or claims.get("upn") or claims.get("email") or fallback or "unknown"


def wait_for_callback(verifier: str, timeout: int = 180) -> dict:
    """起本地回调服务，返回 {code} 或 {error}"""
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    auth_url = f"{AUTHORITY}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"

    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(q.query)
            if "code" in query:
                got["code"] = query["code"][0]
                page = "<html><body><h3>✅ 授权成功，可以关闭此页面</h3></body></html>"
            elif "error" in query:
                got["error"] = query.get("error_description", query.get("error"))
                page = f"<html><body><h3>❌ 授权失败</h3><pre>{got['error']}</pre></body></html>"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())

        def log_message(self, *a):
            pass

    port = urllib.parse.urlparse(REDIRECT_URI).port or 80
    server = HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    print(f"浏览器将打开授权页，请在 {timeout}s 内登录并点击「同意」", file=sys.stderr)
    print(auth_url, file=sys.stderr)
    webbrowser.open(auth_url)

    deadline = time.time() + timeout
    while not got and time.time() < deadline:
        time.sleep(0.5)
    server.shutdown()
    return got


def main():
    account_hint = sys.argv[1] if len(sys.argv) > 1 else ""

    verifier = _b64url(secrets.token_bytes(32))
    got = wait_for_callback(verifier)
    if "error" in got:
        print(f"ERROR: 授权失败 — {got['error']}", file=sys.stderr)
        sys.exit(1)
    if "code" not in got:
        print("ERROR: 等待授权超时", file=sys.stderr)
        sys.exit(1)

    # 换 token(PKCE，不带 client_secret)
    try:
        token = _post_token({
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": got["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "scope": SCOPE,
        })
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if "refresh_token" not in token:
        print(f"ERROR: 响应里没有 refresh_token: {json.dumps(token)[:500]}", file=sys.stderr)
        sys.exit(1)

    account = decode_id_token_account(token.get("id_token", ""), account_hint) or "unknown"
    print(f"✅ 授权成功，账号: {account}", file=sys.stderr)

    # 合并保存
    saved = {}
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    saved[account] = {
        "refresh_token": token["refresh_token"],
        "scope": SCOPE,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(saved, f, indent=2, ensure_ascii=False)
    print(f"已保存到 {TOKENS_FILE}", file=sys.stderr)

    # IMAP XOAUTH2 验证: 用新 token 直连 outlook.office365.com
    try:
        import imaplib
        conn = imaplib.IMAP4_SSL("outlook.office365.com", 993, timeout=30)
        auth = f"user={account}\x01auth=Bearer {token['access_token']}\x01\x01"
        conn.authenticate("XOAUTH2", lambda _: auth.encode())
        typ, folders = conn.list()
        names = " ".join(str(f) for f in folders)
        junk_ok = "Junk" in names
        typ, data = conn.select("INBOX")
        total = int(data[0]) if typ == "OK" else -1
        conn.logout()
        print(f"✅ IMAP 验证 OK: 收件箱 {total} 封, Junk 文件夹{'存在' if junk_ok else '⚠️不存在'}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️ IMAP 验证失败: {e}", file=sys.stderr)

    print(account)  # stdout 输出账号名


if __name__ == "__main__":
    main()

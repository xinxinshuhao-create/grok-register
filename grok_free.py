"""
Grok 全自动注册 — 免费版 v4
─────────────────────────────
- 邮箱：GPTMail (mail.chatgpt.org.uk) 免费 API — 公共 Key gpt-test，日 20 万次
- Turnstile：DrissionPage 浏览器手动 turnstile.render()
- 验证码：gRPC-web 协议发送 + 验证（对齐原始 grok.py）
- 注册：Next.js Server Action POST（curl_cffi）
- Clash IP 轮换：每次注册前切换代理节点降低风控
- 输出：SSO + email:password:sso → keys/

无需 YesCaptcha / LuckMail / MailTM
"""
import os, re, sys, json, time, random, string, struct, urllib.parse, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from curl_cffi import requests as cf_req
from DrissionPage import ChromiumPage, ChromiumOptions
import requests  # 标准 requests 用于 SSO 跳转，兼容 auth.grokipedia.com 等域名

# ── 配置 ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SITE_URL = "https://accounts.x.ai"
FALLBACK_SITE_KEY = "0x4AAAAAAAhr9JGVDZbrZOo0"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
PROXY = os.getenv("GROK_PROXY") or "http://127.0.0.1:7897"
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "keys")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Clash 轮换器（可选，导入失败则跳过 IP 轮换）
try:
    from clash_rotator import (random_switch, switch_region, get_current_ip,
                                health as clash_health, snapshot, restore,
                                list_fast_nodes)
    HAS_CLASH = True
except ImportError:
    HAS_CLASH = False
    print("[!] clash_rotator 未找到，IP 轮换功能禁用")

# ═══════════════════════ 工具函数 ═══════════════════════

def rand_str(length=15):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))

def rand_name():
    n = random.randint(4, 6)
    return random.choice(string.ascii_uppercase) + ''.join(random.choice(string.ascii_lowercase) for _ in range(n - 1))

def _proxy_dict():
    if not PROXY:
        return None
    return {"http": PROXY, "https": PROXY}

# ── gRPC 编码（对齐原始 grok.py） ──

def encode_grpc_msg(field_id, val):
    key = (field_id << 3) | 2
    vb = val.encode("utf-8")
    payload = struct.pack("B", key) + struct.pack("B", len(vb)) + vb
    return b"\x00" + struct.pack(">I", len(payload)) + payload

def encode_grpc_verify(email, code):
    p1 = struct.pack("B", (1 << 3) | 2) + struct.pack("B", len(email)) + email.encode()
    p2 = struct.pack("B", (2 << 3) | 2) + struct.pack("B", len(code)) + code.encode()
    payload = p1 + p2
    return b"\x00" + struct.pack(">I", len(payload)) + payload

GRPC_HEADERS = {
    "content-type": "application/grpc-web+proto",
    "x-grpc-web": "1",
    "x-user-agent": "connect-es/2.1.1",
    "origin": SITE_URL,
    "referer": f"{SITE_URL}/sign-up?redirect=grok-com",
}

def send_email_code_grpc(session, email):
    """gRPC: 发送邮箱验证码"""
    url = f"{SITE_URL}/auth_mgmt.AuthManagement/CreateEmailValidationCode"
    data = encode_grpc_msg(1, email)
    try:
        res = session.post(url, data=data, headers=GRPC_HEADERS, timeout=15)
        return res.status_code == 200
    except Exception as e:
        print(f"  [!] 发送验证码异常: {e}")
        return False

def verify_email_code_grpc(session, email, code):
    """gRPC: 验证邮箱验证码"""
    url = f"{SITE_URL}/auth_mgmt.AuthManagement/VerifyEmailValidationCode"
    data = encode_grpc_verify(email, code)
    try:
        res = session.post(url, data=data, headers=GRPC_HEADERS, timeout=15)
        return res.status_code == 200
    except Exception as e:
        print(f"  [!] 验证验证码异常: {e}")
        return False


# ═══════════════════════ GPTMail 邮箱（新版 API） ═══════════════════════

class GPTMailInbox:
    """GPTMail 免费临时邮箱 —— 客户端生成邮箱 + inbox-token 注册

    API 流程（2026-07 新版）:
      1. GET  /api/domains/public  → 获取域名列表
      2. 客户端拼邮箱: prefix@random_domain
      3. POST /api/inbox-token     → 注册邮箱，获取 JWT token
      4. GET  /api/emails?email=.. → 轮询邮件
      5. GET  /api/email/{id}      → 获取邮件正文
    """

    def __init__(self):
        proxy_dict = _proxy_dict() if PROXY else None
        self.sess = cf_req.Session(impersonate="chrome120")
        if proxy_dict:
            self.sess.proxies = proxy_dict
        self.email = ""
        self.token = ""
        self._domains = []

    def _get_domains(self):
        """获取活跃域名列表"""
        if self._domains:
            return self._domains
        try:
            # 预热
            self.sess.get("https://mail.chatgpt.org.uk/", timeout=15)
        except Exception:
            pass
        r = self.sess.get("https://mail.chatgpt.org.uk/api/domains/public", timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"获取域名失败: {r.status_code}")
        data = r.json()
        domains_list = (data.get("data") or {}).get("domains") or []
        self._domains = [d["domain_name"] for d in domains_list if d.get("is_active")]
        if not self._domains:
            raise RuntimeError("无活跃域名")
        return self._domains

    def create(self):
        """生成邮箱并注册"""
        domains = self._get_domains()
        prefix = rand_str(10)
        domain = random.choice(domains)
        self.email = f"{prefix}@{domain}"

        # 注册邮箱到 inbox token
        r = self.sess.post(
            "https://mail.chatgpt.org.uk/api/inbox-token",
            headers={"Content-Type": "application/json"},
            json={"email": self.email},
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"inbox-token 失败: {r.status_code}")
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"inbox-token 返回失败: {data}")
        self.token = (data.get("auth") or {}).get("token") or ""
        if not self.token:
            raise RuntimeError("未获取到 inbox token")
        return self.email

    def wait_code(self, timeout=60, interval=5):
        """轮询 GPTMail 获取验证码"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(interval)
            try:
                r = self.sess.get(
                    f"https://mail.chatgpt.org.uk/api/emails?email={urllib.parse.quote(self.email)}",
                    headers={"x-inbox-token": self.token},
                    timeout=15,
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                emails = (data.get("data") or {}).get("emails") or data.get("data") or []
                if isinstance(emails, dict):
                    emails = [emails]
                for msg in (emails if isinstance(emails, list) else []):
                    subject = str(msg.get("subject") or "")
                    body = str(msg.get("text") or msg.get("html") or msg.get("body") or "")
                    text = subject + " " + body
                    m = re.search(r"([A-Z0-9]{3})-?([A-Z0-9]{3})", text)
                    if m:
                        return m.group(1) + m.group(2)

                # 如果邮件列表有 ID，获取详情
                for msg in (emails if isinstance(emails, list) else []):
                    msg_id = msg.get("id") or msg.get("message_id")
                    if msg_id:
                        r2 = self.sess.get(
                            f"https://mail.chatgpt.org.uk/api/email/{urllib.parse.quote(str(msg_id))}",
                            headers={"x-inbox-token": self.token},
                            timeout=15,
                        )
                        if r2.status_code == 200:
                            detail = r2.json()
                            d = (detail.get("data") or detail)
                            text2 = str(d.get("subject") or "") + " " + str(d.get("text") or d.get("html") or d.get("body") or "")
                            m = re.search(r"([A-Z0-9]{3})-?([A-Z0-9]{3})", text2)
                            if m:
                                return m.group(1) + m.group(2)
            except Exception:
                continue
        return None


# ═══════════════════════ 浏览器初始化 ═══════════════════════

def browser_init():
    """打开注册页 → 获取 Action ID + Site Key + Turnstile Token + State Tree"""

    co = ChromiumOptions()
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--incognito")
    # 随机窗口大小减少指纹一致性
    w = random.randint(1200, 1400)
    h = random.randint(800, 1000)
    co.set_argument(f"--window-size={w},{h}")
    if PROXY:
        proxy_addr = PROXY.replace("http://", "").replace("https://", "")
        co.set_argument(f"--proxy-server={proxy_addr}")
    page = ChromiumPage(co)

    print("[Browser] 打开注册页...")
    page.get(f"{SITE_URL}/sign-up?redirect=grok-com")
    time.sleep(4)

    html = page.html
    if not html:
        raise RuntimeError("页面加载失败")

    # --- Site Key ---
    site_key = FALLBACK_SITE_KEY
    m = re.search(r'sitekey":"(0x4[a-zA-Z0-9_-]+)"', html)
    if m:
        site_key = m.group(1)
    print(f"[Browser] SiteKey: {site_key}")

    # --- Action ID ---
    js_urls = re.findall(r"/_next/static/chunks/[^\"'\s>]+\.js", html)
    action_id = None
    js_sess = cf_req.Session(impersonate="chrome120")
    if PROXY:
        js_sess.proxies = {"http": PROXY, "https": PROXY}
    for js_path in js_urls:
        url = js_path if js_path.startswith("http") else f"{SITE_URL}{js_path}"
        try:
            js = js_sess.get(url, timeout=15).text
            # 支持新旧两种格式: release:"hex40" 或旧版 7f+hex40
            m = re.search(r'release[:\s]*["\']([a-fA-F0-9]{40})["\']', js)
            if not m:
                m = re.search(r'7f[a-fA-F0-9]{40}', js)
            if m:
                action_id = m.group(1) if m.lastindex else m.group(0)
                print(f"[Browser] ActionID: {action_id}")
                break
        except Exception:
            continue
    if not action_id:
        raise RuntimeError("未找到 Action ID，无法注册")

    # --- State Tree ---
    state_tree = ""
    m = re.search(r'next-router-state-tree":"([^"]+)"', html)
    if m:
        state_tree = m.group(1)

    # --- 点击邮箱注册选项，触发自然 Turnstile ---
    print("[Browser] 点击邮箱注册选项...")
    page.run_js('''
    var all=document.querySelectorAll('button,[role=button]');
    for(var i=0;i<all.length;i++){
        if(!all[i].offsetParent) continue;
        var t=(all[i].innerText||'').trim();
        if(t.indexOf('邮箱')>=0||t.indexOf('email')>=0||t.indexOf('Email')>=0){
            all[i].click(); break;
        }
    }
    ''')
    time.sleep(3)

    # --- Turnstile: 优先等待页面自带的 cf-turnstile-response 填充 ---
    print("[Browser] 等待 Turnstile 解决...")
    ts_token = ""
    for attempt in range(45):
        time.sleep(2)
        ts_token = page.run_js(
            'return document.querySelector("[name=cf-turnstile-response]")?.value||""')
        if len(ts_token) > 50:
            print(f"[Browser] 自然 Turnstile 已解决（尝试{attempt+1}次）")
            break
        # 检查是否有 iframe/challenge
        has_challenge = page.run_js(
            'return document.querySelector("iframe[src*=turnstile],iframe[src*=challenges]")!==null')
        if has_challenge and attempt == 0:
            print("[Browser] Turnstile challenge 已出现，等待解决...")

    if len(ts_token) < 50:
        # 手动渲染 fallback（70s 超时，需大于 Turnstile 的 60s）
        print("[Browser] 自然等待超时，尝试手动渲染（70s超时）...")
        ts_token = page.run_js('''
        var _sitekey = arguments[0];
        return new Promise(function(resolve, _reject) {
            var sitekey = _sitekey;
            var tsDiv = document.createElement('div');
            tsDiv.id = '_grok_free_ts';
            tsDiv.style.cssText = 'position:fixed;top:10px;right:10px;z-index:99999';
            document.body.appendChild(tsDiv);

            var timeout = setTimeout(function() { resolve('timeout'); }, 60000);

            try {
                turnstile.render('#'+tsDiv.id, {
                    sitekey: sitekey, theme: 'light',
                    callback: function(token) {
                        clearTimeout(timeout);
                        var hidden = document.querySelector('[name="cf-turnstile-response"]');
                        if (hidden) hidden.value = token;
                        resolve(token);
                    },
                    'error-callback': function(e) {
                        clearTimeout(timeout);
                        resolve('error:' + (e && e.message ? e.message : String(e)));
                    }
                });
            } catch(e) {
                clearTimeout(timeout);
                resolve('exception:' + (e && e.message ? e.message : String(e)));
            }
        });
        ''', site_key, timeout=75)
        print(f"[Browser] 手动渲染结果: {ts_token[:80] if ts_token else 'None'}...")

    if not ts_token or ts_token.startswith('timeout') or ts_token.startswith('error'):
        # 尝试直接从已有字段获取
        ts_token = page.run_js(
            'return document.querySelector("[name=cf-turnstile-response]")?.value||""')
        if len(ts_token) < 50:
            page.quit()
            raise RuntimeError(f"Turnstile 未解决: {ts_token}")

    print(f"[Browser] Turnstile 已解决（{len(ts_token)} chars）")

    return {
        "site_key": site_key,
        "action_id": action_id,
        "state_tree": state_tree,
        "ts_token": ts_token,
        "page": page,
    }


# ═══════════════════════ 注册流程 ═══════════════════════

def solve_turnstile(page, site_key):
    """在浏览器中解决 Turnstile，返回新 token。每次注册前调用"""
    # 刷新注册页
    page.get(f"{SITE_URL}/sign-up?redirect=grok-com")
    time.sleep(3)

    # 点击邮箱选项
    page.run_js('''
    var all=document.querySelectorAll('button,[role=button]');
    for(var i=0;i<all.length;i++){
        if(!all[i].offsetParent) continue;
        var t=(all[i].innerText||'').trim();
        if(t.indexOf('邮箱')>=0||t.indexOf('email')>=0||t.indexOf('Email')>=0){
            all[i].click(); break;
        }
    }
    ''')
    time.sleep(2)

    # 等待自然 Turnstile 解决
    ts_token = ""
    for attempt in range(35):
        ts_token = page.run_js(
            'return document.querySelector("[name=cf-turnstile-response]")?.value||""')
        if len(ts_token) > 50:
            break

    # fallback: 手动渲染
    if len(ts_token) < 50:
        ts_token = page.run_js('''
        var _sitekey = arguments[0];
        return new Promise(function(resolve, _reject) {
            var sitekey = _sitekey;
            var tsDiv = document.createElement('div');
            tsDiv.id = '_grok_fresh_ts';
            tsDiv.style.cssText = 'position:fixed;top:10px;right:10px;z-index:99999';
            document.body.appendChild(tsDiv);
            var timeout = setTimeout(function() { resolve('timeout'); }, 60000);
            try {
                turnstile.render('#'+tsDiv.id, {
                    sitekey: sitekey, theme: 'light',
                    callback: function(token) {
                        clearTimeout(timeout);
                        var hidden = document.querySelector('[name="cf-turnstile-response"]');
                        if (hidden) hidden.value = token;
                        resolve(token);
                    },
                    'error-callback': function(e) {
                        clearTimeout(timeout);
                        resolve('error:' + (e && e.message ? e.message : String(e)));
                    }
                });
            } catch(e) {
                clearTimeout(timeout);
                resolve('exception:' + (e && e.message ? e.message : String(e)));
            }
        });
        ''', site_key, timeout=75)

    if not ts_token or ts_token.startswith('timeout') or ts_token.startswith('error'):
        return None
    return ts_token


def register_one(cfg):
    """注册单个账号 → 返回 (email, password, sso) 或 None"""

    # ── 创建邮箱 ──
    print("[Mail] 创建 Gmail 别名...")
    try:
        from email_service import GmailIMAPClient
        mail = GmailIMAPClient()
        email = mail.create_email()
    except Exception as e:
        print(f"[Mail] Gmail 失败: {e}")
        return None
    print(f"[Mail] {email}")

    # ── curl_cffi session ──
    sess = cf_req.Session(impersonate="chrome120")
    if PROXY:
        sess.proxies = {"http": PROXY, "https": PROXY}
    try:
        sess.get(SITE_URL, timeout=10)
    except Exception:
        pass

    # ── Step 1: 发送验证码 (gRPC) ──
    print(f"[{email}] 发送验证码 (gRPC)...")
    if not send_email_code_grpc(sess, email):
        print(f"[{email}] 发送验证码失败")
        return None
    print(f"[{email}] 验证码已发送")

    # ── Step 2: 等待验证码 ──
    print(f"[{email}] 等待验证码...")
    code = None
    for _ in range(36):  # 36 x 5s = 180s
        time.sleep(5)
        content = mail.fetch_first_email()
        if content:
            m = re.search(r"([A-Z0-9]{3})-?([A-Z0-9]{3})", content)
            if m:
                code = m.group(1) + m.group(2)
                break
    if not code:
        print(f"[{email}] 未收到验证码（超时）")
        return None
    print(f"[{email}] 验证码: {code}")

    # ── Step 3: 验证验证码 (gRPC) ──
    print(f"[{email}] 验证验证码 (gRPC)...")
    if not verify_email_code_grpc(sess, email, code):
        print(f"[{email}] 验证码无效")
        return None
    print(f"[{email}] 验证码正确")

    # ── Step 4: 刷新 Turnstile token（每次注册用新 token） ──
    print(f"[{email}] 刷新 Turnstile...")
    ts_token = solve_turnstile(cfg["page"], cfg["site_key"])
    if not ts_token:
        print(f"[{email}] Turnstile 刷新失败")
        return None
    print(f"[{email}] Turnstile 已刷新（{len(ts_token)} chars）")

    # ── Step 5: 准备注册数据 ──
    password = rand_str(14) + "Aa1!"
    first = rand_name()
    last = rand_name()

    # ── Step 6: 注册 POST ──
    print(f"[{email}] 提交注册...")
    try:
        sess.get(SITE_URL, timeout=10)
    except Exception:
        pass

    cf_bm = sess.cookies.get("__cf_bm", "")
    headers = {
        "user-agent": UA,
        "accept": "text/x-component",
        "content-type": "text/plain;charset=UTF-8",
        "origin": SITE_URL,
        "referer": f"{SITE_URL}/sign-up",
        "cookie": f"__cf_bm={cf_bm}",
        "next-router-state-tree": cfg["state_tree"],
        "next-action": cfg["action_id"],
    }

    payload = [{
        "emailValidationCode": code,
        "createUserAndSessionRequest": {
            "email": email,
            "givenName": first,
            "familyName": last,
            "clearTextPassword": password,
            "tosAcceptedVersion": "$undefined",
        },
        "turnstileToken": ts_token,
        "promptOnDuplicateEmail": True,
    }]

    try:
        r = sess.post(f"{SITE_URL}/sign-up", json=payload, headers=headers, timeout=30)
        print(f"[{email}] POST 状态: {r.status_code}")
    except Exception as e:
        print(f"[{email}] POST 异常: {e}")
        return None

    if r.status_code != 200:
        print(f"[{email}] 注册失败: {r.text[:300]}")
        return None

    # ── Step 7: 提取 SSO ──
    resp_text = r.text
    # 多种 SSO URL 匹配尝试
    sso_url = None
    for pat in [
        r'(https://[^"\s]+set-cookie\?q=[^:"\s]+)1:',
        r'(https://[^"\s]+set-cookie\?q=[^"\s]+)',
        r'https://[^"\s]*set-cookie[^"\s]*',
    ]:
        m = re.search(pat, resp_text)
        if m:
            sso_url = m.group(0).rstrip("1:")
            break

    if sso_url:
        # 清理 URL 末尾残留
        sso_url = re.sub(r'[:\d]*$', '', sso_url) if sso_url.endswith(('1:', '2:', '3:')) else sso_url
        print(f"[{email}] SSO URL: {sso_url[:100]}...")

        # 用标准 requests 获取 SSO（curl_cffi 对 auth.grokipedia.com TLS 不兼容）
        sso = None
        try:
            rs = requests.Session()
            if PROXY:
                rs.proxies = {"http": PROXY, "https": PROXY}
            rs.get(sso_url, allow_redirects=True, timeout=15,
                   headers={"User-Agent": UA})
            sso = rs.cookies.get("sso")
        except Exception as e:
            print(f"[{email}] SSO 标准请求异常: {e}，回退 curl_cffi...")
            try:
                sess.get(sso_url, allow_redirects=True, timeout=15)
                sso = sess.cookies.get("sso")
            except Exception as e2:
                print(f"[{email}] SSO curl_cffi 也失败: {e2}")

        if sso:
            print(f"[{email}] ✅ SSO: {sso[:30]}...")
            with open(os.path.join(OUTPUT_DIR, "grok.txt"), "a", encoding="utf-8") as f:
                f.write(sso + "\n")
            with open(os.path.join(OUTPUT_DIR, "accounts.txt"), "a", encoding="utf-8") as f:
                f.write(f"{email}:{password}:{sso}\n")
            return (email, password, sso)
        else:
            print(f"[{email}] 无 SSO cookie")
    else:
        print(f"[{email}] 响应中无 SSO URL，前300字符:")
        print(f"  {resp_text[:300]}")

    return None


# ═══════════════════════ 主程序 ═══════════════════════

def main():
    parser = argparse.ArgumentParser(description="Grok 全自动注册")
    parser.add_argument("--count", type=int, default=3, help="注册数量（默认 3）")
    parser.add_argument("--no-rotate", action="store_true", help="禁用 IP 轮换")
    parser.add_argument("--rotate-interval", type=int, default=1,
                        help="每注册 N 个后切换 IP（默认 1，即每次切换）")
    parser.add_argument("--min-delay", type=int, default=8, help="注册间隔最小值（秒，默认 8）")
    parser.add_argument("--max-delay", type=int, default=25, help="注册间隔最大值（秒，默认 25）")
    parser.add_argument("--rotate-region", action="store_true", help="切换不同区域节点（而非随机节点）")
    args = parser.parse_args()

    print("=" * 55)
    print(f"Grok 注册 · 免费版 v4 (GPTMail + gRPC + Clash)")
    print(f"数量: {args.count}  IP轮换: {'✅' if not args.no_rotate and HAS_CLASH else '❌'}")
    print("=" * 55)

    # ── Clash: 快照当前节点（注册完恢复） ──
    original_node = None
    if not args.no_rotate and HAS_CLASH:
        try:
            original_node = snapshot()
            h = clash_health()
            # 显示低延迟节点数
            fast, slow = list_fast_nodes()
            print(f"[Clash] 快照: {h['current_node'][:40]}")
            print(f"[Clash] 出口 IP: {h['current_ip']}  ({h['region']})")
            print(f"[Clash] 低延迟节点: {len(fast)}  慢/断线: {len(slow)}")
            if slow:
                for n, reason in slow[:3]:
                    print(f"[Clash]   ⚠ {n[:35]} — {reason}")
        except Exception as e:
            print(f"[Clash] ⚠️ 检查失败: {e}")

    # 1. 浏览器初始化（获取 Turnstile token + Action ID 等），失败重试
    cfg = None
    for retry in range(3):
        try:
            cfg = browser_init()
            break
        except RuntimeError as e:
            print(f"\n[!] 浏览器初始化失败 (尝试 {retry+1}/3): {e}")
            if retry < 2 and HAS_CLASH and not args.no_rotate:
                try:
                    # 换个区域重试 Turnstile
                    random_switch()
                    print(f"[Clash] 切换区域后重试...")
                except Exception:
                    pass
            time.sleep(5)
    if not cfg:
        print("[!] 浏览器初始化失败，放弃")
        return

    # 2. 注册循环
    success = 0
    fail = 0
    t0 = time.time()

    # 使用过的区域追踪（避免重复用同一区域）
    used_regions = set()

    try:
        for i in range(args.count):
            print(f"\n{'─'*40}")
            print(f"第 {i+1}/{args.count} 次注册")
            print(f"{'─'*40}")

            # ── IP 轮换 ──
            if not args.no_rotate and HAS_CLASH and i > 0 and i % args.rotate_interval == 0:
                try:
                    if args.rotate_region:
                        switch_region(exclude_regions=used_regions)
                    else:
                        random_switch()
                    new_ip = get_current_ip()
                    if new_ip:
                        print(f"  [IP] 新出口 IP: {new_ip}")
                except Exception as e:
                    print(f"  [IP] ⚠️ 切换失败: {e}，继续使用当前 IP")

            try:
                result = register_one(cfg)
                if result:
                    success += 1
                    avg = (time.time() - t0) / success
                    print(f"  ✅ 成功={success} 失败={fail} 均速={avg:.0f}s/个")
                else:
                    fail += 1
                    print(f"  ❌ 失败 成功={success} 失败={fail}")
            except KeyboardInterrupt:
                break
            except Exception as e:
                fail += 1
                print(f"[!] 异常: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(5)

            # ── 随机间隔（最后一个不需要等待） ──
            if i < args.count - 1:
                delay = random.uniform(args.min_delay, args.max_delay)
                print(f"  ⏳ 等待 {delay:.1f}s...")
                time.sleep(delay)
    finally:
        if cfg and cfg.get("page"):
            cfg["page"].quit()
        # ── 恢复原始节点 ──
        if original_node and HAS_CLASH:
            try:
                restore(original_node)
            except Exception as e:
                print(f"[Clash] ⚠️ 恢复节点失败: {e}")

    elapsed = time.time() - t0
    print(f"\n{'='*55}")
    print(f"结束。成功={success} 失败={fail} 耗时={elapsed:.0f}s")
    if success > 0:
        print(f"SSO 已保存至: {OUTPUT_DIR}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()

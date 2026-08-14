import os, json, random, string, time, re, struct, argparse
import threading
import concurrent.futures
import sys
from urllib.parse import urljoin, urlparse
from curl_cffi import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from email_service import EmailService
from YesCaptcha_service import TurnstileService

# 基础配置
site_url = "https://accounts.x.ai"
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
_proxy_url = os.getenv("GROK_PROXY") or "http://127.0.0.1:7897"
PROXIES = {
    "http": _proxy_url,
    "https": _proxy_url
}

# 动态获取的全局变量
config = {
    "site_key": "0x4AAAAAAAhr9JGVDZbrZOo0",
    "action_id": None,
    "state_tree": "%5B%22%22%2C%7B%22children%22%3A%5B%22(app)%22%2C%7B%22children%22%3A%5B%22(auth)%22%2C%7B%22children%22%3A%5B%22sign-up%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2C%22%2Fsign-up%22%2C%22refresh%22%5D%7D%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%2Ctrue%5D"
}

post_lock = threading.Lock()
file_lock = threading.Lock()
count_lock = threading.Lock()
stop_event = threading.Event()
success_count = 0
completed_count = 0
target_count = 0  # 0 = 无限
start_time = time.time()
EMAIL_PROVIDER = str(os.getenv("EMAIL_PROVIDER") or "luckmail").strip().lower()

def generate_random_name() -> str:
    length = random.randint(4, 6)
    return random.choice(string.ascii_uppercase) + ''.join(random.choice(string.ascii_lowercase) for _ in range(length - 1))

def generate_random_string(length: int = 15) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))

def encode_grpc_message(field_id, string_value):
    key = (field_id << 3) | 2
    value_bytes = string_value.encode('utf-8')
    length = len(value_bytes)
    payload = struct.pack('B', key) + struct.pack('B', length) + value_bytes
    return b'\x00' + struct.pack('>I', len(payload)) + payload

def encode_grpc_message_verify(email, code):
    p1 = struct.pack('B', (1 << 3) | 2) + struct.pack('B', len(email)) + email.encode('utf-8')
    p2 = struct.pack('B', (2 << 3) | 2) + struct.pack('B', len(code)) + code.encode('utf-8')
    payload = p1 + p2
    return b'\x00' + struct.pack('>I', len(payload)) + payload

def send_email_code_grpc(session, email):
    url = f"{site_url}/auth_mgmt.AuthManagement/CreateEmailValidationCode"
    data = encode_grpc_message(1, email)
    headers = {"content-type": "application/grpc-web+proto", "x-grpc-web": "1", "x-user-agent": "connect-es/2.1.1", "origin": site_url, "referer": f"{site_url}/sign-up?redirect=grok-com"}
    try:
        # print(f"[debug] {email} 正在发送验证码请求...")
        res = session.post(url, data=data, headers=headers, timeout=15)
        # print(f"[debug] {email} 请求结束，状态码: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        print(f"[-] {email} 发送验证码异常: {e}")
        return False

def verify_email_code_grpc(session, email, code):
    url = f"{site_url}/auth_mgmt.AuthManagement/VerifyEmailValidationCode"
    data = encode_grpc_message_verify(email, code)
    headers = {"content-type": "application/grpc-web+proto", "x-grpc-web": "1", "x-user-agent": "connect-es/2.1.1", "origin": site_url, "referer": f"{site_url}/sign-up?redirect=grok-com"}
    try:
        print(f"[debug] {email} 验证码: {code}, 状态码检查...")
        res = session.post(url, data=data, headers=headers, timeout=15)
        # print(f"[debug] {email} 验证响应状态: {res.status_code}, 内容长度: {len(res.content)}")
        return res.status_code == 200
    except Exception as e:
        print(f"[-] {email} 验证验证码异常: {e}")
        return False

def register_single_thread(email_provider: str = "gptmail"):
    # 错峰启动，防止瞬时并发过高
    time.sleep(random.uniform(0, 5))

    try:
        email_service = EmailService(proxies=PROXIES, provider=email_provider)
        turnstile_service = TurnstileService()
    except Exception as e:
        print(f"[-] 服务初始化失败: {e}")
        return

    # 从 config 获取 action_id，缺少则直接退出
    final_action_id = config.get("action_id")
    if not final_action_id:
        print("[-] 线程退出：缺少 Action ID")
        return

    while not stop_event.is_set():
        try:
            with requests.Session(impersonate="chrome120", proxies=PROXIES) as session:
                # 预热连接
                try: session.get(site_url, timeout=10)
                except: pass

                password = generate_random_string()
                
                # print(f"[debug] 线程-{threading.get_ident()} 正在请求创建邮箱...")
                try:
                    jwt, email = email_service.create_email()
                except Exception as e:
                    print(f"[-] 邮箱服务抛出异常: {e}")
                    jwt, email = None, None

                if not email:
                    print(f"[-] 线程-{threading.get_ident()} 邮箱创建返回空，可能接口挂了或超时，等待 5s...")
                    time.sleep(5); continue
                
                print(f"[*] 开始注册: {email}")

                # Step 1: 发送验证码
                if not send_email_code_grpc(session, email):
                    print(f"[-] {email} 发送验证码失败")
                    time.sleep(5); continue
                
                # Step 2: 获取验证码
                verify_code = None
                for _ in range(12):
                    time.sleep(5)
                    content = email_service.fetch_first_email(jwt)
                    if content:
                        # 兼容新格式："SZ0-0SW xAI confirmation code" 以及 HTML 中的 "SZ0-0SW"
                        match = re.search(r"([A-Z0-9]{3}-[A-Z0-9]{3})", content)
                        if match:
                            verify_code = match.group(1).replace("-", "")
                            break
                if not verify_code:
                    print(f"[-] {email} 未收到验证码")
                    continue

                # Step 3: 先解 Turnstile（最耗时），避免验证码过期
                ts_token = None
                for ts_attempt in range(3):
                    task_id = turnstile_service.create_task(site_url, config["site_key"])
                    ts_token = turnstile_service.get_response(task_id)
                    if ts_token and ts_token != "CAPTCHA_FAIL":
                        break
                    print(f"[-] {email} CAPTCHA 失败，重试...")
                    time.sleep(2)
                if not ts_token or ts_token == "CAPTCHA_FAIL":
                    print(f"[-] {email} CAPTCHA 全部失败")
                    continue

                # Step 4: 直接提交注册（跳过预验证，避免消耗验证码）
                for attempt in range(1):  # 只试一次，失败换号重来
                    headers = {
                        "user-agent": user_agent, "accept": "text/x-component", "content-type": "text/plain;charset=UTF-8",
                        "origin": site_url, "referer": f"{site_url}/sign-up", "cookie": f"__cf_bm={session.cookies.get('__cf_bm','')}",
                        "next-router-state-tree": config["state_tree"],
                    }
                    if final_action_id:
                        headers["next-action"] = final_action_id
                    payload = [{
                        "emailValidationCode": verify_code,
                        "createUserAndSessionRequest": {
                            "email": email, "givenName": generate_random_name(), "familyName": generate_random_name(),
                            "clearTextPassword": password, "tosAcceptedVersion": "$undefined"
                        },
                        "turnstileToken": ts_token, "promptOnDuplicateEmail": True
                    }]
                    
                    with post_lock:
                        res = session.post(f"{site_url}/sign-up", json=payload, headers=headers)
                    
                    if res.status_code == 200:
                        # 尝试多种 SSO 提取方式
                        sso = None
                        # 方式1: set-cookie?q= URL (老格式)
                        for pat in [
                            r'(https://[^"\s]+set-cookie\?q=[^:"\s]+)',
                            r'(https://[^"\s]+set-cookie[^"\s]+)',
                        ]:
                            m = re.search(pat, res.text)
                            if m:
                                sso_url = m.group(0).rstrip("1:").rstrip("2:").rstrip("3:")
                                try:
                                    session.get(sso_url, allow_redirects=True, timeout=15)
                                except:
                                    pass
                                sso = session.cookies.get("sso")
                                if sso:
                                    break
                        # 方式2: 直接从 response cookies 取
                        if not sso:
                            sso = session.cookies.get("sso")
                        # 方式3: 检查 Set-Cookie header
                        if not sso:
                            set_cookie = res.headers.get("set-cookie", "")
                            for c in set_cookie.split(","):
                                if "sso=" in c:
                                    sso_val = c.split("sso=")[1].split(";")[0]
                                    if sso_val:
                                        sso = sso_val
                                        break
                        # 判断：如果响应中包含明确的 invalid-code 错误才是真失败
                        if '"error"' in res.text and 'invalid' in res.text.lower():
                            if not sso:
                                print(f"[-] {email} 验证码无效: {res.text[:150]}")
                            # 如果有 sso 还是算成功（响应格式混乱时）

                        if sso:
                            with file_lock:
                                os.makedirs("keys", exist_ok=True)
                                with open("keys/grok.txt", "a") as f: f.write(sso + "\n")
                                with open("keys/accounts.txt", "a") as f: f.write(f"{email}:{password}:{sso}\n")
                                global success_count, completed_count
                                success_count += 1
                                completed_count += 1
                                avg = (time.time() - start_time) / success_count

                            if target_count > 0 and completed_count >= target_count:
                                stop_event.set()

                            print(f"[OK] 注册成功: {email} | SSO: {sso[:15]}... | 平均: {avg:.1f}s | 进度: {completed_count}/{target_count if target_count else '无限'}")
                            break
                        elif '"error"' not in res.text or 'invalid' not in res.text.lower():
                            # 无明显错误但也没 SSO，打印更多信息调试
                            print(f"[-] {email} 无 SSO (200 OK, len={len(res.text)}): {res.text[:150]}")
                        # else: 有 invalid 错误且无 SSO，已在上面的 if 打印
                    else:
                        print(f"[-] {email} 提交失败 ({res.status_code}): {res.text[:200]}")
                    time.sleep(2)
                else:
                    print(f"[-] {email} 放弃，换号")
                    time.sleep(5)

        except Exception as e:
            # 捕获所有异常防止线程退出
            print(f"[-] 异常: {str(e)[:50]}")
            time.sleep(5)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email-provider", choices=["gptmail", "luckmail", "mailtm", "gmail", "tmail", "fce", "outlook"], default=os.getenv("EMAIL_PROVIDER", "luckmail"), help="邮箱提供商：gptmail/luckmail/mailtm/gmail/tmail/fce/outlook")
    parser.add_argument("--threads", type=int, default=None, help="并发线程数")
    parser.add_argument("--count", type=int, default=0, help="总注册数量（0=无限）")
    args = parser.parse_args()

    global target_count
    target_count = args.count

    print("=" * 60 + "\nGrok 注册机\n" + "=" * 60)
    print(f"[*] 当前邮箱提供商: {args.email_provider}")
    print(f"[*] 目标数量: {args.count if args.count else '无限'}")

    # 1. 扫描参数
    print("[*] 正在初始化...")
    start_url = f"{site_url}/sign-up"
    with requests.Session(impersonate="chrome120", proxies=PROXIES) as s:
        try:
            html = s.get(start_url).text
            # Key
            key_match = re.search(r'sitekey":"(0x4[a-zA-Z0-9_-]+)"', html)
            if key_match: config["site_key"] = key_match.group(1)
            # Tree
            tree_match = re.search(r'next-router-state-tree":"([^"]+)"', html)
            if tree_match: config["state_tree"] = tree_match.group(1)
            # Action ID — 并发抓取所有 JS 文件（用标准 requests，线程安全+快速）
            js_urls = list(set(urljoin(start_url, m.group(0)) for m in re.finditer(r"/_next/static/chunks/[^\"'\s>]+\.js", html)))
            if not js_urls:
                preview = html[:500].replace("\n", " ")
                print(f"[Warn] HTML 长度 {len(html)}, 未解析出 JS，前500字符预览: {preview}")
            action_found = None
            print(f"[*] 并发搜索 {len(js_urls)} 个 JS 文件中的 Action ID...")

            def _fetch_and_search(url):
                """用标准 requests（线程安全），快速扫描 JS 文件找 Action ID"""
                import requests as _req
                try:
                    js = _req.get(url, proxies={"http": PROXIES["http"], "https": PROXIES["https"]}, timeout=10).text
                    m = re.search(r'7f[a-fA-F0-9]{40}', js)
                    if m:
                        return m.group(0)
                except Exception:
                    pass
                return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                for result in pool.map(_fetch_and_search, js_urls):
                    if result:
                        action_found = result
                        pool.shutdown(wait=False, cancel_futures=True)
                        break

            if action_found:
                config["action_id"] = action_found
                print(f"[+] Action ID: {action_found}")
                try:
                    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".action_id.cache"), "w").write(action_found)
                except Exception:
                    pass
            else:
                # 回退缓存 (2026-08-06: 扫描间歇失败时用上次成功的 ID)
                try:
                    cached = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".action_id.cache")).read().strip()
                    if re.match(r"^7f[a-fA-F0-9]{40}$", cached):
                        config["action_id"] = cached
                        print(f"[+] 使用缓存 Action ID: {cached}")
                except Exception:
                    pass
        except Exception as e:
            print(f"[-] 初始化扫描失败: {e}")
            return

    if not config["action_id"]:
        print("[-] 错误: 未找到 Action ID")
        return

    # 2. 启动
    if args.threads is not None:
        t = args.threads
    else:
        try:
            t = int(input("\n并发数 (默认1): ").strip() or 1)
        except:
            t = 1
    
    print(f"[*] 启动 {t} 个线程...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=t) as executor:
        # 只提交与线程数相等的任务，让它们在内部无限循环
        futures = [executor.submit(register_single_thread, args.email_provider) for _ in range(t)]
        try:
            concurrent.futures.wait(futures)
        except KeyboardInterrupt:
            print("\n[!] 收到中断信号，准备退出...")

if __name__ == "__main__":
    main()

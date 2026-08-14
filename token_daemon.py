"""
Token 自动刷新守护进程
────────────────────
后台监控 auths/ 目录中的所有 xAI token，在过期前自动刷新。

用法:
  python token_daemon.py                # 一次性检查并刷新
  python token_daemon.py --daemon 300   # 每 5 分钟守护刷新
  python token_daemon.py --pre 1800     # 提前 30 分钟刷新
"""
import os, sys, json, time, argparse
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import requests


def _utc_to_ts(utc_str):
    """解析 UTC ISO 时间戳 → Unix timestamp（修正时区问题）"""
    try:
        return datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, OverflowError):
        return None

AUTH_DIR = os.getenv("CPA_AUTHS_DIR") or r"D:\CLIProxyAPIPlus\auths"
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
PROXY = os.getenv("GROK_PROXY") or "http://127.0.0.1:7897"
TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:56121/callback"


def get_session():
    s = requests.Session()
    if PROXY:
        s.proxies = {"http": PROXY, "https": PROXY}
    return s


def load_accounts():
    """加载所有非禁用账号"""
    if not os.path.isdir(AUTH_DIR):
        return []
    accounts = []
    for fn in sorted(os.listdir(AUTH_DIR)):
        if not fn.startswith("xai-") or not fn.endswith(".json"):
            continue
        path = os.path.join(AUTH_DIR, fn)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("access_token") and not d.get("disabled"):
                d["_path"] = path
                d["_fn"] = fn
                accounts.append(d)
        except Exception as e:
            print(f"[SKIP] {fn}: {e}")
    return accounts


def expires_in(data):
    """返回 token 剩余秒数（负数表示已过期）"""
    expired_str = data.get("expired", "")
    if not expired_str:
        return None
    try:
        ts = _utc_to_ts(expired_str)
        return int(ts - time.time()) if ts else None
    except (ValueError, OverflowError):
        return None


def refresh_one(data):
    """刷新单个账号的 token，失败返回 False"""
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        print(f"  [{data.get('email','?')}] 无 refresh_token，跳过")
        return False

    print(f"  [{data.get('email','?')}] 刷新中...")
    s = get_session()
    try:
        r = s.post(
            data.get("token_endpoint", TOKEN_ENDPOINT),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
            },
            timeout=30,
        )
    except Exception as e:
        print(f"  [{data.get('email','?')}] 刷新网络错误: {e}")
        return False

    if r.status_code != 200:
        print(f"  [{data.get('email','?')}] 刷新失败: {r.status_code} {r.text[:150]}")
        # 如果是 invalid_grant，标记禁用（必须落盘，否则 CPA 继续轮转死 token）
        try:
            err = r.json()
            if "invalid_grant" in str(err):
                print(f"  [{data.get('email','?')}] ⛔ 账号已被封禁，标记 disabled")
                data["disabled"] = True
                data["disabled_reason"] = "refresh_token_invalid"
                with open(data["_path"], "w", encoding="utf-8") as f:
                    json.dump({k: v for k, v in data.items() if not k.startswith("_")},
                              f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return False

    new = r.json()
    new_access = new.get("access_token")
    if not new_access:
        print(f"  [{data.get('email','?')}] 响应无 access_token")
        return False

    data["access_token"] = new_access
    data["refresh_token"] = new.get("refresh_token", refresh_token)
    expires_in_val = new.get("expires_in", 21600)
    data["expires_in"] = expires_in_val
    data["expired"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + expires_in_val)
    )
    data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))

    # 写回文件
    path = data["_path"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in data.items() if not k.startswith("_")},
                  f, ensure_ascii=False, indent=2)
    print(f"  [{data.get('email','?')}] ✅ 刷新成功 (expires_in={expires_in_val}s)")
    return True


def check_and_refresh(pre_seconds=1800):
    """检查所有账号，提前 pre_seconds 秒刷新过期的 token"""
    accounts = load_accounts()
    if not accounts:
        print("[DAEMON] 无可用账号")
        return {"total": 0, "refreshed": 0, "disabled": 0, "expired": 0, "ok": 0}

    refreshed = disabled = expired = ok = 0
    for d in accounts:
        remaining = expires_in(d)
        email = d.get("email", "?")

        if remaining is None:
            print(f"  [{email}] 无过期时间，强制刷新...")
            if refresh_one(d):
                refreshed += 1
            else:
                disabled += 1
            continue

        if remaining < 0:
            print(f"  [{email}] 已过期 ({abs(remaining)}s 前)，尝试刷新...")
            if refresh_one(d):
                refreshed += 1
            else:
                expired += 1
            continue

        if remaining < pre_seconds:
            mins = remaining // 60
            print(f"  [{email}] {mins}分钟后过期，预刷新...")
            if refresh_one(d):
                refreshed += 1
            else:
                ok += 1  # 刷新失败但还没过期，仍然可用
            continue

        ok += 1

    print(f"\n[DAEMON] 总计={len(accounts)}  OK={ok}  已刷新={refreshed}  已过期={expired}  已禁用={disabled}")
    return {"total": len(accounts), "refreshed": refreshed, "disabled": disabled, "expired": expired, "ok": ok}


def main():
    parser = argparse.ArgumentParser(description="Token 自动刷新守护进程")
    parser.add_argument("--daemon", type=int, default=0, help="守护模式，每 N 秒检查一次")
    parser.add_argument("--pre", type=int, default=1800, help="提前多少秒预刷新（默认 1800=30分钟）")
    parser.add_argument("--check", action="store_true", help="仅检查，不刷新")
    args = parser.parse_args()

    if args.check:
        accounts = load_accounts()
        print(f"账号总数: {len(accounts)}\n")
        for d in accounts:
            remaining = expires_in(d)
            email = d.get("email", "?")
            if remaining is None:
                status = "⚠️ 无过期时间"
            elif remaining < 0:
                status = f"❌ 已过期 {abs(remaining)//60}分钟前"
            elif remaining < args.pre:
                status = f"⏰ {remaining//60}分钟后过期"
            else:
                status = f"✅ {remaining//3600}小时后过期"
            print(f"  {email:30s} {status}")
        return

    if args.daemon:
        print(f"[DAEMON] 每 {args.daemon}s 检查一次，提前 {args.pre}s 预刷新")
        while True:
            try:
                check_and_refresh(pre_seconds=args.pre)
            except KeyboardInterrupt:
                print("\n[DAEMON] 退出")
                break
            except Exception as e:
                print(f"[DAEMON] 错误: {e}")
                import traceback
                traceback.print_exc()
            time.sleep(args.daemon)
    else:
        check_and_refresh(pre_seconds=args.pre)


if __name__ == "__main__":
    main()

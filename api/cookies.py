# -*- coding: utf-8 -*-

import requests
import threading

from api.runtime import get_runtime_context

# 定义全局 Cookie 文件锁，保证读写绝对安全
cookie_lock = threading.RLock()


def save_cookies(session: requests.Session):
    cookies_path = get_runtime_context().cookies_path
    cookies_path.parent.mkdir(parents=True, exist_ok=True)

    with cookie_lock:
        buffer = ";".join(f"{k}={v}" for k, v in session.cookies.items())
        cookies_path.write_text(buffer, encoding="utf8")


def use_cookies() -> dict:
    cookies_path = get_runtime_context().cookies_path
    if not cookies_path.exists():
        return {}

    cookies = {}
    with cookies_path.open("r", encoding="utf8") as f:
        buffer = f.read().strip()
        for item in buffer.split(";"):
            if not item.strip():
                continue
            k, v = item.strip().split("=", 1)
            cookies[k] = v

        return cookies

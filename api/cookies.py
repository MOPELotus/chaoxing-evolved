# -*- coding: utf-8 -*-

import requests

from api.runtime import get_runtime_context

# 定义全局 Cookie 文件锁，保证读写绝对安全
cookie_lock = threading.RLock()


def save_cookies(session: requests.Session):
    cookies_path = get_runtime_context().cookies_path
    cookies_path.parent.mkdir(parents=True, exist_ok=True)

    buffer = ""
    with cookies_path.open("w", encoding="utf8") as f:
        for k, v in session.cookies.items():
            buffer += f"{k}={v};"
        buffer = buffer.removesuffix(";")
        with open(gc.COOKIES_PATH, "w") as f:
            f.write(buffer)


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

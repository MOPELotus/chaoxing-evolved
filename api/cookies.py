# -*- coding: utf-8 -*-

import requests

from api.runtime import get_runtime_context


def save_cookies(session: requests.Session):
    cookies_path = get_runtime_context().cookies_path
    cookies_path.parent.mkdir(parents=True, exist_ok=True)

    buffer = ""
    with cookies_path.open("w", encoding="utf8") as f:
        for k, v in session.cookies.items():
            buffer += f"{k}={v};"
        buffer = buffer.removesuffix(";")
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

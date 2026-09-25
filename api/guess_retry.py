from __future__ import annotations

import hashlib
import json
import math
import secrets
from pathlib import Path
from threading import RLock

from bs4 import BeautifulSoup

from api.runtime import get_runtime_context


_ledger_lock = RLock()


def guess_retry_settings(config: dict) -> tuple[bool, int]:
    enabled = str(config.get("guess_retry_enabled", False)).strip().lower() in {"true", "1", "yes", "on"}
    try:
        limit = max(0, min(10, int(config.get("guess_retry_limit", 3))))
    except (ValueError, TypeError, OverflowError):
        limit = 3
    return enabled and limit > 0, limit


def work_feedback(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    status = " ".join(node.get_text(" ", strip=True) for node in soup.select(".testTit_status"))
    if any(marker in status for marker in ("已完成", "已通过")):
        return "complete"
    form = soup.find("form")
    submit = soup.select_one(".btnSubmit, button[type='submit'], input[type='submit']")
    can_submit = submit is not None and not (
        submit.has_attr("disabled")
        or submit.get("aria-disabled") == "true"
        or "disabled" in submit.get("class", [])
        or submit.has_attr("hidden")
        or "display:none" in str(submit.get("style", "")).replace(" ", "").lower()
    )
    if (
        any(marker in status for marker in ("未达到及格线", "未达到通过标准"))
        and form is not None
        and can_submit
        and "addStudentWork" in str(form.get("action", ""))
        and form.select(".singleQuesId, .questionLi")
    ):
        return "retryable"
    return "unknown"


def combination_key(answers: list[str]) -> str:
    return hashlib.sha256(json.dumps(answers, ensure_ascii=False).encode("utf-8")).hexdigest()


def next_guess(domains: list[list[str]], seen: set[str]) -> list[str] | None:
    count = math.prod(len(domain) for domain in domains)
    if not domains or not count:
        return None
    start = secrets.randbelow(count)
    for offset in range(min(count, len(seen) + 1)):
        position = (start + offset) % count
        candidate = []
        for domain in domains:
            position, choice = divmod(position, len(domain))
            candidate.append(domain[choice])
        if combination_key(candidate) not in seen:
            return candidate
    return None


class GuessRetryLedger:
    def __init__(self, path: Path | None = None):
        self.path = path or get_runtime_context().config_path.with_suffix(".guess-retry.cache.json")

    @staticmethod
    def work_key(course: dict, job: dict, info: dict) -> str:
        identity = [course.get("courseId"), course.get("clazzId"), info.get("cpi"), job.get("jobid")]
        return hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid guess retry ledger")
        return data

    def get(self, key: str) -> dict | None:
        with _ledger_lock:
            entry = self._read().get(key)
            if entry is not None and not isinstance(entry, dict):
                raise ValueError("Invalid guess retry entry")
            return entry

    def record(self, key: str, status: str, attempts: int, seen: set[str]) -> None:
        with _ledger_lock:
            data = self._read()
            data[key] = {"status": status, "attempts": attempts, "combinations": sorted(seen)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)

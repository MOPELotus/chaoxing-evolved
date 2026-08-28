from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


COURSE_CLASS_SEPARATOR = "::"


def course_class_key(course: Mapping[str, Any]) -> str:
    """Return the stable desktop selection key for one concrete class."""
    course_id = str(course.get("courseId") or "").strip()
    clazz_id = str(course.get("clazzId") or "").strip()
    if not course_id:
        return ""
    return (
        f"{course_id}{COURSE_CLASS_SEPARATOR}{clazz_id}"
        if clazz_id
        else course_id
    )


def course_matches_selection(course: Mapping[str, Any], selections: Iterable[object]) -> bool:
    """Match exact course/class keys while keeping legacy courseId entries compatible.

    Older profiles stored only ``courseId``. Such an entry intentionally
    selects every matching class; once the course list is refreshed, the UI
    saves concrete ``courseId::clazzId`` keys instead.
    """
    course_id = str(course.get("courseId") or "").strip()
    exact_key = course_class_key(course)
    normalized = {str(value).strip() for value in selections if str(value).strip()}
    return exact_key in normalized or course_id in normalized


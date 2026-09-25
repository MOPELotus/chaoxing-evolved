from __future__ import annotations

from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True)
class CourseProgress:
    total: int
    completed: int
    unknown: int
    locked: int
    expired: int
    unfinished_titles: tuple[str, ...]

    @property
    def remaining(self) -> int:
        return self.total - self.completed - self.unknown

    @property
    def percentage(self) -> str:
        return f"{self.completed / self.total:.1%}" if self.total else "--"


def summarize_course_points(data: dict) -> CourseProgress:
    if not isinstance(data, dict) or not isinstance(data.get("points"), list):
        raise ValueError("Invalid course point response")
    points = data["points"]
    if any(not isinstance(point, dict) for point in points):
        raise ValueError("Invalid course point")
    completed = sum(point.get("has_finished") is True for point in points)
    unknown = sum(type(point.get("has_finished")) is not bool for point in points)
    pending = [point for point in points if point.get("has_finished") is False]
    return CourseProgress(
        total=len(points),
        completed=completed,
        unknown=unknown,
        locked=sum(point.get("need_unlock") is True for point in pending),
        expired=sum(point.get("is_expired") is True for point in pending),
        unfinished_titles=tuple(str(point.get("title") or point.get("id") or "未命名知识点") for point in pending),
    )


def log_course_report(chaoxing, courses: list[dict], outcomes: list[str], elapsed_seconds: float) -> None:
    logger.info("========== 本轮课程完成报告 ==========")
    logger.info("统计口径：结束时平台确认的知识点（章节节点），不是答题数或任务附件数；跳过不等于完成。")
    summaries = []
    readable = 0
    fully_completed = 0
    for index, course in enumerate(courses):
        title = str(course.get("title") or "未命名课程").replace("\n", " ").replace("\r", " ")
        label = f"{title} [班级 {course.get('clazzId', '--')}]"
        outcome = outcomes[index] if index < len(outcomes) else "未执行"
        try:
            data = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
            progress = summarize_course_points(data)
        except Exception as exc:
            logger.warning("[课程报告] {} | 知识点 --/-- | 完成率 -- | {} | 状态读取失败：{}", label, outcome, type(exc).__name__)
            continue
        readable += 1
        if not progress.total:
            logger.warning("[课程报告] {} | 知识点 0/0 | 完成率 -- | {} | 未获取到知识点，不能判定课程已完成", label, outcome)
            continue
        summaries.append(progress)
        fully_completed += progress.completed == progress.total
        logger.info(
            "[课程报告] {} | 知识点 {}/{} | 完成率 {} | 未完成 {} | 状态未知 {} | {}",
            label, progress.completed, progress.total, progress.percentage, progress.remaining, progress.unknown, outcome,
        )
        if progress.locked or progress.expired:
            logger.info("[课程报告] {} | 未完成中：待解锁 {}，已过期 {}（两项可能重叠）", label, progress.locked, progress.expired)
        if progress.unfinished_titles:
            titles = "、".join(title.replace("\n", " ").replace("\r", " ") for title in progress.unfinished_titles[:5])
            suffix = f" 等 {len(progress.unfinished_titles)} 个" if len(progress.unfinished_titles) > 5 else ""
            logger.info("[课程报告] {} | 未完成知识点：{}{}", label, titles, suffix)
    total = sum(item.total for item in summaries)
    completed = sum(item.completed for item in summaries)
    unknown = sum(item.unknown for item in summaries)
    percentage = f"{completed / total:.1%}" if total else "--"
    logger.info(
        "[总报告] 课程确认全部完成 {}/{} | 状态读取成功 {}/{} | 有知识点数据 {}/{} | 已获取知识点 {}/{} | 完成率 {} | 状态未知 {} | 总耗时 {:.1f} 秒",
        fully_completed, len(courses), readable, len(courses), len(summaries), len(courses), completed, total, percentage, unknown, max(0, elapsed_seconds),
    )
    if len(summaries) != len(courses) or unknown:
        logger.warning("[总报告] 存在缺失或未知状态；汇总仅计已获取数据，不能代表所有课程的最终完成率。")
    logger.info("========== 课程完成报告结束 ==========")

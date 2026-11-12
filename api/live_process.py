import time

from api.live import Live
from api.logger import logger


class LiveProcessor:
    @staticmethod
    def _positive_seconds(value):
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return None
        return seconds if seconds > 0 else None

    @classmethod
    def _duration_seconds(cls, live: Live, live_status: dict):
        """Read duration from current and legacy insertlive response shapes."""
        temp = live_status.get("temp")
        data = temp.get("data") if isinstance(temp, dict) else None
        candidates = []
        for container in (data, temp, live_status.get("data"), live_status):
            if isinstance(container, dict):
                candidates.extend(
                    container.get(key)
                    for key in ("duration", "liveDuration", "totalDuration")
                )

        attachment = getattr(live, "attachment", {})
        properties = attachment.get("property") if isinstance(attachment, dict) else None
        for container in (properties, attachment):
            if isinstance(container, dict):
                candidates.extend(
                    container.get(key)
                    for key in ("duration", "liveDuration", "totalDuration")
                )

        for candidate in candidates:
            duration = cls._positive_seconds(candidate)
            if duration is not None:
                return duration
        return None

    @staticmethod
    def run_live(live: Live, speed: float = 1.0):
        """循环提交直播时长，直到达到总时长"""
        if speed != 1.0:
            logger.info("直播任务不支持倍速计时，已锁定为 1 倍速")
        speed = 1.0
        # 获取直播状态（包含总时长）
        live_status = live.get_status()
        if not live_status:
            logger.error("直播状态获取失败，无法继续")
            return False

        # 解析直播总时长（单位：秒）
        duration = LiveProcessor._duration_seconds(live, live_status)
        if duration is None:
            temp = live_status.get("temp")
            if isinstance(temp, dict) and temp.get("status") == 0:
                logger.error("旧直播没有有效回放元数据，平台已拒绝该直播状态")
                return False
            logger.warning("无法获取直播总时长，默认按30分钟处理")
            duration = 30 * 60

        # 根据播放速度调整所需时间
        adjusted_duration = duration / speed
        total_minutes = (int(adjusted_duration) + 59) // 60  # 转换为分钟（向上取整）
        logger.info(f"开始刷取直播'{live.name}'，总时长{total_minutes}分钟（已根据倍速调整）")

        # 循环提交时长（每59秒一次，模拟持续观看）
        for i in range(total_minutes):
            logger.info(f"直播'{live.name}'已观看{i + 1}/{total_minutes}分钟")
            success = live.do_finish()  # 提交当前时长
            if not success:
                logger.warning(f"第{i + 1}分钟时长提交失败，将重试")
                # 失败重试一次
                time.sleep(5)
                if not live.do_finish():
                    logger.error(
                        f"第{i + 1}分钟时长连续两次提交失败，停止本轮直播任务"
                    )
                    return False

            # 根据倍速调整间隔时间
            sleep_time = 59 / speed
            time.sleep(sleep_time)

        logger.success(f"直播'{live.name}'时长刷取完成")
        return True

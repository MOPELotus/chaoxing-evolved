"""
通知服务模块，用于向外部服务发送通知消息。
支持多种通知服务，包括 OneBot v11 反向 WebSocket。
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional

import requests

from api.logger import logger

try:
    import websockets
except ImportError:  # pragma: no cover - 依赖缺失时走降级逻辑
    websockets = None


def _to_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default


class NotificationService(ABC):
    """
    通知服务基类，定义通知服务的公共接口和实现。
    """

    supports_file_upload = False

    def __init__(self):
        self.name = self.__class__.__name__
        self.url = ""
        self.tg_chat_id = ""
        self._conf = None
        self.disabled = False

    def config_set(self, config: Dict[str, str]) -> None:
        self._conf = config

    def _load_config_from_file(self) -> Optional[Dict[str, str]]:
        logger.info("未提供通知配置，已忽略外部通知功能")
        self.disabled = True
        return None

    def init_notification(self) -> None:
        if not self._conf:
            self._conf = self._load_config_from_file()

        if not self.disabled and self._conf:
            self._init_service()

    @abstractmethod
    def _init_service(self) -> None:
        pass

    @abstractmethod
    def _send(self, message: str) -> None:
        pass

    def send(self, message: str) -> None:
        if not self.disabled:
            self._send(message)

    def send_file(self, file_path: str | Path, display_name: str | None = None) -> None:
        return


class NotificationFactory:
    @staticmethod
    def create_service(config: Optional[Dict[str, str]] = None) -> NotificationService:
        service = DefaultNotification()

        if config:
            service.config_set(config)

        service = service.get_notification_from_config()
        service.init_notification()
        return service


class DefaultNotification(NotificationService):
    def _init_service(self) -> None:
        pass

    def _send(self, message: str) -> None:
        pass

    def get_notification_from_config(self) -> NotificationService:
        if not self._conf:
            self._conf = self._load_config_from_file()

        if self.disabled:
            return self

        try:
            provider_name = self._conf["provider"]
            if not provider_name:
                raise KeyError("未指定通知服务提供方")

            provider_class = globals().get(provider_name)
            if not provider_class:
                logger.error(f"未找到名为 {provider_name} 的通知服务提供方")
                self.disabled = True
                return self

            service = provider_class()
            service.config_set(self._conf)
            return service
        except KeyError:
            self.disabled = True
            logger.info("未找到外部通知配置，已忽略外部通知功能")
            return self


class ServerChan(NotificationService):
    def _init_service(self) -> None:
        if not self._conf or not self._conf.get("url"):
            self.disabled = True
            logger.info("未找到 Server酱 地址配置，已忽略该通知服务")
            return

        self.url = self._conf["url"]
        logger.info(f"已初始化 Server酱 通知服务，地址: {self.url}")

    def _send(self, message: str) -> None:
        params = {"text": message, "desp": message}
        headers = {"Content-Type": "application/json;charset=utf-8"}

        try:
            response = requests.post(self.url, json=params, headers=headers, timeout=20)
            response.raise_for_status()
            logger.info("Server酱通知发送成功")
        except requests.RequestException as exc:
            logger.error(f"Server酱通知发送失败: {exc}")


class Qmsg(NotificationService):
    def _init_service(self) -> None:
        if not self._conf or not self._conf.get("url"):
            self.disabled = True
            logger.info("未找到 Qmsg 地址配置，已忽略该通知服务")
            return

        self.url = self._conf["url"]
        logger.info(f"已初始化 Qmsg 通知服务，地址: {self.url}")

    def _send(self, message: str) -> None:
        params = {"msg": message}
        headers = {"Content-Type": "application/json;charset=utf-8"}

        try:
            response = requests.post(self.url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            logger.info("Qmsg 通知发送成功")
        except requests.RequestException as exc:
            logger.error(f"Qmsg 通知发送失败: {exc}")


class Bark(NotificationService):
    def _init_service(self) -> None:
        if not self._conf or not self._conf.get("url"):
            self.disabled = True
            logger.info("未找到 Bark 地址配置，已忽略该通知服务")
            return

        self.url = self._conf["url"]
        logger.info(f"已初始化 Bark 通知服务，地址: {self.url}")

    def _send(self, message: str) -> None:
        params = {"body": message}

        try:
            response = requests.post(self.url, params=params, timeout=20)
            response.raise_for_status()
            logger.info("Bark 通知发送成功")
        except requests.RequestException as exc:
            logger.error(f"Bark 通知发送失败: {exc}")


class Telegram(NotificationService):
    def _init_service(self) -> None:
        if not self._conf or not self._conf.get("url") or not self._conf.get("tg_chat_id"):
            self.disabled = True
            logger.info("未找到 Telegram 地址或会话 ID 配置，已忽略该通知服务")
            return

        self.tg_chat_id = self._conf["tg_chat_id"]
        self.url = self._conf["url"]
        logger.info(f"已初始化 Telegram 通知服务，Chat ID: {self.tg_chat_id}")

    def _send(self, message: str) -> None:
        params = {
            "chat_id": self.tg_chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            response = requests.post(self.url, data=params, timeout=20)
            response.raise_for_status()
            result = response.json()
            if result.get("ok"):
                logger.info("Telegram 通知发送成功")
            else:
                logger.error(f"Telegram 通知发送失败: {result}")
        except requests.RequestException as exc:
            logger.error(f"Telegram 通知发送失败: {exc}")
        except ValueError as exc:
            logger.error(f"Telegram 返回数据解析失败: {exc}")


class OneBotReverseWebSocketBridge:
    def __init__(self, host: str, port: int, path: str, access_token: str = "") -> None:
        self.host = host
        self.port = port
        self.path = path or "/"
        self.access_token = access_token.strip()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._ready = threading.Event()
        self._connected = threading.Event()
        self._send_lock: asyncio.Lock | None = None
        self._server = None
        self._websocket = None
        self._pending: dict[str, asyncio.Future] = {}
        self._startup_error: Exception | None = None

    def start(self) -> None:
        if websockets is None:
            raise RuntimeError("当前环境未安装 websockets，无法启用 OneBot v11 反向 WebSocket。")

        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return

            self._ready.clear()
            self._startup_error = None
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name=f"OneBotBridge-{self.host}:{self.port}",
            )
            self._thread.start()

        if not self._ready.wait(timeout=5):
            raise RuntimeError("OneBot v11 反向 WebSocket 服务启动超时。")
        if self._startup_error is not None:
            raise RuntimeError(f"OneBot v11 反向 WebSocket 服务启动失败: {self._startup_error}")

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def endpoint_display(self) -> str:
        return f"ws://{self.host}:{self.port}{self.path}"

    def send_action(self, action: str, params: dict, timeout: float = 10.0) -> dict:
        self.start()
        if not self._connected.wait(timeout=timeout):
            raise RuntimeError(f"NapCat 尚未连接到 {self.endpoint_display()}")
        if self._loop is None:
            raise RuntimeError("OneBot v11 事件循环尚未就绪")

        future = asyncio.run_coroutine_threadsafe(
            self._send_action_async(action, params, timeout),
            self._loop,
        )
        return future.result(timeout=timeout + 2)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._send_lock = asyncio.Lock()

        try:
            self._server = self._loop.run_until_complete(self._start_server_async())
        except Exception as exc:
            self._startup_error = exc
            self._ready.set()
            logger.error(f"OneBot v11 反向 WebSocket 服务启动失败: {exc}")
            return

        logger.info(f"OneBot v11 反向 WebSocket 服务已启动: {self.endpoint_display()}")
        self._ready.set()
        self._loop.run_forever()

    async def _start_server_async(self):
        return await websockets.serve(self._handle_connection, self.host, self.port)

    async def _handle_connection(self, websocket, path=None) -> None:
        requested_path = path or self._extract_path(websocket)
        if self.path and requested_path != self.path:
            logger.warning(f"OneBot v11 收到路径不匹配的连接: {requested_path}")
            await websocket.close(code=1008, reason="Invalid path")
            return

        if self.access_token:
            auth_header = self._extract_authorization_header(websocket)
            expected = f"Bearer {self.access_token}"
            if auth_header != expected:
                logger.warning("OneBot v11 连接鉴权失败，已拒绝该连接")
                await websocket.close(code=1008, reason="Unauthorized")
                return

        self._websocket = websocket
        self._connected.set()
        logger.info(f"OneBot v11 客户端已连接: {self.endpoint_display()}")

        try:
            async for raw_message in websocket:
                try:
                    payload = json.loads(raw_message)
                except json.JSONDecodeError:
                    continue

                echo = str(payload.get("echo", "") or "")
                pending = self._pending.pop(echo, None)
                if pending and not pending.done():
                    pending.set_result(payload)
        except Exception as exc:
            logger.warning(f"OneBot v11 连接已断开: {exc}")
        finally:
            if self._websocket is websocket:
                self._websocket = None
                self._connected.clear()
                self._fail_all_pending(RuntimeError("OneBot v11 连接已断开"))

    async def _send_action_async(self, action: str, params: dict, timeout: float) -> dict:
        if self._websocket is None:
            raise RuntimeError("NapCat 尚未连接到反向 WebSocket 服务")
        if self._send_lock is None:
            raise RuntimeError("OneBot v11 发送锁尚未初始化")

        echo = uuid.uuid4().hex
        response_future = self._loop.create_future()
        self._pending[echo] = response_future
        payload = {
            "action": action,
            "params": params,
            "echo": echo,
        }

        async with self._send_lock:
            await self._websocket.send(json.dumps(payload, ensure_ascii=False))

        try:
            response = await asyncio.wait_for(response_future, timeout=timeout)
        finally:
            self._pending.pop(echo, None)

        status = str(response.get("status", "") or "")
        if status and status != "ok":
            raise RuntimeError(f"OneBot v11 动作执行失败: {response}")
        return response

    def _fail_all_pending(self, exc: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    @staticmethod
    def _extract_path(websocket) -> str:
        request = getattr(websocket, "request", None)
        if request is not None and getattr(request, "path", None):
            return request.path
        if getattr(websocket, "path", None):
            return websocket.path
        return "/"

    @staticmethod
    def _extract_authorization_header(websocket) -> str:
        request = getattr(websocket, "request", None)
        if request is not None:
            headers = getattr(request, "headers", None)
            if headers is not None:
                return str(headers.get("Authorization", "") or "")

        headers = getattr(websocket, "request_headers", None)
        if headers is not None:
            return str(headers.get("Authorization", "") or "")
        return ""


_ONEBOT_BRIDGES: dict[tuple[str, int, str, str], OneBotReverseWebSocketBridge] = {}
_ONEBOT_BRIDGES_LOCK = threading.Lock()


def get_onebot_bridge(host: str, port: int, path: str, access_token: str = "") -> OneBotReverseWebSocketBridge:
    key = (host, port, path or "/", access_token.strip())
    with _ONEBOT_BRIDGES_LOCK:
        bridge = _ONEBOT_BRIDGES.get(key)
        if bridge is None:
            bridge = OneBotReverseWebSocketBridge(*key)
            _ONEBOT_BRIDGES[key] = bridge
        return bridge


class OneBotV11(NotificationService):
    supports_file_upload = True

    def __init__(self) -> None:
        super().__init__()
        self.host = "127.0.0.1"
        self.port = 3001
        self.path = "/"
        self.access_token = ""
        self.target_type = "private"
        self.user_id = ""
        self.group_id = ""
        self.bridge: OneBotReverseWebSocketBridge | None = None

    def _init_service(self) -> None:
        if websockets is None:
            self.disabled = True
            logger.error("未安装 websockets，无法启用 OneBot v11 通知。")
            return

        self.host = str(self._conf.get("onebot_host", "127.0.0.1") or "127.0.0.1").strip()
        self.port = _to_int(self._conf.get("onebot_port", 3001), 3001)
        self.path = str(self._conf.get("onebot_path", "/") or "/").strip() or "/"
        self.access_token = str(self._conf.get("onebot_access_token", "") or "").strip()
        self.target_type = str(self._conf.get("onebot_target_type", "private") or "private").strip().lower()
        self.user_id = str(self._conf.get("onebot_user_id", "") or "").strip()
        self.group_id = str(self._conf.get("onebot_group_id", "") or "").strip()

        if self.target_type not in {"private", "group"}:
            self.disabled = True
            logger.error(f"OneBot v11 目标类型无效: {self.target_type}")
            return
        if self.target_type == "private" and not self.user_id:
            self.disabled = True
            logger.error("OneBot v11 已启用 QQ 私聊通知，但未填写 QQ 号。")
            return
        if self.target_type == "group" and not self.group_id:
            self.disabled = True
            logger.error("OneBot v11 已启用 QQ 群通知，但未填写群号。")
            return

        try:
            self.bridge = get_onebot_bridge(self.host, self.port, self.path, self.access_token)
            self.bridge.start()
            logger.info(f"已初始化 OneBot v11 通知服务，监听地址: {self.bridge.endpoint_display()}")
        except Exception as exc:
            self.disabled = True
            logger.error(f"OneBot v11 通知服务初始化失败: {exc}")

    def _build_target_params(self) -> dict:
        if self.target_type == "group":
            return {"group_id": _to_int(self.group_id)}
        return {"user_id": _to_int(self.user_id)}

    def _send(self, message: str) -> None:
        if self.disabled or self.bridge is None:
            return

        action = "send_group_msg" if self.target_type == "group" else "send_private_msg"
        params = self._build_target_params()
        params["message"] = message

        try:
            self.bridge.send_action(action, params)
            logger.info("OneBot v11 文本通知发送成功")
        except Exception as exc:
            logger.error(f"OneBot v11 文本通知发送失败: {exc}")

    def send_file(self, file_path: str | Path, display_name: str | None = None) -> None:
        if self.disabled or self.bridge is None:
            return

        file_path = Path(file_path).resolve()
        if not file_path.exists():
            logger.warning(f"OneBot v11 文件通知失败，文件不存在: {file_path}")
            return

        action = "upload_group_file" if self.target_type == "group" else "upload_private_file"
        params = self._build_target_params()
        params["file"] = str(file_path)
        params["name"] = display_name or file_path.name

        try:
            self.bridge.send_action(action, params)
            logger.info("OneBot v11 文件通知发送成功")
        except Exception as exc:
            logger.error(f"OneBot v11 文件通知发送失败: {exc}")


# 为了向后兼容，保留原来的 Notification 别名
Notification = DefaultNotification

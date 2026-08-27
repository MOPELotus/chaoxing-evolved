import base64
import configparser
import json
import mimetypes
import os
import random
import re
import shutil
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from re import sub
from typing import Optional
from urllib.parse import urlparse

import httpx
import requests
from openai import OpenAI
from urllib3 import disable_warnings, exceptions

from api.answer_check import check_answer
from api.logger import logger
from api.runtime import get_runtime_context

# 关闭警告
disable_warnings(exceptions.InsecureRequestWarning)

__all__ = ["CacheDAO", "Tiku", "TikuYanxi", "TikuLike", "TikuAdapter", "AI", "SiliconFlow", "MultiTiku"]

IMG_TAG_PATTERN = re.compile(r'<img\b[^>]*?\bsrc=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
DEFAULT_IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}
IMAGE_DATA_URL_CACHE: dict[str, str] = {}
IMAGE_DATA_URL_LOCK = threading.RLock()


def normalize_question_title(title: str) -> str:
    title = sub(r'^\d+', '', title)
    title = sub(r'（\d+\.\d+分）$', '', title)
    return title


def extract_image_urls(text: str | None) -> list[str]:
    if not text:
        return []

    image_urls: list[str] = []
    for match in IMG_TAG_PATTERN.finditer(str(text)):
        url = match.group(1).strip()
        if url and url not in image_urls:
            image_urls.append(url)
    return image_urls


def normalize_rich_text_for_prompt(text: str | None) -> str:
    if not text:
        return ""

    normalized = IMG_TAG_PATTERN.sub(" [图片] ", str(text))
    normalized = HTML_TAG_PATTERN.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def normalize_prompt_options(options: str | list[str] | None) -> str:
    if options is None:
        return ""
    if isinstance(options, list):
        raw_options = options
    else:
        raw_options = str(options).splitlines()
    cleaned_options = [
        re.sub(
            r"^[A-Za-z][\s\.\)、:：]*",
            "",
            normalize_rich_text_for_prompt(str(option)),
        ).strip()
        for option in raw_options
        if str(option).strip()
    ]
    return "\n".join(cleaned_options)


def build_multimodal_user_content(text: str, image_urls: list[str] | None = None) -> str | list[dict]:
    cleaned_text = (text or "").strip()
    unique_urls = [url for url in prepare_multimodal_image_urls(image_urls or []) if url]
    if not unique_urls:
        return cleaned_text

    if cleaned_text:
        cleaned_text = f"{cleaned_text}\n本题包含 {len(unique_urls)} 张图片，请结合图片内容作答。"
    else:
        cleaned_text = f"本题包含 {len(unique_urls)} 张图片，请结合图片内容作答。"

    content: list[dict] = [{"type": "text", "text": cleaned_text}]
    for image_url in unique_urls:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    return content


def messages_have_images(messages: list[dict]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    return True
    return False


def strip_images_from_messages(messages: list[dict]) -> list[dict]:
    normalized_messages: list[dict] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            normalized_messages.append(dict(message))
            continue

        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")).strip())
        normalized_message = dict(message)
        normalized_message["content"] = "\n".join(part for part in text_parts if part).strip()
        normalized_messages.append(normalized_message)
    return normalized_messages


def _image_request_headers(url: str) -> dict[str, str]:
    headers = dict(DEFAULT_IMAGE_HEADERS)
    host = urlparse(url).netloc.lower()
    if "chaoxing.com" in host:
        headers["Referer"] = "https://p.ananas.chaoxing.com/"
    return headers


def _guess_image_mime_type(url: str, response: requests.Response) -> str:
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if content_type.startswith("image/"):
        return content_type

    guessed, _ = mimetypes.guess_type(url)
    if guessed and guessed.startswith("image/"):
        return guessed
    return "image/png"


def image_url_to_data_url(url: str, timeout: int = 15) -> str:
    if not url:
        return url
    if url.startswith("data:"):
        return url
    if not url.lower().startswith(("http://", "https://")):
        return url

    with IMAGE_DATA_URL_LOCK:
        cached = IMAGE_DATA_URL_CACHE.get(url)
        if cached:
            return cached

    response = requests.get(
        url,
        headers=_image_request_headers(url),
        timeout=timeout,
    )
    response.raise_for_status()
    mime_type = _guess_image_mime_type(url, response)
    data_url = f"data:{mime_type};base64,{base64.b64encode(response.content).decode('ascii')}"

    with IMAGE_DATA_URL_LOCK:
        IMAGE_DATA_URL_CACHE[url] = data_url
    return data_url


def prepare_multimodal_image_urls(image_urls: list[str]) -> list[str]:
    prepared_urls: list[str] = []
    for url in image_urls:
        if not url:
            continue
        try:
            prepared_urls.append(image_url_to_data_url(url))
        except Exception as exc:
            logger.warning(f"图片转 base64 失败，回退为原始链接：{url} -> {exc}")
            prepared_urls.append(url)
    return prepared_urls


def parse_provider_names(provider_value: Optional[str]) -> list[str]:
    if not provider_value:
        return []
    return [item.strip() for item in re.split(r"[,+]", provider_value) if item.strip()]


def normalize_answer_for_compare(answer: str, question_type: str, tiku: "Tiku") -> str:
    if answer is None:
        return ""

    answer = str(answer).strip()
    if not answer:
        return ""

    if question_type == "judgement":
        if answer in tiku.true_list:
            return "judgement:true"
        if answer in tiku.false_list:
            return "judgement:false"

    parts = cut(answer) or [answer]
    normalized_parts = []
    for part in parts:
        cleaned = re.sub(r"\s+", "", str(part).strip()).lower()
        if cleaned:
            normalized_parts.append(cleaned)

    if question_type == "multiple":
        normalized_parts = sorted(set(normalized_parts))

    return "|".join(normalized_parts)

class CacheDAO:
    """
    @Author: SocialSisterYi
    @Reference: https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy
    """
    DEFAULT_CACHE_FILE = None

    def __init__(self, file: str | os.PathLike[str] | None = DEFAULT_CACHE_FILE):
        if file is None:
            file = get_runtime_context().cache_path
        self.cache_file = Path(file)
        self._lock = threading.RLock()
        if not self.cache_file.is_file():
            self._write_cache({})

    def _read_cache(self) -> dict:
        # 新增缓存文件读取的异常处理
        try:
            with self._lock:
                if not self.cache_file.is_file():
                    return {}
                try:
                    with self.cache_file.open("r", encoding="utf8") as fp:
                        return json.load(fp)
                except json.JSONDecodeError as e:
                    logger.error(f"缓存文件 JSON 解析失败: {e}, 尝试恢复...")
                    # 尝试从原始二进制中以 utf-8 忽略错误地恢复有效 JSON 段
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            try:
                                return json.loads(text[start:end + 1])
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # 若无法恢复，备份损坏文件并返回空缓存
                    try:
                        bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                        bak_path = self.cache_file.with_name(bak_name)
                        shutil.copy2(self.cache_file, bak_path)
                        logger.error(f"缓存文件已损坏，已备份为: {bak_path}，将使用空缓存继续运行")
                    except Exception as ex:
                        logger.error(f"备份损坏缓存失败: {ex}")
                    return {}
                except UnicodeDecodeError as e:
                    logger.error(f"缓存文件编码读取失败: {e}, 采用恢复策略...")
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            try:
                                return json.loads(text[start:end + 1])
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                        bak_path = self.cache_file.with_name(bak_name)
                        shutil.copy2(self.cache_file, bak_path)
                        logger.error(f"缓存文件编码错误，已备份为: {bak_path}，将使用空缓存继续运行")
                    except Exception as ex:
                        logger.error(f"备份损坏缓存失败: {ex}")
                    return {}
        except Exception as e:
            logger.error(f"读取缓存异常: {e}")
            return {}

    def _write_cache(self, data: dict) -> None:
        # 为缓存写入加锁，防止并发写入损坏文件
        try:
            with self._lock:
                parent = self.cache_file.parent
                if not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)
                # 写入临时文件后原子替换，减少并发写入时的损坏风险
                fd, tmp_path = tempfile.mkstemp(prefix=self.cache_file.name, dir=str(parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf8") as fp:
                        json.dump(data, fp, ensure_ascii=False, indent=4)
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(tmp_path, str(self.cache_file))
                except Exception as e:
                    # 清理临时文件
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    logger.error(f"Failed to write cache atomically: {e}")
        except IOError as e:
            logger.error(f"Failed to write cache: {e}")

    def get_cache(self, question: str) -> Optional[str]:
        data = self._read_cache()
        return data.get(question)

    def add_cache(self, question: str, answer: str) -> None:
        # 为缓存写入加锁，防止并发写入损坏文件
        with self._lock:
            data = self._read_cache()
            data[question] = answer
            self._write_cache(data)


# TODO: 重构此部分代码，将此类改为抽象类，加载题库方法改为静态方法，禁止直接初始化此类
class Tiku:
    CONFIG_PATH = os.path.join(os.getcwd(), "config.ini")
    DISABLE = False     # 停用标志
    SUBMIT = False      # 提交标志
    COVER_RATE = 0.8    # 覆盖率
    true_list = []
    false_list = []
    def __init__(self, config_path: Optional[str] = None) -> None:
        self._name = None
        self._api = None
        self._conf = None
        self._config_path = config_path or self.CONFIG_PATH
        self.true_list = []
        self.false_list = []

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def api(self):
        return self._api

    @api.setter
    def api(self, value):
        self._api = value

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self, value):
        self._token = value

    def init_tiku(self):
        # 仅用于题库初始化, 应该在题库载入后作初始化调用, 随后才可以使用题库
        # 尝试根据配置文件设置提交模式
        if not self._conf:
            self.DISABLE = True
            logger.error("未找到题库配置, 已忽略题库功能")
        if not self.DISABLE:
            # 设置提交模式
            self.SUBMIT = True if self._conf['submit'] == 'true' else False
            self.COVER_RATE = float(self._conf['cover_rate'])
            self.true_list = self._conf['true_list'].split(',')
            self.false_list = self._conf['false_list'].split(',')
            # 调用自定义题库初始化
            self._init_tiku()

    def _init_tiku(self):
        # 仅用于题库初始化, 例如配置token, 交由自定义题库完成
        pass

    def config_set(self, config):
        self._conf = config

    def _get_conf(self):
        try:
            parser = configparser.ConfigParser()
            parser.read(self._config_path, encoding="utf8")
            return parser["tiku"]
        except (KeyError, FileNotFoundError):
            self.DISABLE = True
            logger.info("未找到tiku配置, 已忽略题库功能")
            return None

    @property
    def _is_manual_mode(self) -> bool:
        return bool(
            getattr(self, "is_manual", False)
            or self.__class__.__name__ == "TikuManual"
            or (
                self.__class__.__name__ == "TikuFallback"
                and any(
                    getattr(p, "is_manual", False) or p.__class__.__name__ == "TikuManual"
                    for p in getattr(self, "providers", [])
                )
            )
        )

    def _normalize_question_info(self, q_info: dict) -> dict:
        normalized_q_info = dict(q_info)
        logger.debug(f"原始标题：{normalized_q_info['title']}")
        normalized_q_info['title'] = normalize_question_title(normalized_q_info['title'])
        normalized_q_info['title_text'] = normalize_rich_text_for_prompt(normalized_q_info['title'])
        title_image_urls = extract_image_urls(normalized_q_info['title'])
        option_source = normalized_q_info.get('options')
        if isinstance(option_source, list):
            option_source = "\n".join(str(item) for item in option_source)
        option_image_urls = extract_image_urls(option_source)
        normalized_q_info['image_urls'] = title_image_urls + [
            url for url in option_image_urls if url not in title_image_urls
        ]
        logger.debug(f"处理后标题：{normalized_q_info['title']}")
        if normalized_q_info['image_urls']:
            logger.debug(f"识别到题目图片：{', '.join(normalized_q_info['image_urls'])}")
        return normalized_q_info

    def _validate_answer(self, answer: str, q_info: dict) -> bool:
        return check_answer(answer, q_info['type'], self)

    def _query_validated(self, q_info: dict) -> Optional[str]:
        answer = self._query(q_info)
        if answer:
            answer = answer.strip()
            logger.info(f"从{self.name}获取答案：{q_info['title']} -> {answer}")
            if self._validate_answer(answer, q_info):
                return answer

            logger.info(f"从{self.name}获取到的答案类型与题目类型不符，已舍弃")
            return None

        logger.error(f"从{self.name}获取答案失败：{q_info['title']}")
        return None

    def query(self,q_info:dict) -> Optional[str]:
        if self.DISABLE:
            return None

        q_info = self._normalize_question_info(q_info)

        # 先过缓存
        cache_dao = CacheDAO()
        answer = cache_dao.get_cache(q_info['title'])
        if answer:
            logger.info(f"从缓存中获取答案：{q_info['title']} -> {answer}")
            answer = answer.strip()
            if self._validate_answer(answer, q_info):
                return answer
            logger.warning(f"缓存中的答案未通过当前题型校验，已忽略缓存：{q_info['title']}")

        answer = self._query_validated(q_info)
        if answer:
            cache_dao.add_cache(q_info['title'], answer)
            return answer
        return None

    def query_without_cache(self, q_info: dict) -> Optional[str]:
        if self.DISABLE:
            return None
        return self._query_validated(self._normalize_question_info(q_info))

    def query_all(self, q_list: list[dict], query_delay: float = 0.0) -> list[Optional[str]]:
        if self.DISABLE:
            return [None] * len(q_list)

        results = [None] * len(q_list)
        pending_indices = []

        cache_dao = CacheDAO()
        for idx, q in enumerate(q_list):
            if not self._is_manual_mode:
                logger.debug(f"原始标题：{q['title']}")
            q['title'] = sub(r'^\d+', '', q['title'])
            q['title'] = sub(r'（\d+\.\d+分）$', '', q['title'])
            if not self._is_manual_mode:
                logger.debug(f"处理后标题：{q['title']}")

            answer = cache_dao.get_cache(q['title'])
            if answer:
                logger.info(f"从缓存中获取答案：{q['title']} -> {answer}")
                results[idx] = answer.strip()
            else:
                pending_indices.append(idx)

        if not pending_indices:
            return results

        sub_q_list = [q_list[idx] for idx in pending_indices]
        sub_results = self._query_all(sub_q_list, query_delay=query_delay)

        if not isinstance(sub_results, list):
            logger.error(f"{self.name} _query_all 返回结果格式异常，期望列表")
            sub_results = [None] * len(pending_indices)
        elif len(sub_results) != len(pending_indices):
            logger.error(
                f"{self.name} _query_all 返回结果长度不匹配，期望 {len(pending_indices)}，实际 {len(sub_results)}")
            # 补齐或截断 sub_results 防止错位
            sub_results = list(sub_results) + [None] * (len(pending_indices) - len(sub_results))
            sub_results = sub_results[:len(pending_indices)]

        for idx, ans in zip(pending_indices, sub_results):
            q_info = q_list[idx]
            if ans:
                ans = ans.strip()
                logger.info(f"从{self.name}获取答案：{q_info['title']} -> {ans}")
                if check_answer(ans, q_info['type'], self):
                    cache_dao.add_cache(q_info['title'], ans)
                    results[idx] = ans
                else:
                    logger.info(f"从{self.name}获取到的答案类型与题目类型不符，已舍弃")
            else:
                logger.error(f"从{self.name}获取答案失败：{q_info['title']}")

        return results

    @abstractmethod
    def _query(self, q_info: dict) -> Optional[str]:
        """
        查询接口, 交由自定义题库实现
        """
        pass

    def _query_all(self, q_list: list[dict], query_delay: float = 0.0) -> list[Optional[str]]:
        """
        批量查询的实现接口，默认循环调用单个查询 _query。
        子类若有批量查询或交互需求（如手动模式），可重写此方法。
        """
        results = []
        for q in q_list:
            if query_delay > 0:
                time.sleep(query_delay)
            try:
                results.append(self._query(q))
            except Exception as e:
                logger.error(f"{self.name} 查询单个题目发生异常: {e}")
                results.append(None)
        return results

    def get_tiku_from_config(config: Optional[dict] = None, config_path: Optional[str] = None):
        """
        从配置文件加载题库, 这个配置可以是用户提供, 可以是默认配置文件
        """
        conf = config or self._conf
        path = config_path or self._config_path
        if not conf:
            conf = self._get_conf()
        if not conf or self.DISABLE:
            return DummyTiku(config_path=path)
        try:
            config = dict(conf)
            cls_name = config['provider'].strip()
            if not cls_name:
                raise KeyError
        except KeyError:
            logger.error("未找到题库配置, 已忽略题库功能")
            return DummyTiku(config_path=path)

        provider_names = parse_provider_names(config.get("providers") or cls_name)
        if len(provider_names) > 1 or cls_name in {"MultiTiku", "CompositeTiku"}:
            if provider_names:
                config["providers"] = ",".join(provider_names)
            new_cls = MultiTiku(config_path=path)
            new_cls.config_set(config)
            return new_cls

        # FIXME: Implement using StrEnum instead. This is not only buggy but also not safe
        new_cls = globals()[provider_names[0] if provider_names else cls_name](config_path=path)
        new_cls.config_set(config)
        return new_cls

    def judgement_select(self, answer: str) -> bool:
        """
        这是一个专用的方法, 要求配置维护两个选项列表, 一份用于正确选项, 一份用于错误选项, 以应对题库对判断题答案响应的各种可能的情况
        它的作用是将获取到的答案answer与可能的选项列对比并返回对应的布尔值
        """
        if self.DISABLE:
            return False
        # 对响应的答案作处理
        answer = answer.strip().lower()

        # 内置的高频通用判断词规整
        if answer in ['true', 't', '1', '对', '正确', '√', '是', 'yes', 'y']:
            return True
        if answer in ['false', 'f', '0', '错', '错误', '×', '否', 'no', 'n', '不对', '不正确']:
            return False

        # 兼容自定义配置列表
        if answer in [x.lower() for x in self.true_list] or answer in self.true_list:
            return True
        elif answer in [x.lower() for x in self.false_list] or answer in self.false_list:
            return False
        else:
            # 无法判断, 随机选择
            logger.error(
                f'无法判断答案 -> {answer} 对应的是正确还是错误, 请自行判断并加入配置文件重启脚本, 本次将会随机选择选项')
            return random.choice([True, False])

    def get_submit_params(self):
        """
        这是一个专用方法, 用于根据当前设置的提交模式, 响应对应的答题提交API中的pyFlag值
        """
        # 留空直接提交, 1保存但不提交
        if self.SUBMIT:
            return ""
        else:
            return "1"

    def check_llm_connection(self) -> bool:
        """
        检查大模型连接是否可用
        默认返回 True（非大模型题库不需要检查）
        """
        return True

    def resolve_conflict(self, q_info: dict, candidate_answers: list[tuple[str, str]]) -> Optional[str]:
        return None


class TikuFallback(Tiku):
    # 多题库回退实现，按 provider 中配置顺序依次查询。
    def __init__(self, providers=None, config_path: Optional[str] = None):
        """初始化多题库回退."""
        super().__init__(config_path)
        self.name = '多题库回退'
        self.providers = providers or []
        self.skip_answer_validation = True

    def _init_tiku(self):
        active = []
        for provider in self.providers:
            try:
                provider.init_tiku()
                if not provider.DISABLE:
                    active.append(provider)
            except Exception as e:
                logger.error(f'初始化题库 {provider.name} 失败: {e}')
        self.providers = active
        if not self.providers:
            logger.error('多题库回退初始化失败: 没有可用题库')
            self.DISABLE = True
        else:
            logger.info(f"多题库回退已启用，查询顺序: {', '.join([p.__class__.__name__ for p in self.providers])}")

    def _query(self, q_info: dict) -> Optional[str]:
        for provider in self.providers:
            try:
                answer = provider._query(q_info)
            except Exception as e:
                provider_id = f'{provider.name}({provider.__class__.__name__})'
                logger.exception(f'{self.name} 查询时 {provider_id} 异常: {e}')
                continue
            if not answer:
                logger.info(f'{provider.name} 未命中，回退到下一个题库')
                continue

            # 若当前题库返回答案但类型不符，则继续回退。
            if check_answer(answer, q_info['type'], provider):
                logger.info(f'{provider.name} 命中答案')
                return answer

            logger.info(f'{provider.name} 返回答案类型不符，回退到下一个题库')
        return None

    def _query_all(self, q_list: list[dict], query_delay: float = 0.0) -> list[Optional[str]]:
        results = [None] * len(q_list)
        pending_indices = list(range(len(q_list)))

        for provider in self.providers:
            if not pending_indices:
                break
            if provider.DISABLE:
                continue

            sub_q_list = [q_list[idx] for idx in pending_indices]
            try:
                sub_results = provider.query_all(sub_q_list, query_delay=query_delay)
            except Exception as e:
                provider_id = f'{provider.name}({provider.__class__.__name__})'
                logger.exception(f'{self.name} 批量查询时 {provider_id} 异常: {e}')
                continue

            if not isinstance(sub_results, list):
                logger.error(f"{provider.name} 批量查询返回数据格式异常（非列表），跳过该题库")
                continue

            if len(sub_results) != len(pending_indices):
                logger.error(
                    f"{provider.name} 批量查询返回结果长度（{len(sub_results)}）与请求题目数（{len(pending_indices)}）不匹配，跳过该题库以防答案错位")
                continue

            next_pending_indices = []
            for orig_idx, ans in zip(pending_indices, sub_results):
                if ans:
                    logger.info(f'{provider.name} 命中答案: {q_list[orig_idx]["title"]} -> {ans}')
                    results[orig_idx] = ans
                else:
                    logger.info(f'{provider.name} 未命中或返回答案无效，将回退')
                    next_pending_indices.append(orig_idx)
            pending_indices = next_pending_indices

        return results

    def check_llm_connection(self) -> bool:
        for provider in self.providers:
            if not provider.check_llm_connection():
                logger.error(f'{provider.name} 连接检查失败')
                return False
        return True


# 按照以下模板实现更多题库

class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化言溪题库实例."""
        super().__init__(config_path)
        self.name = '言溪题库'
        self.api = 'https://tk.enncy.cn/query'
        self._token = None
        self._token_index = 0  # token队列计数器
        self._times = 100  # 查询次数剩余, 初始化为100, 查询后校对修正

    def _query(self, q_info: dict):
        res = requests.get(
            self.api,
            params={
                'question': q_info['title'],
                'token': self._token,
                # 'type':q_info['type'], #修复478题目类型与答案类型不符（不想写后处理了）
                # 没用，就算有type和options，言溪题库还是可能返回类型不符，问了客服，type仅用于收集
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            if not res_json['code']:
                # 如果是因为TOKEN次数到期, 则更换token
                if self._times == 0 or '次数不足' in res_json['data']['answer']:
                    logger.info(f'TOKEN查询次数不足, 将会更换并重新搜题')
                    self._token_index += 1
                    self.load_token()
                    # 重新查询
                    return self._query(q_info)
                logger.error(
                    f'{self.name}查询失败:\n\t剩余查询数{res_json["data"].get("times", f"{self._times}(仅参考)")}:\n\t消息:{res_json["message"]}')
                return None
            self._times = res_json["data"].get("times", self._times)
            return res_json['data']['answer'].strip()
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def load_token(self):
        token_list = self._conf['tokens'].split(',')
        if self._token_index == len(token_list):
            # TOKEN 用完
            logger.error('TOKEN用完, 请自行更换再重启脚本')
            raise PermissionError(f'{self.name} TOKEN 已用完, 请更换')
        self._token = token_list[self._token_index]

    def _init_tiku(self):
        self.load_token()


class TikuGo(Tiku):
    # GO题（网课小工具题库）实现
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化GO题实例."""
        super().__init__(config_path)
        self.name = 'GO题（网课小工具题库）'
        self.api = 'https://q.icodef.com/wyn-nb?v=4'
        self._headers = {
            'Authorization': '',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        self._request_lock = threading.Lock()
        self._last_request_time = 0.0
        self._min_interval = 1.0
        self._retry_times = 3
        self._retry_backoff = 1.2

    def _sleep_for_next_request(self) -> None:
        with self._request_lock:
            now = time.time()
            wait_time = max(0.0, self._last_request_time + self._min_interval - now)
            self._last_request_time = now + wait_time
        if wait_time > 0:
            time.sleep(wait_time)

    def _mark_request_finished(self) -> None:
        with self._request_lock:
            self._last_request_time = time.time()

    def _request_question(self, question: str, attempt: int) -> Optional[requests.Response]:
        try:
            self._sleep_for_next_request()
            res = requests.post(
                self.api,
                data={'question': question},
                headers=self._headers,
                verify=True,
                timeout=15
            )
            self._mark_request_finished()
            return res
        except requests.exceptions.RequestException as e:
            logger.error(f'{self.name}查询异常 ({attempt}/{self._retry_times}): {e}')
            self._mark_request_finished()
            return None

    def _parse_response(self, res: requests.Response) -> Optional[dict]:
        if res.status_code != 200:
            logger.error(f'{self.name}查询失败: 状态码 {res.status_code}, 响应: {res.text}')
            return None

        try:
            res_json = res.json()
        except ValueError:
            logger.error(f'{self.name}查询失败: 返回内容不是有效JSON, 响应: {res.text}')
            return None

        try:
            code = int(str(res_json.get('code', '')).strip())
        except ValueError:
            code = 0

        answer = str(res_json.get('data', '')).strip()
        msg = str(res_json.get('msg', '')).strip()
        raw_text = f'{answer} {msg}'
        is_throttled = any(key in raw_text for key in ['流控限制', '速度太快', '并发限制', '忙不过来'])
        return {
            'code': code,
            'answer': answer,
            'msg': msg,
            'is_throttled': is_throttled,
        }

    def _sleep_retry(self, attempt: int, reason: str, include_min_interval: bool = False) -> None:
        if include_min_interval:
            sleep_seconds = max(self._min_interval, self._retry_backoff * attempt)
        else:
            sleep_seconds = self._retry_backoff * attempt
        logger.warning(f'{self.name}{reason}，{sleep_seconds:.1f}s 后重试 ({attempt}/{self._retry_times})')
        time.sleep(sleep_seconds)

    @staticmethod
    def _is_placeholder_answer(answer: str, msg: str) -> bool:
        return '李恒雅' in answer or '李恒雅' in msg

    def _query(self, q_info: dict):
        title = q_info.get('title', '')
        candidates = [
            title,
            re.sub(r'^【[^】]+】\s*', '', title).strip(),
            re.sub(r'^\[[^\]]+\]\s*', '', title).strip(),
        ]
        seen = set()
        normalized_titles = []
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                normalized_titles.append(item)

        for query_title in normalized_titles:
            answer = self._query_once(query_title)
            if answer:
                return answer
        return None

    def _query_once(self, question: str) -> Optional[str]:
        for attempt in range(1, self._retry_times + 1):
            res = self._request_question(question, attempt)
            if res is None:
                if attempt < self._retry_times:
                    self._sleep_retry(attempt, '查询异常', include_min_interval=True)
                    continue
                break

            parsed = self._parse_response(res)
            if not parsed:
                return None

            code = parsed['code']
            answer = parsed['answer']
            msg = parsed['msg']
            is_throttled = parsed['is_throttled']

            if code != 1:
                if is_throttled and attempt < self._retry_times:
                    self._sleep_retry(attempt, '触发流控')
                    continue
                logger.info(f"{self.name}未命中或失败: {msg or '未知错误'}")
                return None

            if not answer:
                return None

            # GO题库在未搜到时可能在 data/msg 中返回“李恒雅正在努力撰写中...”。
            if self._is_placeholder_answer(answer, msg):
                if is_throttled and attempt < self._retry_times:
                    self._sleep_retry(attempt, '命中流控提示')
                    continue
                return None

            return answer

        return None

    def _init_tiku(self):
        self._headers['Authorization'] = self._conf.get('go_authorization', self._headers['Authorization'])
        try:
            min_interval = float(self._conf.get('go_min_interval', self._min_interval))
            if min_interval < 0:
                raise ValueError('go_min_interval must be non-negative')
            self._min_interval = min_interval
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_min_interval 无效，使用默认值 {self._min_interval}')

        try:
            retry_times = int(self._conf.get('go_retry_times', self._retry_times))
            if retry_times < 1:
                raise ValueError('go_retry_times must be >= 1')
            self._retry_times = retry_times
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_retry_times 无效，使用默认值 {self._retry_times}')

        try:
            retry_backoff = float(self._conf.get('go_retry_backoff', self._retry_backoff))
            if retry_backoff < 0:
                raise ValueError('go_retry_backoff must be non-negative')
            self._retry_backoff = retry_backoff
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_retry_backoff 无效，使用默认值 {self._retry_backoff}')


class TikuLike(Tiku):
    # LIKE知识库实现 参考 https://www.datam.site/
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化LIKE知识库实例."""
        super().__init__(config_path)
        self.name = 'LIKE知识库'
        self.ver = '2.0.0'  # 对应官网API版本
        self.query_api = 'https://app.datam.site/api/v1/query'
        self.models_api = 'https://app.datam.site/api/v1/query/models'
        self.balance_api = 'https://app.datam.site/api/v1/balance'
        self.homepage = 'https://www.datam.site'
        self._model = None
        self._timeout = 300
        self._retry = True
        self._retry_times = 3
        self._tokens = []
        self._balance = {}
        self._search = False
        self._vision = True
        self._count = 0
        self._headers = {"Content-Type": "application/json"}

    def _query(self, q_info: dict = None):
        if not q_info:
            logger.error("当前无题目信息，请检查")
            return ""

        q_info_map = {"single": "【单选题】", "multiple": "【多选题】", "completion": "【填空题】", "judgement": "【判断题】"}
        q_info_prefix = q_info_map.get(q_info['type'], "【其他类型题目】")
        options = ', '.join(q_info['options']) if isinstance(q_info['options'], list) else q_info['options']
        question = f"{q_info_prefix}{q_info['title']}\n"

        if q_info['type'] in ['single', 'multiple']:
            question += f"选项为: {options}\n"

        # 随机选择一个token进行查询
        token = random.choice(self._tokens)

        # 检查该token是否有余额
        if self._balance.get(token, 0) <= 0:
            logger.error(f'{self.name}当前Token查询次数不足: ...{token[-5:]}')
            # 尝试选择其他有余额的token
            available_tokens = [t for t in self._tokens if self._balance.get(t, 0) > 0]
            if available_tokens:
                token = random.choice(available_tokens)
            else:
                logger.error(f'{self.name}所有Token查询次数都不足')
                return None

        ans = None
        try_times = 0

        # 尝试查询，直到成功或达到重试次数
        while not ans and self._retry and try_times < self._retry_times:
            ans = self._query_single(token, question)
            try_times += 1
            if ans:  # 如果查询成功，减少余额
                self._balance[token] -= 1
                logger.info(f'使用Token ...{token[-5:]} 查询成功，剩余次数: {self._balance[token]}')
                break
            elif try_times < self._retry_times:
                logger.warning(f'使用Token ...{token[-5:]} 查询失败，进行第 {try_times + 1} 次重试...')

        # 10次查询后更新余额
        self._count = (self._count + 1) % 10
        if self._count == 0:
            self.update_times()

        return ans

    def _query_single(self, token: str = "", query: str = "") -> str:
        """
        查询单个问题的答案
        
        Args:
            token: API访问令牌
            query: 查询的问题内容
            
        Returns:
            查询到的答案，如果失败则返回None
        """
        # 验证输入参数
        if not token:
            logger.error(f'{self.name}查询失败: 未提供有效的token')
            return None

        if not query:
            logger.error(f'{self.name}查询失败: 查询内容为空')
            return None

        # 设置请求头
        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'

        # 准备请求数据
        request_data = {
            'query': query,
            'model': self._model if self._model else '',
            'search': self._search,
            'vision': self._vision
        }

        # 发送API请求
        try:
            res = requests.post(
                self.query_api,
                json=request_data,
                headers=temp_headers,
                verify=False,
                timeout=self._timeout  # 添加超时设置
            )
        except requests.exceptions.Timeout:
            logger.error(f'{self.name}查询超时: 请求超过300秒')
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name}网络连接错误: 无法连接到API服务器')
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f'{self.name}查询异常: \n{e}')
            return None
        except Exception as e:
            logger.error(f'{self.name}查询发生未知错误: \n{e}')
            return None

        # 处理HTTP响应
        if res.status_code == 200:
            return self._parse_response(res)
        elif res.status_code == 401:
            logger.error(f'{self.name}认证失败: 请检查Token是否正确或已过期')
        elif res.status_code == 429:
            logger.error(f'{self.name}请求过于频繁: 已达到API速率限制')
        elif res.status_code == 500:
            logger.error(f'{self.name}服务器内部错误: API服务暂时不可用')
        elif res.status_code == 400:
            logger.error(f'{self.name}请求参数错误: 请检查查询内容格式')
        elif res.status_code == 403:
            logger.error(f'{self.name}访问被拒绝: 可能是Token权限不足')
        else:
            logger.error(f'{self.name}查询失败: 状态码 {res.status_code}, 响应内容: \n{res.text}')

        return None

    def _parse_response(self, response):
        """
        解析API响应
        
        Args:
            response: HTTP响应对象
            
        Returns:
            解析后的答案，如果解析失败则返回None
        """
        try:
            res_json = response.json()
        except json.JSONDecodeError:
            logger.error(f'{self.name}响应解析失败: 响应不是有效的JSON格式')
            return None
        except Exception as e:
            logger.error(f'{self.name}响应解析异常: {e}')
            return None

        # 记录响应消息
        msg = res_json.get('message', '')
        if msg:
            logger.info(f'{self.name}响应消息: {msg}')

        results = res_json.get('results', {})
        if not results or not isinstance(results, dict):
            logger.error(f'{self.name}查询结果格式错误: API返回结果中results字段格式不正确')
            return None

        output = results.get('output', None)
        if output is None or not isinstance(output, dict):
            logger.error(f'{self.name}查询结果中output字段格式错误或不存在')
            return None

        q_type = output.get('questionType', None)
        if q_type is None:
            logger.error(f'{self.name}查询结果中questionType字段不存在')
            return None

        answer = output.get('answer', None)
        if answer is None:
            logger.error(f'{self.name}查询结果中answer字段不存在')
            return None

        # 根据题目类型提取答案
        return self._extract_answer_by_type(q_type, answer)

    def _extract_answer_by_type(self, q_type: str, answer: dict) -> str:
        """
        根据题目类型提取答案
        
        Args:
            q_type: 题目类型
            answer: 答案字典
            
        Returns:
            提取的答案文本
        """
        if not isinstance(answer, dict):
            logger.error(f'{self.name}答案格式错误: 不是有效的字典格式')
            return None

        if q_type == "CHOICE":
            selected_options = answer.get('selectedOptions', None)
            if selected_options is not None:
                if isinstance(selected_options, list) and selected_options:
                    # 过滤掉None和空字符串
                    valid_options = [opt for opt in selected_options if opt is not None and str(opt).strip()]
                    if valid_options:
                        return '\n'.join(str(opt) for opt in valid_options)
                    else:
                        logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
                else:
                    logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
            else:
                logger.error(f'{self.name}CHOICE类型题目缺少selectedOptions字段')
        elif q_type == "FILL_IN_BLANK":
            blanks = answer.get('blanks', None)
            if blanks is not None:
                if isinstance(blanks, list) and blanks:
                    # 过滤掉None和空字符串
                    valid_blanks = [blank for blank in blanks if blank is not None and str(blank).strip()]
                    if valid_blanks:
                        return "\n".join(str(blank) for blank in valid_blanks)
                    else:
                        logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
                else:
                    logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
            else:
                logger.error(f'{self.name}FILL_IN_BLANK类型题目缺少blanks字段')
        elif q_type == "JUDGMENT":
            is_correct = answer.get('isCorrect', None)
            if is_correct is not None:
                return "正确" if is_correct else "错误"
            else:
                logger.error(f'{self.name}JUDGMENT类型题目缺少isCorrect字段')
        else:
            otherText = answer.get('otherText', None)
            if otherText is not None:
                return str(otherText)
            else:
                logger.error(f'{self.name}未知题目类型{q_type}且缺少otherText字段')

        return None

    def get_api_balance(self, token: str = ""):
        if not token:
            logger.error(f'{self.name}获取余额失败: 未提供有效的token')
            return 0

        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'
        try:
            res = requests.get(
                self.balance_api,
                headers=temp_headers,
                verify=False,
                timeout=self._timeout
            )
            if res.status_code == 200:
                res_json = res.json()
                return int(res_json.get("balance", 0))
            else:
                logger.error(f'{self.name}请求余额接口失败，状态码: {res.status_code}')
                return 0
        except requests.exceptions.Timeout:
            logger.error(f'{self.name}获取余额超时: 请求超过30秒')
            return 0
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name}网络连接错误: 无法连接到余额查询API服务器')
            return 0
        except ValueError:  # json解析错误或int转换错误
            logger.error(f'{self.name}余额响应解析失败: 响应格式不正确')
            return 0
        except Exception as e:
            logger.error(f'{self.name}Token余额查询过程中出现错误: {e}')
            return 0

    def update_times(self) -> None:
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何Token, 无法更新余额')
            return
        for token in self._tokens:
            balance = self.get_api_balance(token)
            self._balance[token] = balance
            logger.info(
                f"当前LIKE知识库Token: ...{token[-5:]} 的剩余查询次数为: {balance} (仅供参考, 实际次数以查询结果为准)")

    def load_tokens(self) -> None:
        tokens_str = self._conf.get('tokens')
        if not tokens_str:
            logger.error(f'{self.name}配置中未找到tokens')
            self._tokens = []
            return
        if ',' in tokens_str:
            tokens = [token.strip() for token in tokens_str.split(',') if token.strip()]
        else:
            tokens = [tokens_str.strip()] if tokens_str.strip() else []
        self._tokens = tokens
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何有效的Token')

    def load_config(self) -> None:
        # 从配置中获取参数，提供默认值
        self._search = self._conf.get('likeapi_search', False)
        self._model = self._conf.get('likeapi_model', None)
        self._vision = self._conf.get('likeapi_vision', True)
        self._retry = self._conf.get("likeapi_retry", True)
        self._retry_times = int(self._conf.get("likeapi_retry_times", 3))

    def _init_tiku(self) -> None:
        self.load_config()
        self.load_tokens()
        if self._tokens:
            self.update_times()
        else:
            logger.error(f'{self.name}初始化失败: 未加载任何有效的Token')
            self.DISABLE = True


class TikuAdapter(Tiku):
    # TikuAdapter题库实现 https://github.com/DokiDoki1103/tikuAdapter
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化TikuAdapter题库实例."""
        super().__init__(config_path)
        self.name = 'TikuAdapter题库'
        self.api = ''

    def _query(self, q_info: dict):
        # 判断题目类型
        if q_info['type'] == "single":
            type = 0
        elif q_info['type'] == 'multiple':
            type = 1
        elif q_info['type'] == 'completion':
            type = 2
        elif q_info['type'] == 'judgement':
            type = 3
        else:
            type = 4

        options = q_info['options']
        res = requests.post(
            self.api,
            json={
                'question': q_info['title'],
                'options': [sub(r'^[A-Za-z]\.?、?\s?', '', option) for option in options.split('\n')],
                'type': type
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            # if bool(res_json['plat']):
            # plat无论搜没搜到答案都返回0
            # 这个参数是tikuadapter用来设定自定义的平台类型
            if not len(res_json['answer']['bestAnswer']):
                logger.error("查询失败, 返回：" + res.text)
                return None
            sep = "\n"
            return sep.join(res_json['answer']['bestAnswer']).strip()
        # else:
        #   logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def _init_tiku(self):
        # self.load_token()
        self.api = self._conf['url']


class MultiTiku(Tiku):
    def __init__(self) -> None:
        super().__init__()
        self.name = '多题库协同'
        self.providers: list[Tiku] = []
        self.decision_provider: Optional[Tiku] = None

    def _supports_conflict_resolution(self, provider: Tiku) -> bool:
        return type(provider).resolve_conflict is not Tiku.resolve_conflict

    def _build_provider(self, provider_name: str) -> Optional[Tiku]:
        provider_class = globals().get(provider_name)
        if not provider_class or provider_class in {MultiTiku}:
            logger.error(f"未找到名为 {provider_name} 的题库实现")
            return None

        provider = provider_class()
        provider_config = dict(self._conf)
        provider_config["provider"] = provider_name
        provider.config_set(provider_config)
        provider.init_tiku()
        if provider.DISABLE:
            logger.warning(f"题库 {provider_name} 初始化失败，已跳过")
            return None
        return provider

    def _select_decision_provider(self, provider_names: list[str]) -> Optional[Tiku]:
        decision_provider_name = (self._conf.get("decision_provider") or "").strip()
        ordered_names = []
        if decision_provider_name:
            ordered_names.append(decision_provider_name)
        ordered_names.extend(["SiliconFlow", "AI"])
        ordered_names.extend(provider_names)

        for provider_name in ordered_names:
            for provider in self.providers:
                if provider.__class__.__name__ == provider_name and self._supports_conflict_resolution(provider):
                    return provider

        return None

    def _init_tiku(self) -> None:
        provider_names = parse_provider_names(self._conf.get("providers") or self._conf.get("provider"))
        if len(provider_names) < 2:
            logger.error("多题库模式至少需要两个 provider")
            self.DISABLE = True
            return

        self.providers = []
        for provider_name in provider_names:
            provider = self._build_provider(provider_name)
            if provider:
                self.providers.append(provider)

        if not self.providers:
            logger.error("多题库初始化失败，没有可用的题库提供方")
            self.DISABLE = True
            return

        self.decision_provider = self._select_decision_provider(provider_names)
        logger.info(f"已启用多题库协同：{', '.join(provider.name for provider in self.providers)}")
        if self.decision_provider:
            logger.info(f"冲突仲裁题库：{self.decision_provider.name}")

    def _query(self, q_info: dict) -> Optional[str]:
        if not self.providers:
            logger.error("多题库未初始化完成")
            return None

        candidate_answers: list[tuple[Tiku, str]] = []
        normalized_answers: dict[str, list[tuple[Tiku, str]]] = {}

        for provider in self.providers:
            answer = provider.query_without_cache(q_info)
            if not answer:
                continue

            candidate_answers.append((provider, answer))
            normalized_answer = normalize_answer_for_compare(answer, q_info["type"], self)
            normalized_answers.setdefault(normalized_answer, []).append((provider, answer))

        if not candidate_answers:
            return None

        if len(normalized_answers) == 1:
            provider, answer = candidate_answers[0]
            logger.info(f"多题库结果一致，采用 {provider.name} 的答案")
            return answer

        if self.decision_provider:
            logger.warning("多题库结果不一致，正在请求仲裁题库进行二次决策")
            resolved_answer = self.decision_provider.resolve_conflict(
                q_info,
                [(provider.name, answer) for provider, answer in candidate_answers],
            )
            if resolved_answer and self._validate_answer(resolved_answer, q_info):
                logger.info(f"仲裁题库已给出最终答案：{resolved_answer}")
                return resolved_answer.strip()
            logger.warning("仲裁题库未返回有效答案，回退到首个有效答案")

        fallback_provider, fallback_answer = candidate_answers[0]
        logger.warning(f"多题库答案冲突，暂时采用 {fallback_provider.name} 的答案")
        return fallback_answer

    def check_llm_connection(self) -> bool:
        checks = [provider.check_llm_connection() for provider in self.providers]
        return all(checks) if checks else True

class AI(Tiku):
    # AI大模型答题实现
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化AI大模型答题实现."""
        super().__init__(config_path)
        self.name = 'AI大模型答题'
        self.last_request_time = None
        self._lock = threading.Lock()

    def _is_deepseek_v4(self) -> bool:
        return (
                'api.deepseek.com' in (self.endpoint or '').lower()
                and (self.model or '').lower().startswith('deepseek-v4')
        )

    def _completion_kwargs(self, **kwargs):
        if self._is_deepseek_v4():
            # DeepSeek V4 defaults to thinking mode, which can leave message.content empty.
            kwargs['extra_body'] = {'thinking': {'type': 'disabled'}}
        return kwargs

    def _wait_for_interval(self):
        if self.last_request_time:
            interval_time = time.time() - self.last_request_time
            if interval_time < self.min_interval_seconds:
                sleep_time = self.min_interval_seconds - interval_time
                logger.debug(f"API请求间隔过短, 等待 {sleep_time} 秒")
                time.sleep(sleep_time)

    def _remove_md_json_wrapper(self, md_str: str) -> str:
        pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
        match = re.search(pattern, md_str, re.DOTALL)
        return match.group(1).strip() if match else md_str.strip()

    def _build_client(self) -> OpenAI:
        if self.http_proxy:
            httpx_client = httpx.Client(proxy=self.http_proxy, timeout=self.request_timeout_seconds)
            return OpenAI(
                http_client=httpx_client,
                base_url=self.endpoint,
                api_key=self.key,
                timeout=self.request_timeout_seconds,
            )
        return OpenAI(base_url=self.endpoint, api_key=self.key, timeout=self.request_timeout_seconds)

    def _wait_for_rate_limit(self) -> None:
        if not self.last_request_time:
            return

        interval_time = time.time() - self.last_request_time
        if interval_time < self.min_interval_seconds:
            sleep_time = self.min_interval_seconds - interval_time
            logger.debug(f"API请求间隔过短, 等待 {sleep_time} 秒")
            time.sleep(sleep_time)

    def _request_completion(self, messages: list[dict], max_tokens: int = 4096) -> Optional[str]:
        try:
            client = self._build_client()
            self._wait_for_rate_limit()
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
            )
            self.last_request_time = time.time()
            if completion.choices and completion.choices[0].message.content:
                return completion.choices[0].message.content
        except Exception as e:
            if messages_have_images(messages):
                logger.warning(f"{self.name} 图文请求失败，回退为纯文本重试：{e}")
                try:
                    client = self._build_client()
                    self._wait_for_rate_limit()
                    completion = client.chat.completions.create(
                        model=self.model,
                        messages=strip_images_from_messages(messages),
                        max_tokens=max_tokens,
                    )
                    self.last_request_time = time.time()
                    if completion.choices and completion.choices[0].message.content:
                        return completion.choices[0].message.content
                except Exception as retry_error:
                    logger.error(f"{self.name} 纯文本回退请求异常：{retry_error}")
            logger.error(f"{self.name} 请求异常：{e}")
        return None

    def _parse_answer_response(self, content: Optional[str]) -> Optional[str]:
        if not content:
            return None

        try:
            response = json.loads(self._remove_md_json_wrapper(content))
            return "\n".join(response['Answer']).strip()
        except Exception:
            logger.error("无法解析大模型输出内容")
            return None

    def _build_question_messages(self, q_info: dict) -> list[dict]:
        title_text = q_info.get('title_text') or normalize_rich_text_for_prompt(q_info.get('title'))
        options = normalize_prompt_options(q_info.get('options'))
        if q_info['type'] == "single":
            system_prompt = "本题为单选题，你只能选择一个选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
            user_prompt = f"题目：{title_text}\n选项：{options}"
        elif q_info['type'] == 'multiple':
            system_prompt = "本题为多选题，你必须选择两个或以上选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案1\",\"答案2\",\"答案3\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
            user_prompt = f"题目：{title_text}\n选项：{options}"
        elif q_info['type'] == 'completion':
            system_prompt = "本题为填空题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
            user_prompt = f"题目：{title_text}"
        elif q_info['type'] == 'judgement':
            system_prompt = "本题为判断题，你只能回答正确或者错误，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"正确\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
            user_prompt = f"题目：{title_text}"
        else:
            system_prompt = "本题为简答题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"这是我的答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
            user_prompt = f"题目：{title_text}"
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": build_multimodal_user_content(user_prompt, q_info.get("image_urls")),
            },
        ]

    def _build_conflict_messages(self, q_info: dict, candidate_answers: list[tuple[str, str]]) -> list[dict]:
        title_text = q_info.get("title_text") or normalize_rich_text_for_prompt(q_info.get("title"))
        options = normalize_prompt_options(q_info.get("options"))
        candidates_text = "\n".join(
            f"{index}. {provider_name}: {answer}"
            for index, (provider_name, answer) in enumerate(candidate_answers, start=1)
        )
        system_prompt = (
            "你是题库仲裁器。你会收到题目、选项和多个候选答案。请综合判断后输出最终答案，"
            "尽量从候选答案中选择最可信的一项，并以JSON格式输出：{\"Answer\": [\"最终答案\"]}。"
            "不要输出解释，不要使用Markdown。"
        )
        user_prompt = (
            f"题目类型：{q_info['type']}\n"
            f"题目：{title_text}\n"
            f"选项：{options}\n"
            f"候选答案：\n{candidates_text}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": build_multimodal_user_content(user_prompt, q_info.get("image_urls")),
            },
        ]

    def _query(self, q_info: dict):
        content = self._request_completion(self._build_question_messages(q_info))
        return self._parse_answer_response(content)

    def resolve_conflict(self, q_info: dict, candidate_answers: list[tuple[str, str]]) -> Optional[str]:
        content = self._request_completion(self._build_conflict_messages(q_info, candidate_answers))
        return self._parse_answer_response(content)

    def _init_tiku(self):
        self.endpoint = self._conf['endpoint']
        self.key = self._conf['key']
        self.model = self._conf['model']
        self.http_proxy = self._conf['http_proxy']
        self.min_interval_seconds = int(self._conf['min_interval_seconds'])
        self.request_timeout_seconds = int(self._conf.get('request_timeout_seconds', 600))

    def check_llm_connection(self) -> bool:
        """
        检查大模型连接是否可用
        发送一个简单的测试请求来验证 API 配置
        """
        logger.info(f'正在检查 {self.name} 连接...')
        try:
            client = self._build_client()
            
            # 发送一个简单的测试请求
            completion = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        'role': 'user',
                        'content': '你好，请回答：1+1 等于几？只回答数字。'
                    }
                ],
                max_tokens=10
            )
            
            if completion.choices and completion.choices[0].message.content:
                logger.info(f'{self.name} 连接检查成功')
                return True
            else:
                logger.error(f'{self.name} 连接检查失败：未收到响应')
                return False

        except Exception as e:
            logger.error(f'{self.name} 连接检查失败：{e}')
            return False

class SiliconFlow(Tiku):

    def __init__(self, config_path: Optional[str] = None):
        """初始化硅基流动大模型题库."""
        super().__init__(config_path)
        self.name = '硅基流动大模型'
        self.last_request_time = None
        self._lock = threading.Lock()

    def _wait_for_interval(self):
        if self.last_request_time:
            interval = time.time() - self.last_request_time
            if interval < self.min_interval:
                sleep_time = self.min_interval - interval
                logger.debug(f"API请求间隔过短, 等待 {sleep_time} 秒")
                time.sleep(sleep_time)

    def _remove_md_json_wrapper(self, md_str: str) -> str:
        pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
        match = re.search(pattern, md_str, re.DOTALL)
        return match.group(1).strip() if match else md_str.strip()

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _wait_for_rate_limit(self) -> None:
        if not self.last_request_time:
            return

        interval = time.time() - self.last_request_time
        if interval < self.min_interval:
            time.sleep(self.min_interval - interval)

    def _request_completion(self, messages: list[dict], max_tokens: int = 4096) -> Optional[str]:
        for attempt_messages, label in (
            (messages, "图文请求"),
            (strip_images_from_messages(messages), "纯文本回退"),
        ):
            if label == "纯文本回退" and not messages_have_images(messages):
                break

            payload = {
                "model": self.model_name,
                "messages": attempt_messages,
                "stream": False,
                "max_tokens": max_tokens,
                "temperature": 0.7,
                "top_p": 0.7,
                "response_format": {"type": "text"},
            }

            self._wait_for_rate_limit()
            try:
                response = requests.post(
                    self.api_endpoint,
                    headers=self._build_headers(),
                    json=payload,
                    timeout=self.request_timeout_seconds,
                )
                self.last_request_time = time.time()
                if response.status_code == 200:
                    result = response.json()
                    return result['choices'][0]['message']['content']

                if label == "图文请求" and messages_have_images(messages):
                    logger.warning(f"{self.name} 图文请求失败，准备回退为纯文本：{response.status_code} {response.text}")
                    continue
                logger.error(f"API请求失败：{response.status_code} {response.text}")
            except Exception as e:
                if label == "图文请求" and messages_have_images(messages):
                    logger.warning(f"{self.name} 图文请求异常，准备回退为纯文本：{e}")
                    continue
                logger.error(f"硅基流动API异常：{e}")
        return None

    def _parse_answer_response(self, content: Optional[str]) -> Optional[str]:
        if not content:
            return None

        try:
            parsed = json.loads(self._remove_md_json_wrapper(content))
            return "\n".join(parsed['Answer']).strip()
        except Exception:
            logger.error("无法解析硅基流动输出内容")
            return None

    def _build_question_messages(self, q_info: dict) -> list[dict]:
        title_text = q_info.get("title_text") or normalize_rich_text_for_prompt(q_info.get("title"))
        options_text = normalize_prompt_options(q_info.get('options'))
        if q_info['type'] == "single":
            system_prompt = "本题为单选题，请根据题目和选项选择唯一正确答案，输出的是选项的具体内容，而不是内容前的ABCD，并以JSON格式输出：示例回答：{\"Answer\": [\"正确选项内容\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'multiple':
            system_prompt = "本题为多选题，请选择所有正确选项，输出的是选项的具体内容，而不是内容前的ABCD，以JSON格式输出：示例回答：{\"Answer\": [\"选项1\",\"选项2\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'completion':
            system_prompt = "本题为填空题，请直接给出填空内容，以JSON格式输出：示例回答：{\"Answer\": [\"答案文本\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'judgement':
            system_prompt = "本题为判断题，请回答'正确'或'错误'，以JSON格式输出：示例回答：{\"Answer\": [\"正确\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        else:
            system_prompt = "本题为简答题，请直接给出最可能正确的答案，并以JSON格式输出：示例回答：{\"Answer\": [\"答案文本\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。"
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": build_multimodal_user_content(
                    f"题目：{title_text}\n选项：{options_text}",
                    q_info.get("image_urls"),
                ),
            },
        ]

    def _build_conflict_messages(self, q_info: dict, candidate_answers: list[tuple[str, str]]) -> list[dict]:
        title_text = q_info.get("title_text") or normalize_rich_text_for_prompt(q_info.get("title"))
        candidates_text = "\n".join(
            f"{index}. {provider_name}: {answer}"
            for index, (provider_name, answer) in enumerate(candidate_answers, start=1)
        )
        system_prompt = (
            "你是题库仲裁器。请根据题目、选项和多个候选答案做最终决策，"
            "优先返回最可能正确的答案，并以JSON格式输出：{\"Answer\": [\"最终答案\"]}。"
            "不要输出解释，不要使用Markdown。"
        )
        user_prompt = (
            f"题目类型：{q_info['type']}\n"
            f"题目：{title_text}\n"
            f"选项：{normalize_prompt_options(q_info.get('options'))}\n"
            f"候选答案：\n{candidates_text}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": build_multimodal_user_content(user_prompt, q_info.get("image_urls")),
            },
        ]

    def _query(self, q_info: dict):
        content = self._request_completion(self._build_question_messages(q_info))
        return self._parse_answer_response(content)

    def resolve_conflict(self, q_info: dict, candidate_answers: list[tuple[str, str]]) -> Optional[str]:
        content = self._request_completion(self._build_conflict_messages(q_info, candidate_answers))
        return self._parse_answer_response(content)

    def _init_tiku(self):
        # 从配置文件读取参数
        self.api_endpoint = self._conf.get('siliconflow_endpoint', 'https://api.siliconflow.cn/v1/chat/completions')
        self.api_key = self._conf['siliconflow_key']

        self.model_name = self._conf.get('siliconflow_model', 'deepseek-ai/DeepSeek-V3')
        self.min_interval = int(self._conf.get('min_interval_seconds', 3))
        self.request_timeout_seconds = int(self._conf.get('request_timeout_seconds', 600))

    def check_llm_connection(self) -> bool:
        """
        检查硅基流动大模型连接是否可用
        发送一个简单的测试请求来验证 API 配置
        """
        logger.info(f'正在检查 {self.name} 连接...')
        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'model': self.model_name,
                'messages': [
                    {
                        'role': 'user',
                        'content': '你好，请回答：1+1 等于几？只回答数字。'
                    }
                ],
                'stream': False,
                'max_tokens': 10,
                'temperature': 0.7,
                'top_p': 0.7,
                'response_format': {'type': 'text'}
            }
            
            response = requests.post(
                self.api_endpoint,
                headers=headers,
                json=payload,
                timeout=self.request_timeout_seconds
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('choices') and result['choices'][0]['message']['content']:
                    logger.info(f'{self.name} 连接检查成功')
                    return True
                else:
                    logger.error(f'{self.name} 连接检查失败：{response.status_code} {response.text}')
                    return False

            logger.error(f'{self.name} 连接检查失败：{response.status_code} {response.text}')
            return False
        except Exception as e:
            logger.error(f'{self.name} 连接检查失败：{e}')
            return False


class TikuManual(Tiku):
    _manual_lock = threading.Lock()
    is_manual = True

    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化手动题库实例."""
        super().__init__(config_path)
        self.name = '手动输入题库'
        self.default_mode = 'batch'
        self.skip_answer_validation = True

    @staticmethod
    def _extract_option_letters(ans: str) -> list[str]:
        cleaned = re.sub(r'[\s,，;；、]+', '', ans)
        if not cleaned or not re.fullmatch(r'[A-Za-z]+', cleaned):
            return []
        return [c.upper() for c in cleaned]

    def _init_tiku(self):
        self.default_mode = self._conf.get('manual_mode_default', 'batch').strip().lower()
        if self.default_mode not in ['batch', 'single']:
            self.default_mode = 'batch'

        self.separator = self._conf.get('manual_mode_separator', ';')
        if self.separator.lower() in ['\\n', 'newline', '换行']:
            self.separator = '\n'
        elif self.separator.lower() in ['space', '空格']:
            self.separator = ' '
        elif self.separator.lower() in ['tab', '制表符']:
            self.separator = '\t'

    @staticmethod
    def _safe_close_tqdm_bars():
        """安全地清除并关闭所有活动的 tqdm 进度条，防止私有属性变更引发异常."""
        try:
            from tqdm import tqdm
            if hasattr(tqdm, '_instances') and hasattr(tqdm._instances, '__iter__'):
                # 复制一份以防遍历时容器大小发生变化 (WeakSet/list)
                instances = list(tqdm._instances)
                for instance in instances:
                    try:
                        if hasattr(instance, 'leave'):
                            instance.leave = False
                        if hasattr(instance, 'clear'):
                            instance.clear()
                        if hasattr(instance, 'close'):
                            instance.close()
                    except Exception as ie:
                        logger.debug(f"清理单个 tqdm 实例失败: {ie}")
        except Exception as e:
            logger.debug(f"获取/清理 tqdm 实例列表失败: {e}")

    def _query(self, q_info: dict) -> Optional[str]:
        # 强行关闭清除所有当前活动的 tqdm 进度条
        self._safe_close_tqdm_bars()

        with self._manual_lock:
            ans = self._single_query(q_info)
        logger.debug("手动答题结束，冲刷缓存日志")
        return ans

    def _query_all(self, q_list: list[dict], query_delay: float = 0.0) -> list[Optional[str]]:
        # 强行关闭清除所有当前活动的 tqdm 进度条
        self._safe_close_tqdm_bars()

        with self._manual_lock:
            print(f"\n{'=' * 20} 手动输入题库 (共 {len(q_list)} 题) {'=' * 20}")
            if self.default_mode == 'batch':
                ans_list = self._batch_query_flow(q_list)
            else:
                ans_list = [self._single_query(q) for q in q_list]
        logger.debug("手动答题结束，冲刷缓存日志")
        return ans_list

    @staticmethod
    def _get_type_display(type_str: str) -> str:
        type_map = {
            'single': '单选题',
            'multiple': '多选题',
            'completion': '填空题',
            'judgement': '判断题'
        }
        return type_map.get(type_str, '其他类型')

    def _single_query(self, q: dict) -> Optional[str]:
        type_str = self._get_type_display(q['type'])
        if q['type'] in ['single', 'multiple'] and q.get('options'):
            options = q['options']
            parts = []
            if isinstance(options, str):
                parts = [o.strip() for o in options.split('\n') if o.strip()]
                if len(parts) <= 1:
                    from api.answer_check import cut
                    cut_parts = cut(options)
                    if cut_parts:
                        parts = cut_parts
            elif isinstance(options, list):
                parts = [str(o).strip() for o in options if str(o).strip()]

            options_text = "  ".join(parts)
            print(f"\n【{type_str}】 {q['title']} 选项: {options_text}")
        elif q['type'] == 'judgement':
            print(f"\n【{type_str}】 {q['title']} 选项: 正确 / 错误")
        else:
            print(f"\n【{type_str}】 {q['title']}")

        while True:
            ans = input("请输入答案 (直接回车表示跳过/无答案): ").strip()
            if not ans:
                print(f"  [已记录] 题目: {q['title']} ---> 答案: [跳过/随机]")
                return None

            # 即时校验
            ok, err_msg = self._validate_user_input(ans, q)
            if not ok:
                print(f"  \033[31m[输入错误] {err_msg}\033[0m")
                continue

            normalized_ans = self._normalize_user_input(ans, q)
            print(f"  [已记录] 题目: {q['title']} ---> 答案: {normalized_ans}")
            return normalized_ans

    def _validate_user_input(self, ans: str, q: dict) -> tuple[bool, str]:
        """
        验证用户手动输入的答案是否合规.
        """
        if not ans:
            return True, ""

        ans = ans.strip()
        if not ans:
            return True, ""

        q_type = q.get('type')
        if q_type == 'judgement':
            return self._validate_judgement_input(ans)
        elif q_type in ['single', 'multiple']:
            return self._validate_choice_input(ans, q)
        return True, ""

    def _validate_judgement_input(self, ans: str) -> tuple[bool, str]:
        """验证判断题手动输入是否合规."""
        val = ans.lower()
        valid_judgements = [
            'true', 't', '1', '对', '正确', '√', '是', 'yes', 'y',
            'false', 'f', '0', '错', '错误', '×', '否', 'no', 'n', '不对', '不正确'
        ]
        if val not in valid_judgements:
            return False, f"无法识别的判断词 '{ans}'，请输入：对/错、正确/错误、T/F、1/0"
        return True, ""

    def _validate_choice_input(self, ans: str, q: dict) -> tuple[bool, str]:
        """
        验证选择题手动输入是否合规.
        """
        options = q.get('options', '')
        parts = self._parse_options(options)
        valid_keys = self._extract_valid_keys(parts)

        if not valid_keys:
            return True, ""

        letters = self._extract_option_letters(ans)
        if not letters:
            return self._validate_text_match(ans, parts)

        invalid_letters = [letter for letter in letters if letter not in valid_keys]
        if invalid_letters:
            return False, f"输入包含无效的选项字母 {invalid_letters}，当前题目的可用选项为: {', '.join(valid_keys)}"

        if q.get('type') == 'single' and len(letters) > 1:
            return False, "当前是单选题，但输入了多个选项字母！"

        return True, ""

    def _parse_options(self, options) -> list[str]:
        """
        解析选项.
        """
        parts = []
        if isinstance(options, str):
            parts = [o.strip() for o in options.split('\n') if o.strip()]
            if len(parts) <= 1:
                from api.answer_check import cut
                cut_parts = cut(options)
                if cut_parts:
                    parts = cut_parts
        elif isinstance(options, list):
            parts = [str(o).strip() for o in options if str(o).strip()]
        return parts

    def _extract_valid_keys(self, parts: list[str]) -> list[str]:
        """
        提取合法的选项字母.
        """
        valid_keys = []
        for p in parts:
            first_char = p[:1].upper()
            if first_char.isalpha():
                valid_keys.append(first_char)
        return valid_keys

    def _validate_text_match(self, ans: str, parts: list[str]) -> tuple[bool, str]:
        """
        验证用户输入的文本是否和选项文本匹配.
        """
        from api.answer_check import cut
        split_ans = cut(ans)
        if split_ans:
            for item in split_ans:
                matched = False
                for p in parts:
                    p_norm = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', p).strip().lower()
                    if item.strip().lower() in p_norm or p_norm in item.strip().lower():
                        matched = True
                        break
                if not matched:
                    return False, f"输入的文本 '{item}' 在所有选项中均无法匹配，请输入合法的选项文本或字母"
        return True, ""

    def _normalize_user_input(self, ans: str, q: dict) -> Optional[str]:
        """
        规整化用户的手动输入答案.
        """
        if not ans:
            return None

        ans = ans.strip()
        if not ans:
            return None

        q_type = q.get('type')
        if q_type == 'judgement':
            return self._normalize_judgement_input(ans)
        elif q_type in ['single', 'multiple']:
            return self._normalize_choice_input(ans, q)
        return ans

    def _normalize_judgement_input(self, ans: str) -> str:
        """
        规整化判断题的手动输入.
        """
        val = ans.lower()
        if val in ['true', 't', '1', '对', '正确', '√', '是', 'yes', 'y']:
            return "正确"
        elif val in ['false', 'f', '0', '错', '错误', '×', '否', 'no', 'n', '不对', '不正确']:
            return "错误"
        return ans

    def _normalize_choice_input(self, ans: str, q: dict) -> str:
        """
        规整化选择题的手动输入.
        """
        options = q.get('options', '')
        parts = self._parse_options(options)
        valid_keys = self._extract_valid_keys(parts)

        letters = self._extract_option_letters(ans)
        if letters and all(letter in valid_keys for letter in letters):
            unique_ordered_letters = []
            for letter in letters:
                if letter not in unique_ordered_letters:
                    unique_ordered_letters.append(letter)
            return "\n".join(unique_ordered_letters)

        from api.answer_check import cut
        split_ans = cut(ans)
        if split_ans:
            return "\n".join(split_ans)
        return ans

    def _batch_query_flow(self, q_list: list[dict]) -> list[Optional[str]]:
        """
        执行批量手动搜题交互.
        """
        self._print_batch_questions(q_list)

        sep_desc = self.separator
        if self.separator == '\n':
            sep_desc = '换行 (每题一行)'
        elif self.separator == ' ':
            sep_desc = '空格'
        elif self.separator == '\t':
            sep_desc = 'Tab制表符'

        self._print_batch_instructions(sep_desc)

        while True:
            answers = []
            if self.separator == '\n':
                print(f"请直接粘贴或依次输入各题答案（每行一个，共 {len(q_list)} 行）：")
                for i in range(len(q_list)):
                    try:
                        ans = input(f"  第 {i + 1} 题答案: ").strip()
                    except EOFError:
                        ans = ""
                    answers.append(ans)
            else:
                raw_input = input(f"\n请一次性输入所有题目的答案 (使用 '{sep_desc}' 分割): ").strip()
                answers = self._split_batch_answers(raw_input, len(q_list))

            has_error, temp_answers = self._parse_and_validate_batch(q_list, answers)

            if has_error:
                print("\033[31m检测到存在不合规的答案，已拒绝确认，请重新输入！\033[0m")
                continue

            confirm = input("确认使用上述答案？[Y/n]: ").strip().lower()
            if confirm in ['', 'y', 'yes']:
                return temp_answers
            elif confirm == 'switch':
                return [self._single_query(q) for q in q_list]
            else:
                print("已取消，请重新输入，或输入 'switch' 切换为单题输入模式。")

    def _print_batch_questions(self, q_list: list[dict]) -> None:
        """批量打印题目内容及选项."""
        for idx, q in enumerate(q_list):
            type_str = self._get_type_display(q['type'])
            if q['type'] in ['single', 'multiple'] and q.get('options'):
                options = q['options']
                parts = []
                if isinstance(options, str):
                    parts = [o.strip() for o in options.split('\n') if o.strip()]
                    if len(parts) <= 1:
                        from api.answer_check import cut
                        cut_parts = cut(options)
                        if cut_parts:
                            parts = cut_parts
                elif isinstance(options, list):
                    parts = [str(o).strip() for o in options if str(o).strip()]

                options_text = "  ".join(parts)
                print(f"\n[{idx + 1}] 【{type_str}】 {q['title']} 选项: {options_text}")
            elif q['type'] == 'judgement':
                print(f"\n[{idx + 1}] 【{type_str}】 {q['title']} 选项: 正确 / 错误")
            else:
                print(f"\n[{idx + 1}] 【{type_str}】 {q['title']}")

    def _print_batch_instructions(self, sep_desc: str) -> None:
        """打印批量输入的使用引导说明."""
        print("\n" + "=" * 50)
        print("请依次输入每道题的答案。")
        print(f"格式要求：当前配置要求使用【{sep_desc}】分割各题的答案。")
        print("如果是多选题，答案中的多个选项直接连着写即可（例如：AB 或 AC）。")
        print("直接按回车或输入空格跳过的题，对应的答案将为空（会触发随机答题）。")
        if self.separator == '\n':
            print("粘贴多行时，每行会被解析为对应一题的答案。")
        else:
            print(f"示例输入: A{self.separator} B{self.separator} 正确{self.separator} 答案1, 答案2{self.separator} 错")
        print("=" * 50)

    def _split_batch_answers(self, raw_input: str, expected_len: int) -> list[str]:
        """根据配置的分割符将批量的答案进行分拆和补齐."""
        if not raw_input:
            return [''] * expected_len

        if self.separator in [';', '；']:
            raw_input = raw_input.replace('；', ';')
            answers = [ans.strip() for ans in raw_input.split(';')]
        elif self.separator in [',', '，']:
            raw_input = raw_input.replace('，', ',')
            answers = [ans.strip() for ans in raw_input.split(',')]
        else:
            answers = [ans.strip() for ans in raw_input.split(self.separator)]

        if len(answers) < expected_len:
            answers.extend([''] * (expected_len - len(answers)))
        elif len(answers) > expected_len:
            answers = answers[:expected_len]
        return answers

    def _parse_and_validate_batch(self, q_list: list[dict], answers: list[str]) -> tuple[bool, list[Optional[str]]]:
        """批量解析用户输入并进行合法性校验."""
        print("\n--- 解析答案结果 ---")
        has_error = False
        temp_answers = []
        for idx, (q, ans) in enumerate(zip(q_list, answers)):
            ok, err_msg = self._validate_user_input(ans, q)
            if not ok:
                has_error = True
                print(f"第 {idx + 1} 题: {q['title']} ---> \033[31m[错误: {err_msg}]\033[0m")
                temp_answers.append(None)
            else:
                normalized_ans = self._normalize_user_input(ans, q)
                temp_answers.append(normalized_ans)
                print(f"第 {idx + 1} 题: {q['title']} ---> 答案: {normalized_ans if normalized_ans else '[跳过/随机]'}")
        print("-------------------")
        return has_error, temp_answers


class DummyTiku(Tiku):
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化空题库."""
        super().__init__(config_path)
        self.name = '空/禁用题库'
        self.DISABLE = True

    def _query(self, q_info: dict) -> Optional[str]:
        return None


PROVIDER_REGISTRY = {
    'TikuYanxi': TikuYanxi,
    'TikuGo': TikuGo,
    'TikuLike': TikuLike,
    'TikuAdapter': TikuAdapter,
    'AI': AI,
    'SiliconFlow': SiliconFlow,
    'TikuManual': TikuManual,
}

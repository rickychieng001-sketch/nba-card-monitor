"""
工具函数模块
提供价格解析、涨跌计算、日志配置、请求辅助等通用能力
"""

import logging
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlparse, urlunparse

import requests
from dateutil import parser as date_parser

# 全局请求速率限制记录：按域名记录上次请求时间
_last_request_time = {}


def setup_logger(name: str, log_file: Optional[str] = None, level=logging.INFO) -> logging.Logger:
    """
    配置并返回一个结构化日志记录器
    日志格式：时间 | 级别 | 模块 | 消息
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出（可选）
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_random_user_agent() -> str:
    """
    返回一个随机 User-Agent，用于反爬
    """
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ]
    return random.choice(user_agents)


def get_default_headers() -> dict:
    """
    构造默认请求头，包含随机 User-Agent
    """
    return {
        "User-Agent": get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def rate_limited_request(url: str, delay: tuple = (2, 4), **kwargs) -> requests.Response:
    """
    带请求频率控制的 HTTP GET 请求
    :param url: 目标 URL
    :param delay: 请求间隔随机范围（秒），默认 2-4 秒
    :param kwargs: 透传给 requests.get 的其他参数
    :return: requests.Response
    """
    logger = logging.getLogger("utils.helpers")

    # 解析域名用于速率控制
    domain = urlparse(url).netloc or "unknown"
    now = time.time()
    last_time = _last_request_time.get(domain, 0)
    elapsed = now - last_time

    # 单平台每秒不超过 1 次：确保间隔至少 1 秒
    min_interval = max(1.0, random.uniform(*delay))
    if elapsed < min_interval:
        sleep_time = min_interval - elapsed
        logger.debug("[%s] 请求频率控制，休眠 %.2f 秒", domain, sleep_time)
        time.sleep(sleep_time)

    headers = kwargs.pop("headers", {})
    default_headers = get_default_headers()
    default_headers.update(headers)

    try:
        response = requests.get(url, headers=default_headers, timeout=30, **kwargs)
        _last_request_time[domain] = time.time()
        response.raise_for_status()
        return response
    except requests.RequestException as e:
        logger.error("请求失败: %s, 错误: %s", mask_sensitive_url(url), str(e))
        raise


def parse_price(price_text: str, currency_hint: Optional[str] = None) -> Optional[tuple]:
    """
    从价格文本中解析出数值和货币
    :param price_text: 原始价格文本，如 "¥8,500.00"、"$1,200"
    :param currency_hint: 货币提示，如 CNY/USD
    :return: (price: float, currency: str) 或 None
    """
    if not price_text:
        return None

    text = price_text.strip()

    # 货币符号识别
    currency = currency_hint or "USD"
    if "¥" in text or "CNY" in text.upper() or "人民币" in text or "元" in text:
        currency = "CNY"
    elif "$" in text or "USD" in text.upper():
        currency = "USD"

    # 提取数字（支持千分位逗号、小数点）
    number_match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    if not number_match:
        # 尝试直接提取数字
        number_match = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))

    if not number_match:
        return None

    try:
        price = float(number_match.group().replace(",", ""))
        return price, currency
    except ValueError:
        return None


def format_price(price: float, currency: str) -> str:
    """
    将价格和货币格式化为可读字符串
    """
    if currency == "CNY":
        return f"¥{price:,.2f}"
    return f"${price:,.2f}"


def parse_date(date_text: str) -> Optional[str]:
    """
    将各种日期文本解析为 ISO 格式 YYYY-MM-DD
    :param date_text: 日期文本
    :return: ISO 日期字符串或 None
    """
    if not date_text:
        return None

    try:
        dt = date_parser.parse(date_text)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def calculate_change(latest_price: float, previous_price: float) -> dict:
    """
    计算涨跌金额和百分比
    :return: {"change_amount": float, "change_percent": float, "is_abnormal": bool}
    """
    if previous_price == 0:
        return {"change_amount": 0.0, "change_percent": 0.0, "is_abnormal": False}

    change_amount = latest_price - previous_price
    change_percent = change_amount / previous_price

    return {
        "change_amount": round(change_amount, 4),
        "change_percent": round(change_percent, 6),
        "is_abnormal": False,  # 阈值判断由上层根据配置决定
    }


def mask_sensitive_url(url: str) -> str:
    """
    对 URL 中的敏感参数进行脱敏处理
    目前主要针对 token、key、auth 等参数
    """
    if not url:
        return url

    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url

        query_params = []
        for param in parsed.query.split("&"):
            if "=" in param:
                key, value = param.split("=", 1)
                lower_key = key.lower()
                # 脱敏参数：token/auth/secret/password/pwd 及各类 key（但不脱敏 keyword 搜索关键词）
                sensitive_terms = ["token", "auth", "secret", "password", "pwd"]
                is_sensitive = any(s in lower_key for s in sensitive_terms) or (
                    "key" in lower_key and "keyword" not in lower_key
                )
                if is_sensitive:
                    value = "***"
                query_params.append(f"{key}={value}")
            else:
                query_params.append(param)

        masked_query = "&".join(query_params)
        return urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, masked_query, parsed.fragment
        ))
    except Exception:
        return url


def get_today_shanghai() -> str:
    """
    获取上海时区当前日期（YYYY-MM-DD）
    """
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d")


def build_search_keywords(card: dict) -> list:
    """
    根据卡片配置构建搜索关键词列表
    优先使用标准名称，再使用别名
    """
    keywords = [card["name"]]
    aliases = card.get("aliases", [])
    if aliases:
        keywords.extend(aliases)
    return keywords


def fetch_html_with_fallback(url: str, delay: tuple = (2, 4), browser_wait: int = 5) -> Optional[str]:
    """
    先尝试 requests 抓取，失败或无内容时降级到 Playwright 浏览器渲染
    :param url: 目标 URL
    :param delay: requests 请求间隔（秒）
    :param browser_wait: Playwright 页面加载后等待时间（秒）
    :return: 页面 HTML 或 None
    """
    logger = logging.getLogger("utils.helpers")

    # 第一步：尝试 requests
    try:
        response = rate_limited_request(url, delay=delay)
        if response and response.text:
            # 如果返回 200 但内容明显是拦截页面，也视为失败
            text_lower = response.text.lower()
            if any(k in text_lower for k in ["blocked", "captcha", "access denied", "permission denied"]):
                logger.warning("requests 返回拦截页面，尝试浏览器渲染")
            else:
                return response.text
    except Exception as e:
        logger.debug("requests 抓取失败: %s", str(e))

    # 第二步：尝试 Playwright 浏览器渲染
    try:
        from utils.playwright_fetcher import fetch_with_browser
        html = fetch_with_browser(url, wait_seconds=browser_wait)
        if html:
            return html
    except Exception as e:
        logger.debug("Playwright 渲染失败: %s", str(e))

    logger.error("所有抓取方式均失败: %s", mask_sensitive_url(url))
    return None

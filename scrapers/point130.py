"""
130point.com 爬虫模块
130point 是一个聚合 eBay / PWCC / Goldin / MySlabs 等平台球星卡成交数据的网站
本模块使用 Playwright 浏览器渲染绕过 Cloudflare，抓取已成交记录
"""

import logging
import re
import time
from typing import List, Dict, Any
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

import sys
import os as os_mod
sys.path.insert(0, os_mod.path.dirname(os_mod.path.dirname(os_mod.path.abspath(__file__))))

from utils.helpers import parse_price, parse_date
from utils.playwright_fetcher import fetch_with_browser

logger = logging.getLogger("scrapers.point130")


class Point130Scraper:
    """
    130point 爬虫类
    统一接口：search(card_name) -> List[Dict]
    """

    BASE_URL = "https://www.130point.com/sales/"
    PLATFORM = "130point"
    CURRENCY = "USD"

    def __init__(self, max_pages: int = 2):
        """
        初始化爬虫
        :param max_pages: 最大抓取页数，默认 2 页
        """
        self.max_pages = max_pages

    def search(self, card_name: str, original_name: str = None) -> List[Dict[str, Any]]:
        """
        搜索指定卡片在 130point 的已成交记录
        :param card_name: 搜索关键词
        :param original_name: 卡片标准名称（预留）
        :return: 标准格式的成交记录列表
        """
        results = []
        logger.info("开始抓取 130point: %s", card_name)

        for page in range(1, self.max_pages + 1):
            try:
                url = self._build_search_url(card_name, page)
                logger.debug("130point 搜索 URL: %s", url)

                html = fetch_with_browser(url, wait_seconds=8)
                if not html:
                    logger.warning("130point 第 %d 页未获取到 HTML", page)
                    break

                soup = BeautifulSoup(html, "lxml")
                items = self._parse_list_page(soup)
                if not items:
                    logger.info("130point 第 %d 页无数据，停止翻页", page)
                    break

                results.extend(items)
                logger.info("130point 第 %d 页抓取 %d 条记录", page, len(items))

                time.sleep(3)

            except Exception as e:
                logger.error("130point 第 %d 页抓取失败: %s", page, str(e))
                break

        logger.info("130point 抓取完成: %s, 共 %d 条", card_name, len(results))
        return results

    def _build_search_url(self, keyword: str, page: int = 1) -> str:
        """
        构建 130point 搜索 URL
        """
        params = {"search": keyword}
        if page > 1:
            params["page"] = page
        return f"{self.BASE_URL}?{urlencode(params)}"

    def _parse_list_page(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """
        解析 130point 搜索结果页
        130point 页面结构可能变化，使用多种选择器尝试
        """
        items = []

        # 常见列表容器选择器
        product_selectors = [
            ".sale-item",
            ".result-item",
            ".listing-row",
            ".item-row",
            "table.sales-table tbody tr",
            "table tbody tr",
            ".search-result",
            "[class*='sale']",
            "[class*='result']",
        ]

        product_elements = []
        for selector in product_selectors:
            product_elements = soup.select(selector)
            if product_elements:
                logger.debug("130point 使用选择器: %s, 找到 %d 个元素", selector, len(product_elements))
                break

        for element in product_elements:
            try:
                record = self._parse_product_item(element)
                if record:
                    items.append(record)
            except Exception as e:
                logger.warning("解析 130point 商品项失败: %s", str(e))
                continue

        return items

    def _parse_product_item(self, element) -> Dict[str, Any]:
        """
        解析单个 130point 商品元素
        """
        # 提取标题
        title = ""
        for selector in [".title", ".item-title", "h3", "h4", "h5", ".name", "a", "td"]:
            title_elem = element.select_one(selector)
            if title_elem:
                title = title_elem.get_text(strip=True)
                if title:
                    break

        if not title or len(title) < 5:
            return None

        # 过滤非商品项
        text_lower = title.lower()
        if any(k in text_lower for k in ["loading", "no results", "sold for", "subscribe"]):
            return None

        # 提取链接
        url = ""
        link_elem = element.select_one("a[href]")
        if link_elem:
            href = link_elem.get("href", "")
            url = urljoin("https://www.130point.com", href)

        # 提取价格
        price = 0.0
        price_text = ""
        for selector in [
            ".price",
            ".sold-price",
            ".sale-price",
            ".current-price",
            "[class*='price']",
        ]:
            price_elem = element.select_one(selector)
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                parsed = parse_price(price_text, "USD")
                if parsed:
                    price, _ = parsed
                    break

        # 备用：从整个元素文本中提取 $xxx
        if price == 0.0:
            text = element.get_text(" ", strip=True)
            match = re.search(r"\$\s*([\d,]+\.?\d*)", text)
            if match:
                parsed = parse_price(f"${match.group(1)}", "USD")
                if parsed:
                    price, _ = parsed

        if price < 10:
            return None

        # 提取日期
        date_text = ""
        for selector in [
            ".date",
            ".sold-date",
            ".sale-date",
            ".time",
            "[class*='date']",
        ]:
            date_elem = element.select_one(selector)
            if date_elem:
                date_text = date_elem.get_text(strip=True)
                break

        record_date = parse_date(date_text) or self._today()

        return {
            "card_name": "",
            "platform": self.PLATFORM,
            "title": title,
            "price": price,
            "currency": self.CURRENCY,
            "date": record_date,
            "url": url,
        }

    def _today(self) -> str:
        """
        获取今天日期
        """
        from datetime import datetime, timezone, timedelta
        tz = timezone(timedelta(hours=8))
        return datetime.now(tz).strftime("%Y-%m-%d")

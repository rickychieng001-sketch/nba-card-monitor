"""
卡淘 (cardhobby.com.cn) 爬虫模块
负责抓取卡淘平台球星卡成交数据
"""

import logging
import re
import time
from typing import List, Dict, Any
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.helpers import fetch_html_with_fallback, parse_price, parse_date

logger = logging.getLogger("scrapers.cardhobby")


class CardHobbyScraper:
    """
    卡淘爬虫类
    统一接口：search(card_name) -> List[Dict]
    """

    BASE_URL = "https://www.cardhobby.com.cn/market/search"
    PLATFORM = "cardhobby"
    CURRENCY = "CNY"

    def __init__(self, max_pages: int = 3):
        """
        初始化爬虫
        :param max_pages: 最大抓取页数，默认 3 页
        """
        self.max_pages = max_pages

    def search(self, card_name: str) -> List[Dict[str, Any]]:
        """
        搜索指定卡片在卡淘平台的成交记录
        :param card_name: 卡片名称或搜索关键词
        :return: 标准格式的成交记录列表
        """
        results = []
        logger.info("开始抓取卡淘: %s", card_name)

        for page in range(1, self.max_pages + 1):
            try:
                url = self._build_search_url(card_name, page)
                logger.debug("卡淘搜索 URL: %s", url)

                html = fetch_html_with_fallback(url, delay=(2, 4), browser_wait=5)
                if not html:
                    logger.info("卡淘第 %d 页无数据，停止翻页", page)
                    break
                soup = BeautifulSoup(html, "lxml")

                items = self._parse_list_page(soup)
                if not items:
                    logger.info("卡淘第 %d 页无数据，停止翻页", page)
                    break

                results.extend(items)
                logger.info("卡淘第 %d 页抓取 %d 条记录", page, len(items))

                # 卡淘反爬：请求间隔 2-4 秒
                time.sleep(2)

            except Exception as e:
                logger.error("卡淘第 %d 页抓取失败: %s", page, str(e))
                break

        logger.info("卡淘抓取完成: %s, 共 %d 条", card_name, len(results))
        return results

    def _build_search_url(self, keyword: str, page: int = 1) -> str:
        """
        构建卡淘搜索 URL
        """
        params = {"keyword": keyword}
        if page > 1:
            params["page"] = page
        return f"{self.BASE_URL}?{urlencode(params)}"

    def _parse_list_page(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """
        解析卡淘列表页，提取成交记录
        注：卡淘页面结构可能会变化，这里使用常见电商列表选择器
        """
        items = []

        # 卡淘列表项常见容器（根据实际页面可能需调整）
        product_selectors = [
            ".product-item",
            ".goods-item",
            ".list-item",
            ".item",
            "[class*='product']",
            "[class*='item']",
        ]

        product_elements = []
        for selector in product_selectors:
            product_elements = soup.select(selector)
            if product_elements:
                break

        for element in product_elements:
            try:
                record = self._parse_product_item(element)
                if record:
                    items.append(record)
            except Exception as e:
                logger.warning("解析卡淘商品项失败: %s", str(e))
                continue

        return items

    def _parse_product_item(self, element) -> Dict[str, Any]:
        """
        解析单个商品元素
        """
        # 提取标题
        title = ""
        for selector in [".title", ".product-title", "h3", "h4", "a", ".name"]:
            title_elem = element.select_one(selector)
            if title_elem:
                title = title_elem.get_text(strip=True)
                if title:
                    break

        if not title:
            return None

        # 提取链接
        url = ""
        link_elem = element.select_one("a[href]")
        if link_elem:
            href = link_elem.get("href", "")
            url = urljoin("https://www.cardhobby.com.cn", href)

        # 提取价格（人民币）
        price = 0.0
        price_text = ""
        for selector in [".price", ".current-price", ".final-price", ".sold-price", ".rmb", "[class*='price']"]:
            price_elem = element.select_one(selector)
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                parsed = parse_price(price_text, "CNY")
                if parsed:
                    price, _ = parsed
                    break

        # 如果没有找到价格，尝试从整个元素文本中提取
        if price == 0.0:
            text = element.get_text(" ", strip=True)
            match = re.search(r"¥\s*([\d,]+\.?\d*)", text)
            if match:
                parsed = parse_price(f"¥{match.group(1)}", "CNY")
                if parsed:
                    price, _ = parsed

        # 卡淘有最低价格过滤
        if price < 100:
            return None

        # 提取成交日期
        date_text = ""
        for selector in [".date", ".time", ".end-time", ".sold-time", "[class*='date']", "[class*='time']"]:
            date_elem = element.select_one(selector)
            if date_elem:
                date_text = date_elem.get_text(strip=True)
                break

        # 如果未提取到日期，使用今天
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

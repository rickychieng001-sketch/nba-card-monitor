"""
eBay Sold 爬虫模块
负责抓取 eBay 平台已成交球星卡数据
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

from utils.helpers import rate_limited_request, parse_price, parse_date

logger = logging.getLogger("scrapers.ebay")


class EbayScraper:
    """
    eBay Sold 爬虫类
    统一接口：search(card_name) -> List[Dict]
    """

    BASE_URL = "https://www.ebay.com/sch/i.html"
    PLATFORM = "ebay"
    CURRENCY = "USD"

    def __init__(self, max_pages: int = 3):
        """
        初始化爬虫
        :param max_pages: 最大抓取页数，默认 3 页
        """
        self.max_pages = max_pages

    def search(self, card_name: str) -> List[Dict[str, Any]]:
        """
        搜索指定卡片在 eBay 的已成交记录
        :param card_name: 卡片名称或搜索关键词
        :return: 标准格式的成交记录列表
        """
        results = []
        logger.info("开始抓取 eBay Sold: %s", card_name)

        for page in range(1, self.max_pages + 1):
            try:
                url = self._build_search_url(card_name, page)
                logger.debug("eBay 搜索 URL: %s", url)

                response = rate_limited_request(url, delay=(2, 4))
                soup = BeautifulSoup(response.text, "lxml")

                items = self._parse_list_page(soup)
                if not items:
                    logger.info("eBay 第 %d 页无数据，停止翻页", page)
                    break

                results.extend(items)
                logger.info("eBay 第 %d 页抓取 %d 条记录", page, len(items))

                # eBay 反爬：请求间隔
                time.sleep(2)

            except Exception as e:
                logger.error("eBay 第 %d 页抓取失败: %s", page, str(e))
                break

        logger.info("eBay 抓取完成: %s, 共 %d 条", card_name, len(results))
        return results

    def _build_search_url(self, keyword: str, page: int = 1) -> str:
        """
        构建 eBay 已成交商品搜索 URL
        """
        params = {
            "_nkw": keyword,
            "LH_Sold": "1",
            "LH_Complete": "1",
        }
        if page > 1:
            params["_pgn"] = page
        return f"{self.BASE_URL}?{urlencode(params)}"

    def _parse_list_page(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """
        解析 eBay 搜索结果页
        eBay 页面结构较复杂，优先提取已成交(Sold)标签的条目
        """
        items = []

        # eBay 列表项常见容器
        product_selectors = [
            "ul.srp-results li.s-item",
            ".s-item",
            ".srp-result",
            "[data-gr4]",
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
                logger.warning("解析 eBay 商品项失败: %s", str(e))
                continue

        return items

    def _parse_product_item(self, element) -> Dict[str, Any]:
        """
        解析单个 eBay 商品元素
        """
        # 提取标题
        title = ""
        for selector in [".s-item__title", "h3", "h4", ".title"]:
            title_elem = element.select_one(selector)
            if title_elem:
                title = title_elem.get_text(strip=True)
                # 过滤 "Shop on eBay" 等非商品项
                if title and title not in ["Shop on eBay", "", " "]:
                    break

        if not title or "Shop on eBay" in title:
            return None

        # 检查是否已成交（优先提取 Sold 标签）
        text_content = element.get_text(" ", strip=True).lower()
        if "sold" not in text_content:
            return None

        # 提取链接
        url = ""
        link_elem = element.select_one("a.s-item__link") or element.select_one("a[href]")
        if link_elem:
            href = link_elem.get("href", "")
            url = urljoin("https://www.ebay.com", href)

        # 提取价格
        price = 0.0
        price_text = ""
        for selector in [
            ".s-item__price",
            ".notranslate",
            ".sold-price",
            ".price",
            "[class*='price']",
        ]:
            price_elem = element.select_one(selector)
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                parsed = parse_price(price_text, "USD")
                if parsed:
                    price, _ = parsed
                    break

        # 备用：从文本中正则提取 $xxx
        if price == 0.0:
            match = re.search(r"\$\s*([\d,]+\.?\d*)", text_content)
            if match:
                parsed = parse_price(f"${match.group(1)}", "USD")
                if parsed:
                    price, _ = parsed

        # 过滤低价
        if price < 10:
            return None

        # 提取日期
        date_text = ""
        for selector in [
            ".s-item__title--tagblock",
            ".s-item__endedDate",
            ".timeleft",
            "[class*='date']",
            "[class*='sold']",
        ]:
            date_elem = element.select_one(selector)
            if date_elem:
                date_text = date_elem.get_text(strip=True)
                break

        # 尝试从文本中提取 "Sold date" 信息
        if not date_text:
            date_match = re.search(r"sold\s+(\w+\s+\d{1,2},?\s+\d{4})", text_content, re.IGNORECASE)
            if date_match:
                date_text = date_match.group(1)

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

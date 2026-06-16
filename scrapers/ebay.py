"""
eBay Sold 爬虫模块
负责抓取 eBay 平台已成交球星卡数据

支持两种模式：
1. eBay Finding API（推荐，稳定、免费，需申请 App ID）
2. HTML 页面解析（备用，容易被反爬拦截）
"""

import logging
import os
import re
import time
from typing import List, Dict, Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

import sys
import os as os_mod
sys.path.insert(0, os_mod.path.dirname(os_mod.path.dirname(os_mod.path.abspath(__file__))))

from utils.helpers import fetch_html_with_fallback, parse_price, parse_date

logger = logging.getLogger("scrapers.ebay")


class EbayScraper:
    """
    eBay Sold 爬虫类
    统一接口：search(card_name) -> List[Dict]
    """

    BASE_URL = "https://www.ebay.com/sch/i.html"
    API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
    PLATFORM = "ebay"
    CURRENCY = "USD"

    def __init__(self, max_pages: int = 3, app_id: str = None):
        """
        初始化爬虫
        :param max_pages: 最大抓取页数，默认 3 页
        :param app_id: eBay Finding API App ID，默认读取环境变量 EBAY_APP_ID
        """
        self.max_pages = max_pages
        self.app_id = app_id or os.environ.get("EBAY_APP_ID", "")

    def search(self, card_name: str) -> List[Dict[str, Any]]:
        """
        搜索指定卡片在 eBay 的已成交记录
        优先使用 Finding API，未配置 App ID 时降级到 HTML 抓取
        :param card_name: 卡片名称或搜索关键词
        :return: 标准格式的成交记录列表
        """
        if self.app_id:
            logger.info("使用 eBay Finding API 抓取: %s", card_name)
            return self._search_api(card_name)

        logger.info("未配置 eBay App ID，使用 HTML 抓取: %s", card_name)
        return self._search_html(card_name)

    def _search_api(self, card_name: str) -> List[Dict[str, Any]]:
        """
        使用 eBay Finding API findCompletedItems 搜索已成交记录
        """
        results = []
        entries_per_page = 20

        for page in range(1, self.max_pages + 1):
            try:
                params = {
                    "OPERATION-NAME": "findCompletedItems",
                    "SERVICE-VERSION": "1.0.0",
                    "SECURITY-APPNAME": self.app_id,
                    "RESPONSE-DATA-FORMAT": "JSON",
                    "REST-PAYLOAD": "",
                    "keywords": card_name,
                    "paginationInput.entriesPerPage": entries_per_page,
                    "paginationInput.pageNumber": page,
                    "sortOrder": "EndTimeSoonest",
                }

                response = requests.get(
                    self.API_URL,
                    params=params,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()

                items = self._parse_api_response(data)
                if not items:
                    logger.info("eBay API 第 %d 页无数据，停止翻页", page)
                    break

                results.extend(items)
                logger.info("eBay API 第 %d 页抓取 %d 条记录", page, len(items))
                time.sleep(1)

            except Exception as e:
                logger.error("eBay API 第 %d 页抓取失败: %s", page, str(e))
                break

        logger.info("eBay API 抓取完成: %s, 共 %d 条", card_name, len(results))
        return results

    def _parse_api_response(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        解析 eBay Finding API 返回的 JSON
        """
        items = []
        try:
            root = data.get("findCompletedItemsResponse", [{}])[0]
            search_result = root.get("searchResult", [{}])[0]
            item_list = search_result.get("item", [])
        except (KeyError, IndexError) as e:
            logger.warning("eBay API 返回结构异常: %s", str(e))
            return items

        for item in item_list:
            try:
                record = self._parse_api_item(item)
                if record:
                    items.append(record)
            except Exception as e:
                logger.warning("解析 eBay API 商品项失败: %s", str(e))
                continue

        return items

    def _parse_api_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        将单条 eBay API item 转换为标准格式
        """
        title = self._extract_api_value(item, "title")
        if not title:
            return None

        # 价格
        selling_status = item.get("sellingStatus", [{}])[0]
        current_price = selling_status.get("currentPrice", [{}])[0]
        price = float(current_price.get("__value__", 0))
        currency = current_price.get("@currencyId", "USD")

        if price < 10:
            return None

        # 日期
        listing_info = item.get("listingInfo", [{}])[0]
        end_time = self._extract_api_value(listing_info, "endTime")
        record_date = parse_date(end_time) or self._today()

        # 链接
        url = self._extract_api_value(item, "viewItemURL")

        return {
            "card_name": "",
            "platform": self.PLATFORM,
            "title": title,
            "price": price,
            "currency": currency,
            "date": record_date,
            "url": url,
        }

    @staticmethod
    def _extract_api_value(item: Dict[str, Any], key: str) -> str:
        """
        eBay API 的值通常是 ["value"] 数组形式，提取第一个字符串
        """
        value = item.get(key, [""])
        if isinstance(value, list) and value:
            return value[0]
        return value or ""

    def _search_html(self, card_name: str) -> List[Dict[str, Any]]:
        """
        使用 HTML 页面解析抓取 eBay Sold
        容易被反爬，作为 API 不可用时的备用
        """
        results = []
        logger.info("开始抓取 eBay Sold HTML: %s", card_name)

        for page in range(1, self.max_pages + 1):
            try:
                url = self._build_search_url(card_name, page)
                logger.debug("eBay 搜索 URL: %s", url)

                html = fetch_html_with_fallback(url, delay=(2, 4), browser_wait=5)
                if not html:
                    logger.info("eBay 第 %d 页无数据，停止翻页", page)
                    break

                soup = BeautifulSoup(html, "lxml")
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

        logger.info("eBay HTML 抓取完成: %s, 共 %d 条", card_name, len(results))
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

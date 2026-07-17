"""
卡淘 (cardhobby.com.cn) 爬虫模块
通过官方内部 API 抓取球星卡市场数据
"""

import json
import logging
import re
import time
from typing import List, Dict, Any
from urllib.parse import urlencode, urljoin

import requests

import sys
import os as os_mod
sys.path.insert(0, os_mod.path.dirname(os_mod.path.dirname(os_mod.path.abspath(__file__))))

from utils.helpers import rate_limited_request, parse_price, parse_date

logger = logging.getLogger("scrapers.cardhobby")


class CardHobbyScraper:
    """
    卡淘爬虫类
    统一接口：search(card_name) -> List[Dict]
    """

    SEARCH_URL = "https://www.cardhobby.com.cn/NewCommodity/SearchCommodity"
    ITEM_URL = "https://www.cardhobby.com.cn/market/item/{id}"
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
        搜索指定卡片在卡淘平台的市场记录
        注：卡淘该 API 返回的是出售中/拍卖中的商品，非已成交记录
        :param card_name: 卡片名称或搜索关键词
        :return: 标准格式的成交记录列表
        """
        results = []
        logger.info("开始抓取卡淘 API: %s", card_name)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.cardhobby.com.cn/market/search",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

        for page in range(1, self.max_pages + 1):
            try:
                params = {
                    "userId": "",
                    "pageIndex": page,
                    "pageSize": 20,
                    "searchKey": card_name,
                    "searchJson": json.dumps([{"Key": "Status", "Value": 1}]),
                    "sort": "EffectiveTimeStamp",
                    "sortType": "asc",
                }

                logger.debug("卡淘 API 请求: %s", params)
                response = requests.get(
                    self.SEARCH_URL,
                    params=params,
                    headers=headers,
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()

                items = self._parse_api_response(data)
                if not items:
                    logger.info("卡淘 API 第 %d 页无数据，停止翻页", page)
                    break

                results.extend(items)
                logger.info("卡淘 API 第 %d 页抓取 %d 条记录", page, len(items))

                # 请求频率控制
                time.sleep(2)

            except Exception as e:
                logger.error("卡淘 API 第 %d 页抓取失败: %s", page, str(e))
                break

        logger.info("卡淘 API 抓取完成: %s, 共 %d 条", card_name, len(results))
        return results

    def _parse_api_response(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        解析卡淘 API 返回的 JSON
        """
        items = []
        try:
            api_data = data.get("data", {})
            item_list = api_data.get("PagedMarketItemList", [])
        except (KeyError, AttributeError) as e:
            logger.warning("卡淘 API 返回结构异常: %s", str(e))
            return items

        for item in item_list:
            try:
                record = self._parse_api_item(item)
                if record:
                    items.append(record)
            except Exception as e:
                logger.warning("解析卡淘 API 商品项失败: %s", str(e))
                continue

        return items

    def _parse_api_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        将单条卡淘 API item 转换为标准格式
        """
        title = item.get("Title", "").strip()
        if not title:
            return None

        # 价格：优先使用 LowestPrice（起拍/当前展示价），否则使用 Price
        price_text = item.get("LowestPrice") or item.get("Price") or "0"
        try:
            price = float(str(price_text).replace(",", ""))
        except (ValueError, TypeError):
            price = 0.0

        # 日期：拍卖/出售结束时间
        date_text = item.get("EffectiveDate", "")
        record_date = parse_date(date_text) or self._today()

        # 链接
        item_id = item.get("ID")
        url = self.ITEM_URL.format(id=item_id) if item_id else ""

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

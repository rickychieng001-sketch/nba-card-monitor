"""
卡淘 (cardhobby.com.cn) 爬虫模块
通过官方内部 API 抓取球星卡市场数据

重要说明：
- 卡淘该 API 返回的是出售中/拍卖中的商品，不是已成交记录
- 已做标题关键词匹配，只保留与卡片名称高度相关的商品
"""

import json
import logging
import re
import time
from typing import List, Dict, Any, Tuple

import requests

import sys
import os as os_mod
sys.path.insert(0, os_mod.path.dirname(os_mod.path.dirname(os_mod.path.abspath(__file__))))

from utils.helpers import parse_date

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

    # 关键术语的中英文映射，用于标题匹配
    TERM_ALIASES = {
        "refractor": ["refractor", "折射", "银折", "折"],
        "psa": ["psa", "评级"],
        "chrome": ["chrome", "tc"],
        "wembanyama": ["wembanyama", "文班亚马"],
        "doncic": ["doncic", "东契奇", "luka"],
        "flagg": ["flagg", "弗拉格", "库珀"],
        "prizm": ["prizm", "prizim"],
        "rpa": ["rpa"],
        "nt": ["national treasures", "nt"],
        "silver": ["silver", "银"],
    }

    def __init__(self, max_pages: int = 3, min_match_score: float = 0.6):
        """
        初始化爬虫
        :param max_pages: 最大抓取页数，默认 3 页
        :param min_match_score: 标题最低匹配分数（0-1），低于此值会被过滤
        """
        self.max_pages = max_pages
        self.min_match_score = min_match_score

    def search(self, card_name: str) -> List[Dict[str, Any]]:
        """
        搜索指定卡片在卡淘平台的市场记录
        :param card_name: 卡片名称或搜索关键词
        :return: 标准格式的市场记录列表（按匹配度排序）
        """
        raw_results = []
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

                raw_results.extend(items)
                logger.info("卡淘 API 第 %d 页抓取 %d 条记录", page, len(items))

                # 请求频率控制
                time.sleep(2)

            except Exception as e:
                logger.error("卡淘 API 第 %d 页抓取失败: %s", page, str(e))
                break

        # 标题匹配过滤与排序
        results = self._filter_by_relevance(card_name, raw_results)
        logger.info(
            "卡淘 API 抓取完成: %s, 原始 %d 条, 匹配 %d 条",
            card_name, len(raw_results), len(results)
        )
        return results

    def _filter_by_relevance(self, card_name: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        根据卡片名称与标题的匹配度过滤结果
        只返回匹配分数 >= min_match_score 的记录，并按匹配度降序排列
        """
        key_terms = self._extract_key_terms(card_name)
        if not key_terms:
            return records

        scored = []
        for record in records:
            score = self._title_match_score(record.get("title", ""), key_terms)
            if score >= self.min_match_score:
                record["_match_score"] = round(score, 3)
                scored.append(record)

        scored.sort(key=lambda x: x["_match_score"], reverse=True)
        return scored

    def _extract_key_terms(self, card_name: str) -> List[Tuple[str, List[str]]]:
        """
        从卡片名称中提取关键匹配项
        返回 [(原始词, [原始词, 别名1, 别名2, ...]), ...]
        """
        # 拆分为英文单词、数字组合
        tokens = re.findall(r"[A-Za-z0-9]+(?:/[A-Za-z0-9]+)?", card_name)

        # 过滤掉太泛的词汇，保留有意义的词
        stop_words = {"the", "and", "of", "in", "on", "at", "to", "for", "with", "rc"}
        terms = []
        for token in tokens:
            lower = token.lower()
            if lower in stop_words or len(lower) <= 1:
                continue

            # 合并相邻的 PSA 和 10
            if lower == "psa" and "10" in card_name.lower().split("psa")[-1][:5]:
                token = "psa10"
                lower = "psa10"

            aliases = self.TERM_ALIASES.get(lower, [lower])
            terms.append((lower, aliases))

        # 添加年份作为可选匹配项（不强求）
        year_match = re.search(r"\d{4}-\d{2}", card_name)
        if year_match:
            terms.append(("year", [year_match.group().replace("-", "")]))

        return terms

    def _title_match_score(self, title: str, key_terms: List[Tuple[str, List[str]]]) -> float:
        """
        计算标题与关键术语的匹配分数
        每个关键术语找到匹配得 1 分，总分除以关键术语数
        """
        if not title or not key_terms:
            return 0.0

        title_lower = title.lower()
        # 移除空格和特殊字符，便于匹配 psa10 等连写形式
        title_compact = re.sub(r"[^\w\u4e00-\u9fff]", "", title_lower)

        matched = 0
        for _, aliases in key_terms:
            if self._any_alias_present(aliases, title_lower, title_compact):
                matched += 1

        return matched / len(key_terms)

    def _any_alias_present(self, aliases: List[str], title_lower: str, title_compact: str) -> bool:
        """
        检查标题是否包含任一别名
        """
        for alias in aliases:
            alias_lower = alias.lower()
            if alias_lower in title_lower:
                return True
            # 兼容 psa10、psa10分 等无空格写法
            alias_compact = re.sub(r"[^\w\u4e00-\u9fff]", "", alias_lower)
            if alias_compact and alias_compact in title_compact:
                return True
        return False

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

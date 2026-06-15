"""
飞书 Webhook 推送模块
负责发送每日日报和异常价格提醒
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import List, Dict, Any, Optional

import requests

import sys
import os as os_mod
sys.path.insert(0, os_mod.path.dirname(os_mod.path.dirname(os_mod.path.abspath(__file__))))

from utils.helpers import format_price, get_today_shanghai

logger = logging.getLogger("alerts.feishu")


class FeishuAlert:
    """
    飞书消息推送类
    支持每日日报 send_daily_report 和异常提醒 send_alert
    """

    def __init__(self, webhook_url: Optional[str] = None, secret: Optional[str] = None):
        """
        初始化飞书推送
        :param webhook_url: 飞书 Webhook 地址，默认从环境变量 FEISHU_WEBHOOK 读取
        :param secret: 飞书机器人签名校验密钥，默认从环境变量 FEISHU_SECRET 读取
        """
        self.webhook_url = webhook_url or os.environ.get("FEISHU_WEBHOOK", "")
        self.secret = secret or os.environ.get("FEISHU_SECRET", "")
        if not self.webhook_url:
            logger.warning("未配置飞书 Webhook 地址，推送将不可用")
        if not self.secret:
            logger.info("未配置飞书签名校验密钥，将以无签名模式发送")

    @staticmethod
    def _generate_sign(secret: str, timestamp: int) -> str:
        """
        生成飞书自定义机器人签名字符串
        算法：Base64(HMAC-SHA256(timestamp + "\n" + secret))
        """
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    def _send_with_retry(self, payload: Dict[str, Any], max_retries: int = 3) -> bool:
        """
        发送飞书消息，失败时重试
        :param payload: 飞书消息体
        :param max_retries: 最大重试次数
        :return: 是否发送成功
        """
        if not self.webhook_url:
            logger.error("飞书 Webhook 未配置，无法发送消息")
            return False

        # 如果配置了签名校验密钥，生成 timestamp 和 sign
        if self.secret:
            timestamp = int(time.time())
            sign = self._generate_sign(self.secret, timestamp)
            payload["timestamp"] = timestamp
            payload["sign"] = sign
            logger.debug("已添加飞书签名校验: timestamp=%s", timestamp)

        headers = {"Content-Type": "application/json"}

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(
                    self.webhook_url,
                    headers=headers,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    timeout=30,
                )
                response.raise_for_status()
                result = response.json()

                if result.get("code") == 0:
                    logger.info("飞书消息发送成功")
                    return True
                else:
                    logger.warning("飞书返回错误: %s", result)

            except requests.RequestException as e:
                logger.error("飞书消息发送失败（第 %d 次）: %s", attempt, str(e))

            if attempt < max_retries:
                sleep_time = 2 ** attempt  # 指数退避
                logger.info("%d 秒后重试...", sleep_time)
                time.sleep(sleep_time)

        logger.error("飞书消息发送最终失败，已重试 %d 次", max_retries)
        return False

    def send_daily_report(self, data: Dict[str, Any]) -> bool:
        """
        发送每日监控日报
        :param data: 汇总后的日报数据，包含 summary 和 details 等字段
        :return: 是否发送成功
        """
        logger.info("开始组装每日日报")

        today = data.get("date", get_today_shanghai())
        summary = data.get("summary", {})
        details = data.get("details", [])
        abnormal_items = data.get("abnormal_items", [])

        # 概况文本
        overview_text = (
            f"**📊 今日概况**\n"
            f"监控卡片：{summary.get('total_cards', 0)} 张 | "
            f"平台：{summary.get('total_platforms', 0)} 个 | "
            f"异常：{summary.get('abnormal_count', 0)} 张"
        )

        # 价格明细表格
        table_header = "| 卡片 | 平台 | 最新价 | 涨跌 |"
        table_sep = "|---|---|---|---|"
        table_rows = [table_header, table_sep]

        for item in details:
            card_name = item.get("card_name", "未知卡片")
            platform = item.get("platform", "")
            latest_price = item.get("latest_price", 0)
            currency = item.get("latest_currency", "USD")
            change_percent = item.get("change_percent", 0)
            is_abnormal = item.get("is_abnormal", False)

            price_str = format_price(latest_price, currency)

            if change_percent > 0:
                change_str = f"↑{change_percent * 100:.1f}%"
            elif change_percent < 0:
                change_str = f"↓{abs(change_percent) * 100:.1f}%"
            else:
                change_str = "—"

            if is_abnormal:
                change_str = f"**{change_str}** ⚠️"

            # 表格中卡片名过长时截断
            display_name = card_name[:20] + "..." if len(card_name) > 20 else card_name
            table_rows.append(f"| {display_name} | {platform} | {price_str} | {change_str} |")

        detail_text = "**📈 价格明细**\n" + "\n".join(table_rows) if len(table_rows) > 2 else "**📈 价格明细**\n暂无数据"

        # 异常提醒文本
        if abnormal_items:
            abnormal_texts = []
            for item in abnormal_items:
                card_name = item.get("card_name", "")
                platform = item.get("platform", "")
                latest_price = item.get("latest_price", 0)
                currency = item.get("latest_currency", "USD")
                change_percent = item.get("change_percent", 0)
                direction = "上涨" if change_percent > 0 else "下跌"
                abnormal_texts.append(
                    f"【{card_name}】{platform} 成交 {format_price(latest_price, currency)}，"
                    f"较上次{direction} {abs(change_percent) * 100:.1f}%，建议关注。"
                )
            abnormal_text = "**⚠️ 异常波动**\n" + "\n".join(abnormal_texts)
        else:
            abnormal_text = "**⚠️ 异常波动**\n今日未发现异常波动。"

        # 市场简评（可扩展）
        market_comment = data.get(
            "market_comment",
            "今日市场整体平稳，建议持续观察重点卡片走势。"
        )
        comment_text = f"**📊 市场简评**\n{market_comment}"

        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"🏀 球星卡监控日报 | {today}"
                    },
                    "template": "blue"
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": overview_text}
                    },
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": detail_text}
                    },
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": abnormal_text}
                    },
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": comment_text}
                    },
                ]
            }
        }

        return self._send_with_retry(payload)

    def send_alert(self, card_data: Dict[str, Any]) -> bool:
        """
        发送单张卡片价格异常提醒
        :param card_data: 单条价格变动记录
        :return: 是否发送成功
        """
        logger.info("开始组装异常提醒: %s", card_data.get("card_name", ""))

        card_name = card_data.get("card_name", "未知卡片")
        platform = card_data.get("platform", "")
        latest_price = card_data.get("latest_price", 0)
        latest_currency = card_data.get("latest_currency", "USD")
        previous_price = card_data.get("previous_price", 0)
        previous_currency = card_data.get("previous_currency", "USD")
        change_percent = card_data.get("change_percent", 0)
        date = card_data.get("date", get_today_shanghai())
        url = card_data.get("url", "")

        direction = "↑" if change_percent > 0 else "↓"
        change_str = f"{direction} {abs(change_percent) * 100:.1f}%"

        link_text = f"[查看详情]({url})" if url else "暂无链接"

        content = (
            f"**【卡片】** {card_name}\n"
            f"**【平台】** {platform}\n"
            f"**【最新成交】** {format_price(latest_price, latest_currency)}\n"
            f"**【上次成交】** {format_price(previous_price, previous_currency)}\n"
            f"**【涨跌】** {change_str}\n"
            f"**【时间】** {date}\n"
            f"**【链接】** {link_text}"
        )

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "⚠️ 球星卡价格异常提醒"},
                    "template": "red"
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": content}
                    }
                ]
            }
        }

        return self._send_with_retry(payload)

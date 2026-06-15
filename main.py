"""
NBA 球星卡多平台价格监控 + 飞书推送系统
主入口模块
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List

import yaml

from alerts.feishu import FeishuAlert
from scrapers.cardhobby import CardHobbyScraper
from scrapers.ebay import EbayScraper
from scrapers.goldin import GoldinScraper
from scrapers.pwcc import PwccScraper
from storage.database import PriceDatabase
from utils.helpers import (
    setup_logger,
    calculate_change,
    get_today_shanghai,
    build_search_keywords,
)

# 平台名称到爬虫类的映射
PLATFORM_MAP = {
    "cardhobby": CardHobbyScraper,
    "ebay": EbayScraper,
    "goldin": GoldinScraper,
    "pwcc": PwccScraper,
}


def load_config(config_path: str) -> Dict[str, Any]:
    """
    加载 YAML 配置文件
    :param config_path: 配置文件路径
    :return: 配置字典
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 环境变量覆盖 Webhook 地址和签名校验密钥
    env_webhook = os.environ.get("FEISHU_WEBHOOK")
    if env_webhook:
        config.setdefault("notifications", {}).setdefault("feishu", {})["webhook_url"] = env_webhook

    env_secret = os.environ.get("FEISHU_SECRET")
    if env_secret:
        config.setdefault("notifications", {}).setdefault("feishu", {})["secret"] = env_secret

    return config


def filter_cards(cards: List[Dict[str, Any]], card_name: str = None) -> List[Dict[str, Any]]:
    """
    根据命令行参数过滤卡片
    :param cards: 配置中的所有卡片
    :param card_name: 指定的卡片名称（可选）
    :return: 过滤后的卡片列表
    """
    if not card_name:
        return cards

    filtered = [card for card in cards if card_name.lower() in card.get("name", "").lower()]
    if not filtered:
        logging.warning("未找到匹配的卡片: %s", card_name)
    return filtered


def scrape_card(card: Dict[str, Any], scraper_instance, min_price: float, logger: logging.Logger) -> List[Dict[str, Any]]:
    """
    对单个卡片使用指定爬虫抓取数据，并做价格过滤
    :param card: 卡片配置
    :param scraper_instance: 爬虫实例
    :param min_price: 该平台最低价格阈值
    :param logger: 日志器
    :return: 过滤后的价格记录列表
    """
    records = []
    keywords = build_search_keywords(card)

    for keyword in keywords:
        try:
            results = scraper_instance.search(keyword)
            for record in results:
                record["card_name"] = card["name"]
                # 价格过滤
                if record.get("price", 0) >= min_price:
                    records.append(record)
                else:
                    logger.debug(
                        "价格低于阈值，过滤: %s %.2f",
                        record.get("title", ""), record.get("price", 0)
                    )

            # 如果标准名称已抓到数据，不再用别名尝试（避免重复）
            if results and keyword == card["name"]:
                break

        except Exception as e:
            logger.error("抓取失败 [%s]: %s", keyword, str(e))
            continue

    return records


def get_min_price_threshold(config: Dict[str, Any], currency: str) -> float:
    """
    根据货币类型获取最低价格阈值
    """
    thresholds = config.get("thresholds", {})
    if currency == "CNY":
        return thresholds.get("min_price_cny", 100)
    return thresholds.get("min_price_usd", 10)


def save_records(db: PriceDatabase, records: List[Dict[str, Any]], logger: logging.Logger) -> int:
    """
    批量保存价格记录到数据库
    :return: 成功保存的记录数
    """
    saved = 0
    for record in records:
        try:
            if db.insert_price(record["card_name"], record):
                saved += 1
        except Exception as e:
            logger.error("保存记录失败: %s", str(e))
    return saved


def analyze_changes(db: PriceDatabase, card_name: str, platform: str, threshold: float) -> Dict[str, Any]:
    """
    计算某卡片在某平台的最新价格相对上一次价格的涨跌
    :return: 包含涨跌信息的字典
    """
    latest_records = db.get_latest_n_records(card_name, platform, n=1)
    if not latest_records:
        return None

    latest = latest_records[0]
    previous = db.get_previous_record(card_name, platform, latest["date"])

    latest_price = latest["price"]
    previous_price = previous["price"] if previous else latest_price

    change = calculate_change(latest_price, previous_price)
    is_abnormal = abs(change["change_percent"]) >= threshold

    return {
        "card_name": card_name,
        "platform": platform,
        "latest_price": latest_price,
        "latest_currency": latest["currency"],
        "previous_price": previous_price,
        "previous_currency": previous["currency"] if previous else latest["currency"],
        "change_amount": change["change_amount"],
        "change_percent": change["change_percent"],
        "is_abnormal": is_abnormal,
        "date": latest["date"],
        "url": latest["url"],
        "title": latest["title"],
    }


def run_monitor(config: Dict[str, Any], test_mode: bool = False, target_card: str = None):
    """
    执行完整监控流程
    :param config: 配置字典
    :param test_mode: 是否为测试模式（只抓取不推送）
    :param target_card: 指定卡片名称（可选）
    """
    logger = logging.getLogger("main")
    db_path = config.get("database_path", "data/prices.db")

    # 初始化数据库
    with PriceDatabase(db_path) as db:
        cards = filter_cards(config.get("cards", []), target_card)
        threshold = config.get("thresholds", {}).get("daily_change", 0.15)

        all_analysis = []
        total_platforms = set()

        for card in cards:
            card_name = card["name"]
            platforms = card.get("platforms", [])
            logger.info("开始处理卡片: %s, 平台: %s", card_name, platforms)

            for platform in platforms:
                if platform not in PLATFORM_MAP:
                    logger.warning("未知平台: %s，已跳过", platform)
                    continue

                total_platforms.add(platform)
                scraper_class = PLATFORM_MAP[platform]
                scraper = scraper_class()

                # 确定最低价格阈值
                currency = "CNY" if platform == "cardhobby" else "USD"
                min_price = get_min_price_threshold(config, currency)

                # 抓取数据
                records = scrape_card(card, scraper, min_price, logger)

                # 保存到数据库
                saved_count = save_records(db, records, logger)
                logger.info(
                    "卡片 [%s] 平台 [%s] 抓取 %d 条，保存 %d 条",
                    card_name, platform, len(records), saved_count
                )

                # 计算涨跌
                analysis = analyze_changes(db, card_name, platform, threshold)
                if analysis:
                    all_analysis.append(analysis)
                    logger.info(
                        "涨跌分析: %s | %s | %.2f | 变化 %.2f%% | 异常: %s",
                        card_name, platform,
                        analysis["latest_price"],
                        analysis["change_percent"] * 100,
                        analysis["is_abnormal"]
                    )

        # 汇总日报数据
        abnormal_items = [item for item in all_analysis if item.get("is_abnormal")]
        daily_report = {
            "date": get_today_shanghai(),
            "summary": {
                "total_cards": len(cards),
                "total_platforms": len(total_platforms),
                "abnormal_count": len(abnormal_items),
            },
            "details": all_analysis,
            "abnormal_items": abnormal_items,
            "market_comment": generate_market_comment(all_analysis),
        }

        # 推送
        if not test_mode:
            send_notifications(config, daily_report, abnormal_items, logger)
        else:
            logger.info("测试模式：跳过推送")

        logger.info("监控流程执行完成")


def generate_market_comment(all_analysis: List[Dict[str, Any]]) -> str:
    """
    根据分析结果生成简单的市场简评
    """
    if not all_analysis:
        return "今日无新成交数据，市场暂无更新。"

    abnormal_count = sum(1 for item in all_analysis if item.get("is_abnormal"))
    up_count = sum(1 for item in all_analysis if item.get("change_percent", 0) > 0)
    down_count = sum(1 for item in all_analysis if item.get("change_percent", 0) < 0)

    comment = f"今日共更新 {len(all_analysis)} 条价格记录。"
    if abnormal_count > 0:
        comment += f" 其中 {abnormal_count} 条出现异常波动，建议重点关注。"
    elif up_count > down_count:
        comment += " 整体呈现小幅上涨趋势，市场活跃度良好。"
    elif down_count > up_count:
        comment += " 整体呈现小幅下跌趋势，建议观望。"
    else:
        comment += " 市场整体平稳，涨跌互现。"

    return comment


def send_notifications(config: Dict[str, Any], daily_report: Dict[str, Any], abnormal_items: List[Dict[str, Any]], logger: logging.Logger):
    """
    根据配置发送日报和异常提醒
    """
    feishu_config = config.get("notifications", {}).get("feishu", {})
    webhook_url = feishu_config.get("webhook_url")
    secret = feishu_config.get("secret")

    if not webhook_url:
        logger.warning("未配置飞书 Webhook，跳过推送")
        return

    alert = FeishuAlert(webhook_url, secret)

    # 发送每日日报
    if feishu_config.get("enable_daily", True):
        try:
            alert.send_daily_report(daily_report)
        except Exception as e:
            logger.error("发送日报失败: %s", str(e))

    # 发送异常提醒
    if feishu_config.get("enable_alert", True):
        for item in abnormal_items:
            try:
                alert.send_alert(item)
            except Exception as e:
                logger.error("发送异常提醒失败 [%s]: %s", item.get("card_name", ""), str(e))


def main():
    """
    命令行入口
    """
    parser = argparse.ArgumentParser(description="NBA 球星卡价格监控系统")
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="配置文件路径（默认 config.yaml）"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="测试模式：只抓取不推送"
    )
    parser.add_argument(
        "--card",
        type=str,
        default=None,
        help="只监控指定卡片（名称匹配）"
    )
    args = parser.parse_args()

    # 设置日志
    logger = setup_logger("main", log_file="logs/monitor.log", level=logging.INFO)
    logger.info("=" * 50)
    logger.info("球星卡价格监控系统启动")

    try:
        config = load_config(args.config)
        run_monitor(config, test_mode=args.test, target_card=args.card)
    except Exception as e:
        logger.exception("监控运行失败: %s", str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()

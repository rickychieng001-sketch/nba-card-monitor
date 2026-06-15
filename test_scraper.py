"""
测试脚本：单独测试某个平台的爬虫
用法：
    python test_scraper.py --platform ebay --keyword "Wembanyama Silver Prizm PSA 10"
    python test_scraper.py --platform cardhobby --keyword "文班亚马 银折 PSA10"
"""

import argparse
import json
import logging

from scrapers.cardhobby import CardHobbyScraper
from scrapers.ebay import EbayScraper
from scrapers.goldin import GoldinScraper
from scrapers.pwcc import PwccScraper
from utils.helpers import setup_logger

# 平台名称到爬虫类的映射
PLATFORM_MAP = {
    "cardhobby": CardHobbyScraper,
    "ebay": EbayScraper,
    "goldin": GoldinScraper,
    "pwcc": PwccScraper,
}


def main():
    """
    命令行入口
    """
    parser = argparse.ArgumentParser(description="测试单个平台爬虫")
    parser.add_argument(
        "--platform",
        type=str,
        required=True,
        choices=list(PLATFORM_MAP.keys()),
        help="要测试的平台: cardhobby, ebay, goldin, pwcc"
    )
    parser.add_argument(
        "--keyword",
        type=str,
        required=True,
        help="搜索关键词"
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="最大抓取页数（默认 1）"
    )
    args = parser.parse_args()

    logger = setup_logger("test_scraper", level=logging.DEBUG)
    logger.info("开始测试平台: %s, 关键词: %s", args.platform, args.keyword)

    scraper_class = PLATFORM_MAP[args.platform]
    scraper = scraper_class(max_pages=args.pages)

    try:
        results = scraper.search(args.keyword)
        logger.info("抓取完成，共 %d 条结果", len(results))

        if results:
            print("\n========== 抓取结果 ==========")
            print(json.dumps(results, ensure_ascii=False, indent=2))
            print("==============================\n")
        else:
            print("未抓取到任何结果，请检查关键词或页面结构。")

    except Exception as e:
        logger.exception("测试抓取失败: %s", str(e))


if __name__ == "__main__":
    main()

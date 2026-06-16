"""
Playwright 渲染抓取工具
用于处理需要 JavaScript 渲染或强反爬的页面
作为 requests 抓取失败后的降级方案
"""

import logging
from typing import Optional

logger = logging.getLogger("utils.playwright_fetcher")


def fetch_with_browser(url: str, wait_seconds: int = 5) -> Optional[str]:
    """
    使用 Playwright 启动 headless Chromium 渲染页面并返回 HTML
    :param url: 目标 URL
    :param wait_seconds: 页面加载后额外等待时间（秒）
    :return: 页面 HTML 或 None
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("未安装 playwright，跳过浏览器渲染")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            page = context.new_page()

            # 隐藏 webdriver 标记
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
            """)

            logger.info("Playwright 正在渲染: %s", url)
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(wait_seconds * 1000)
            html = page.content()

            context.close()
            browser.close()
            logger.info("Playwright 渲染完成，HTML 长度: %d", len(html))
            return html

    except Exception as e:
        logger.error("Playwright 渲染失败: %s", str(e))
        return None

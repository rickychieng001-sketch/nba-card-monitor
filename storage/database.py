"""
SQLite 数据存储模块
负责球星卡价格历史数据的持久化、去重与查询
"""

import logging
import os
import sqlite3
from typing import List, Optional, Dict, Any

logger = logging.getLogger("storage.database")


class PriceDatabase:
    """
    价格数据库管理类
    使用 SQLite 存储卡片信息与历史成交价格
    """

    def __init__(self, db_path: str = "data/prices.db"):
        """
        初始化数据库连接，并自动创建表
        :param db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info("数据库初始化完成: %s", db_path)

    def _create_tables(self):
        """
        首次运行时自动创建 cards 和 prices 表
        """
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                title TEXT,
                price REAL NOT NULL,
                currency TEXT NOT NULL,
                date TEXT NOT NULL,
                url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (card_id) REFERENCES cards(id)
            )
        """)

        # 创建联合索引，用于去重和快速查询
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_prices_card_platform_date
            ON prices(card_id, platform, date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_prices_created_at
            ON prices(created_at)
        """)

        self.conn.commit()

    def get_or_create_card(self, name: str) -> int:
        """
        根据卡片名称获取 ID，不存在则创建
        :param name: 卡片标准名称
        :return: 卡片 ID
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM cards WHERE name = ?", (name,))
        row = cursor.fetchone()

        if row:
            return row["id"]

        cursor.execute("INSERT INTO cards (name) VALUES (?)", (name,))
        self.conn.commit()
        logger.info("创建新卡片记录: %s", name)
        return cursor.lastrowid

    def insert_price(self, card_name: str, record: Dict[str, Any]) -> bool:
        """
        插入一条价格记录，若同卡片+平台+日期已存在则跳过（去重）
        :param card_name: 卡片标准名称
        :param record: 标准格式价格记录
        :return: 是否成功插入
        """
        card_id = self.get_or_create_card(card_name)
        platform = record.get("platform", "")
        date = record.get("date", "")

        # 检查是否已存在相同记录
        if self._exists(card_id, platform, date):
            logger.debug(
                "记录已存在，跳过: card_id=%s, platform=%s, date=%s",
                card_id, platform, date
            )
            return False

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO prices (card_id, platform, title, price, currency, date, url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            card_id,
            platform,
            record.get("title", ""),
            record.get("price", 0.0),
            record.get("currency", "USD"),
            date,
            record.get("url", ""),
        ))
        self.conn.commit()
        logger.info(
            "插入价格记录: %s | %s | %s | %.2f %s",
            card_name, platform, date, record.get("price", 0.0), record.get("currency", "USD")
        )
        return True

    def _exists(self, card_id: int, platform: str, date: str) -> bool:
        """
        检查指定卡片、平台、日期的记录是否已存在
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT 1 FROM prices
            WHERE card_id = ? AND platform = ? AND date = ?
            LIMIT 1
        """, (card_id, platform, date))
        return cursor.fetchone() is not None

    def get_latest_n_records(
        self,
        card_name: str,
        platform: str,
        n: int = 1
    ) -> List[Dict[str, Any]]:
        """
        获取某卡片某平台的最新 N 条记录
        :param card_name: 卡片标准名称
        :param platform: 平台名称
        :param n: 记录数量
        :return: 价格记录列表（按日期倒序）
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT p.*, c.name as card_name
            FROM prices p
            JOIN cards c ON p.card_id = c.id
            WHERE c.name = ? AND p.platform = ?
            ORDER BY p.date DESC, p.created_at DESC
            LIMIT ?
        """, (card_name, platform, n))

        rows = cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_previous_record(
        self,
        card_name: str,
        platform: str,
        current_date: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取某卡片某平台上一次（早于当前日期）的记录，用于计算涨跌
        :param card_name: 卡片标准名称
        :param platform: 平台名称
        :param current_date: 当前记录日期（YYYY-MM-DD）
        :return: 上一条记录或 None
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT p.*, c.name as card_name
            FROM prices p
            JOIN cards c ON p.card_id = c.id
            WHERE c.name = ? AND p.platform = ? AND p.date < ?
            ORDER BY p.date DESC, p.created_at DESC
            LIMIT 1
        """, (card_name, platform, current_date))

        row = cursor.fetchone()
        return self._row_to_dict(row) if row else None

    def get_all_cards(self) -> List[Dict[str, Any]]:
        """
        获取所有卡片列表
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM cards ORDER BY created_at DESC")
        rows = cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_distinct_platforms(self, card_name: str) -> List[str]:
        """
        获取某卡片所有有记录的平台
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT DISTINCT p.platform
            FROM prices p
            JOIN cards c ON p.card_id = c.id
            WHERE c.name = ?
            ORDER BY p.platform
        """, (card_name,))
        return [row["platform"] for row in cursor.fetchall()]

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """
        将 sqlite3.Row 转换为字典
        """
        return {key: row[key] for key in row.keys()}

    def close(self):
        """
        关闭数据库连接
        """
        if self.conn:
            self.conn.close()
            logger.info("数据库连接已关闭")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

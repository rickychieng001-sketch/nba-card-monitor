# 🏀 NBA 球星卡价格监控系统 - 部署指南

本项目每日自动抓取卡淘 / eBay / Goldin / PWCC 球星卡成交数据，对比历史价格计算涨跌，异常波动时推送飞书消息，并生成可读的监控日报。

---

## 1. Fork 本仓库

点击 GitHub 页面右上角的 **Fork** 按钮，将本仓库复制到你的 GitHub 账号下。

---

## 2. 配置飞书机器人

1. 打开目标飞书群 → 点击右上角 **设置**（齿轮图标）
2. 选择 **群机器人** → **添加机器人**
3. 选择 **自定义机器人**，按提示完成创建
4. 复制生成的 **Webhook 地址**（格式类似 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx`）

> ⚠️ 请妥善保管 Webhook 地址，不要泄露到公共代码或日志中。

---

## 3. 配置 GitHub Secrets

1. 进入你 Fork 后的仓库页面
2. 点击 **Settings** → **Secrets and variables** → **Actions**
3. 点击 **New repository secret**
4. 添加以下 Secret：

| 名称 | 值 | 说明 |
|------|-----|------|
| `FEISHU_WEBHOOK` | 你从飞书复制的 Webhook 地址 | 必填 |
| `FEISHU_SECRET` | 飞书机器人签名密钥 | 若机器人开启了签名校验则必填 |
| `EBAY_APP_ID` | eBay Finding API App ID | 推荐，可稳定获取 eBay 成交数据 |

---

## 4. 修改 config.yaml

编辑仓库根目录下的 `config.yaml`，添加你要监控的卡片：

```yaml
cards:
  - name: "2023-24 Panini Prizm Victor Wembanyama Silver Prizm PSA 10"
    aliases: ["文班亚马 银折 PSA10", "Wembanyama Silver Prizm"]
    platforms: ["cardhobby", "ebay"]
  - name: "2018-19 Panini National Treasures Luka Doncic RPA /99"
    aliases: ["东契奇 RPA", "Doncic RPA NT"]
    platforms: ["goldin", "pwcc", "ebay"]

thresholds:
  daily_change: 0.15           # 异常阈值：单日涨跌超过 15%
  min_price_usd: 10
  min_price_cny: 100

platform_keys:
  ebay_app_id: ""             # eBay Finding API App ID（免费申请，见下文）

notifications:
  feishu:
    webhook_url: ""             # 留空即可，优先读取环境变量 FEISHU_WEBHOOK
    secret: ""                  # 飞书签名密钥，优先读取环境变量 FEISHU_SECRET
    enable_daily: true
    enable_alert: true
    daily_time: "09:00"
```

### 配置说明

- `name`：卡片的标准名称，用于数据库和报表展示
- `aliases`：搜索别名，提高匹配率
- `platforms`：要监控的平台，可选 `cardhobby`、`ebay`、`goldin`、`pwcc`
- `thresholds.daily_change`：涨跌超过该比例会触发异常提醒
- `min_price_usd` / `min_price_cny`：过滤低价 noise 数据
- `platform_keys.ebay_app_id`：eBay 官方 API 密钥，最稳定的数据来源

---

## 5. 手动触发测试

1. 进入仓库的 **Actions** 页面
2. 选择 **🏀 NBA Card Monitor** 工作流
3. 点击 **Run workflow** → **Run workflow**
4. 等待运行完成后，查看飞书群是否收到日报消息

---

## 6. 本地测试（可选）

如果你想在本地运行或调试：

### 6.1 安装依赖

```bash
cd card-monitor
python -m venv venv
source venv/bin/activate  # Windows 使用 venv\Scripts\activate
pip install -r requirements.txt
```

### 6.2 配置环境变量

```bash
export FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx"
```

### 6.3 运行主程序

```bash
# 正常运行
python main.py

# 测试模式（只抓取不推送）
python main.py --test

# 只监控某一张卡片
python main.py --card "Wembanyama"
```

### 6.4 单独测试某个平台爬虫

```bash
python test_scraper.py --platform ebay --keyword "Wembanyama Silver Prizm PSA 10"
python test_scraper.py --platform cardhobby --keyword "文班亚马 银折 PSA10" --pages 2
```

---

## 7. 查看运行结果

### 7.1 GitHub Actions 日志

- 进入 **Actions** 页面
- 点击某次运行记录
- 查看 `Run monitor` 步骤的输出日志

### 7.2 飞书群消息

- 每日北京时间 09:00 自动发送日报
- 异常波动时即时推送提醒

### 7.3 数据库查看

每次运行后，GitHub Actions 会自动上传以下 Artifacts：

- `price-database`：包含 `data/prices.db`
- `monitor-logs`：包含 `logs/monitor.log`

下载 `prices.db` 后，可使用以下工具查看：

- [DB Browser for SQLite](https://sqlitebrowser.org/)
- 命令行：`sqlite3 prices.db "SELECT * FROM prices ORDER BY date DESC LIMIT 20;"`

---

## 8. 安全注意事项

1. **不要将 `FEISHU_WEBHOOK` 写入代码或 config.yaml 后提交**
2. 代码中已做 URL 敏感参数脱敏处理
3. 请求频率已限制（单平台每秒不超过 1 次）
4. 单个平台失败不会影响其他平台运行

---

## 9. 常见问题

### Q: 飞书没有收到消息？

A: 请检查：
1. GitHub Secret 是否正确配置
2. 工作流是否成功运行
3. `enable_daily` 是否设为 `true`
4. 飞书机器人是否被禁言或移除

### Q: 某个平台抓不到数据？

A: 这些网站结构可能变化，或启用反爬/JS 渲染。可：
1. 使用 `test_scraper.py` 单独测试
2. 调整 `max_pages` 参数
3. 检查页面是否需要登录或 Cookie
4. 查看 `logs/monitor.log` 中的错误信息

### Q: 如何修改定时时间？

A: 编辑 `.github/workflows/monitor.yml` 中的 `schedule.cron`。注意 GitHub Actions 使用 UTC 时间，北京时间 = UTC+8。

例如：
- 北京时间 09:00 → UTC 01:00 → `0 1 * * *`
- 北京时间 21:00 → UTC 13:00 → `0 13 * * *`

---

## 10. 关于反爬与 eBay API（重要）

### 反爬说明

卡淘、eBay、Goldin、PWCC 均设有反爬机制，直接使用 HTTP 请求容易被拦截。当前代码做了以下处理：

1. 随机 User-Agent + 请求频率控制
2. 卡淘搜索 URL 已修正为 `/market/search?keyword=`
3. eBay 支持官方 Finding API（最稳定）
4. 所有平台在 HTTP 请求失败后，会自动尝试 Playwright 浏览器渲染

### 申请 eBay App ID（免费）

eBay 官方 API 是获取 sold 数据最稳定、最合规的方式：

1. 访问 https://developer.ebay.com/
2. 注册/登录 eBay 开发者账号
3. 创建 Application Key Set
4. 获取 **App ID (Client ID)**
5. 配置方式三选一：
   - 写入 `config.yaml` 的 `platform_keys.ebay_app_id`
   - 设置为 GitHub Secret：`EBAY_APP_ID`
   - 本地运行时：`export EBAY_APP_ID=你的AppID`

> 只要配置了 `EBAY_APP_ID`，eBay 爬虫会自动使用官方 API，不再走 HTML 解析。

---

## 11. 项目结构

```
card-monitor/
├── config.yaml              # 用户配置文件
├── main.py                  # 主入口
├── scrapers/                # 各平台爬虫
├── storage/                 # SQLite 数据库
├── alerts/                  # 飞书推送
├── utils/                   # 工具函数
├── test_scraper.py          # 爬虫测试脚本
├── requirements.txt         # Python 依赖
├── .github/workflows/       # GitHub Actions
└── setup.md                 # 本文件
```

---

如有问题，请查看 `logs/monitor.log` 或通过 GitHub Issues 反馈。

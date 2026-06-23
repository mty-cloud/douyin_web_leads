# douyin_web_leads

**抖音网页版客户线索识别与分层系统**

一个基于 **Playwright + SQLite + Streamlit + 智谱 AI** 的工具，帮助女装批发店主通过抖音网页版自动搜索潜在客户、采集主页信息、AI 分析分层，最终导出 Excel 线索列表。

---

## 适用人群

我是二级女装批发店老板，主营 **35–60 岁女性服装**（打底衫、内搭、妈妈装、针织衫、秋冬基础款等）。

我想找到：
- 县城女装实体店老板
- 下沉市场服装店主
- 妈妈装/中老年女装店主
- 经常上新、需要补货的服装零售老板

---

## 功能概览

1. **地区管理** — 管理搜索的目标省市县
2. **关键词管理** — 自定义搜索关键词模板（支持 {county} 变量替换）
3. **任务生成** — 根据地区和关键词自动生成搜索任务
4. **自动搜索** — Playwright 控制抖音网页版搜索、切用户 tab、滚动提取
5. **候选用户采集** — 提取用户昵称、抖音号、主页链接
6. **主页采集** — 新标签页打开用户主页，提取简介/粉丝/作品等信息
7. **规则打分** — 根据服装行业关键词做多维评分（地区/行业/年龄/品类/实体/可信）
8. **智谱 AI 分析** — 可选的 AI 复核（使用智谱 AI API）
9. **A/B/C/D 分层** — 综合规则+AI 输出分层结果
10. **人工审核** — Streamlit 后台审核、修改分层、添加备注
11. **Excel 导出** — 按地区/分层/审核状态导出

---

## 第一次使用

### 1. 安装 Python

需要 Python 3.10 以上版本。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

安装 Playwright 浏览器：

```bash
playwright install
```

### 3. 配置智谱 AI API Key（可选）

```bash
cp .env.example .env
```

编辑 `.env`，填入你的智谱 API Key：

```
ZHIPU_API_KEY=你的智谱APIKey
```

如果没有 API Key，系统会自动只用规则打分，AI 分析功能跳过。

### 4. 启动后台

```bash
streamlit run dashboard/app.py
```

### 5. 初始化配置

在浏览器中打开后台（默认 http://localhost:8501），按顺序：

1. **首页仪表盘** → 点击「初始化数据库」
2. **地区管理** → 上传 `config/regions.csv` 或手动添加地区
3. **关键词管理** → 导入 `config/keyword_templates.yaml` 或手动添加关键词
4. **任务生成** → 选择地区和模板，生成搜索任务
5. **搜索采集** → 点击「打开自动化浏览器」，在弹出的 Chrome 中**手动登录抖音网页版**
6. 登录成功后，回到后台运行搜索任务

### 6. 日常使用流程

```
打开后台 → 检查地区和关键词 → 生成搜索任务
→ 运行搜索采集 → 采集主页 → 线索分层
→ 人工审核 A/B 类 → 导出 Excel
```

---

## 什么时候需要人工介入

1. **第一次登录** — 在打开的浏览器中手动扫码登录抖音
2. **登录失效** — 系统检测到登录页时会暂停
3. **出现验证码/安全验证** — 系统会暂停，需要你手动在浏览器中处理
4. **页面结构变化** — 抖音改版可能导致自动操作失败
5. **个别主页打不开** — 系统会自动跳过，不影响整体流程
6. **审核 A/B 类客户质量** — AI 判断仅供参考，最终决定权在你

---

## 为什么不用底层接口

本项目严格限定在**浏览器可见页面**范围内操作：

- 不抓包、不逆向、不研究签名
- 不绕过验证码
- 不调用未公开 API
- 不硬编码 Cookie
- 不批量高频请求
- 搜索结果页保留截图和 HTML 快照
- 用户主页新标签页打开，降低页面回退风险

遇到登录、验证码、安全验证等情况，系统会暂停并提示人工处理。

---

## 项目结构

```
douyin_web_leads/
  README.md
  requirements.txt
  .env.example
  .gitignore

  config/
    app_config.yaml          # 主配置（浏览器/搜索/OCR/AI）
    scoring_rules.yaml       # 规则打分配置
    regions.csv              # 地区数据
    keyword_templates.yaml   # 关键词模板

  data/
    leads.sqlite             # SQLite 数据库
    browser_profile/         # 浏览器持久化配置
    screenshots/             # 页面截图
    html_snapshots/          # HTML 快照
    exports/                 # 导出的 Excel
    logs/                    # 运行日志

  src/                       # 核心代码
    main.py                  # CLI 入口
    db.py                    # 数据库操作
    models.py                # 数据模型
    settings.py              # 配置管理
    logger.py                # 日志配置
    task_generator.py        # 任务生成
    exporter.py              # Excel 导出

    browser/                 # 浏览器自动化
      browser_manager.py     # 浏览器管理器
      page_state.py          # 页面状态检测
      actions.py             # 页面操作
      dom_extractor.py       # DOM 信息提取
      search_flow.py         # 搜索流程
      profile_flow.py        # 主页采集流程
      screenshot_ocr.py      # 截图 OCR
      recovery.py            # 错误恢复

    analysis/                # 分析模块
      rule_scorer.py         # 规则打分
      zhipu_ai_analyzer.py   # 智谱 AI 分析
      lead_tier.py           # 线索分层
      text_cleaner.py        # 文本清洗

  dashboard/                # Streamlit 后台
    app.py
    pages/
      1_首页仪表盘.py
      2_地区管理.py
      3_关键词管理.py
      4_任务生成.py
      5_搜索采集.py
      6_候选用户.py
      7_主页采集.py
      8_线索分层.py
      9_人工审核与导出.py
      10_系统日志.py

  tests/                    # 测试
    test_rule_scorer.py
    test_lead_tier.py
    test_text_cleaner.py
    test_keyword_manager.py
    test_page_state.py
```

---

## CLI 命令

```bash
# 初始化数据库
python src/main.py init-db

# 导入地区和关键词
python src/main.py import-regions
python src/main.py import-keywords

# 生成搜索任务
python src/main.py generate-tasks

# 打开浏览器（手动登录）
python src/main.py open-browser

# 运行搜索
python src/main.py run-search --limit 10

# 采集主页
python src/main.py collect-profiles --limit 100

# 评分分层
python src/main.py score-leads

# 导出
python src/main.py export --tier A
python src/main.py export --tier AB
python src/main.py export --all
```

---

## 运行测试

```bash
pytest tests/ -v
```

---

## 分层标准

| 分层 | 分数 | 说明 |
|------|------|------|
| A | ≥85 | 强匹配，优先人工查看 |
| B | 70–84 | 相关但需人工复核 |
| C | 50–69 | 弱相关，暂存 |
| D | <50 | 不匹配，排除 |

---

## 技术栈

- Python 3.10+
- Playwright（浏览器自动化）
- SQLite（数据存储）
- Streamlit（后台界面）
- pandas + openpyxl（Excel 导出）
- 智谱 AI（可选 AI 分析）
- PaddleOCR（可选 OCR 兜底）
- loguru（日志）
- pytest（测试）

---

## 许可证

仅供学习和个人业务使用。

"""DOM 提取器 — 从页面中提取用户卡片和主页信息。

增强功能：
- 主页信息完整提取（点击「更多」展开后）
- 多策略提取昵称、抖音号、简介、粉丝等
- 搜索页用户卡片批量提取
"""

import re

from loguru import logger


class DOMExtractor:
    """从抖音网页版 DOM 中提取信息。"""

    # ------------------------------------------------------------------
    # 搜索页用户卡片提取
    # ------------------------------------------------------------------

    def extract_visible_user_cards(self, page) -> list[dict]:
        """从搜索结果页提取可见用户卡片。"""
        results = []

        try:
            links = page.locator("a").all()
        except Exception as e:
            logger.warning("获取页面链接失败: {}", e)
            return results

        seen = set()

        for link in links:
            try:
                href = link.get_attribute("href")
                text = link.inner_text(timeout=1000)

                if not href:
                    continue

                if href.startswith("//"):
                    href = "https:" + href
                elif "douyin.com" not in href and href.startswith("/"):
                    href = "https://www.douyin.com" + href

                # 去掉 query params（追踪参数导致同一用户 URL 每次都不同，无法去重）
                q_idx = href.find("?")
                if q_idx >= 0:
                    href = href[:q_idx]
                href = href.rstrip("/")

                if not self._looks_like_profile_url(href):
                    continue

                # 获取更完整的卡片文本
                nearby_text = text.strip()
                if not nearby_text:
                    try:
                        parent = link.locator("xpath=..")
                        nearby_text = parent.inner_text(timeout=1000)
                    except Exception:
                        nearby_text = ""

                # 尝试获取更外层容器的文本
                if not nearby_text or len(nearby_text) < 10:
                    try:
                        grandparent = link.locator("xpath=../..")
                        gp_text = grandparent.inner_text(timeout=1000)
                        if len(gp_text) > len(nearby_text):
                            nearby_text = gp_text
                    except Exception:
                        pass

                if not nearby_text:
                    continue

                # 去重
                dedupe = href
                if dedupe in seen:
                    continue
                seen.add(dedupe)

                nickname = self._guess_nickname(nearby_text)
                douyin_id = self._guess_douyin_id(nearby_text)
                # 从 URL 提取 sec_uid 作为兜底唯一标识（搜索卡通常不显示抖音号）
                sec_uid = self.extract_sec_uid_from_url(href)

                results.append({
                    "nickname": nickname,
                    "douyin_id": douyin_id or sec_uid,  # 有真实抖音号则用，没有则用 URL 里的 sec_uid
                    "sec_uid": sec_uid,
                    "profile_url": href,
                    "search_card_text": nearby_text[:2000],
                })

            except Exception:
                continue

        logger.info("DOM 提取到 {} 个用户卡片", len(results))
        return results

    # ------------------------------------------------------------------
    # 主页信息提取（增强版）
    # ------------------------------------------------------------------

    def extract_profile_info(self, page) -> dict:
        """从用户主页提取信息（包含展开后的内容），过滤掉页面框架杂音。

        核心策略：基于 body.innerText 行过滤，定位"抖音号"行取附近内容。
        """
        info = {}
        profile_text = self._extract_clean_profile_text(page)
        info["profile_text"] = profile_text[:5000]
        full_text = profile_text[:10000]

        # 提取各项信息
        info["nickname"] = self._guess_nickname(full_text)
        # 策略A：JS 直接获取抖音号 DOM 元素（最可靠）
        info["douyin_id"] = self._extract_douyin_id_via_js(page) or self._guess_douyin_id(full_text)
        # 从当前页面 URL 提取 sec_uid 作为兜底唯一标识
        try:
            page_url = page.url
            info["sec_uid"] = self.extract_sec_uid_from_url(page_url)
        except Exception:
            info["sec_uid"] = ""
        info["profile_bio"] = self._guess_bio(full_text)
        info["followers_text"] = self._extract_metric(full_text, "粉丝")
        info["following_text"] = self._extract_metric(full_text, "关注")
        info["likes_text"] = self._extract_metric(full_text, "获赞")
        info["works_text"] = self._extract_metric(full_text, "作品")
        info["region_text"] = self._guess_region(full_text)

        info["tags"] = self._guess_tags(full_text)
        info["gender_hint"] = self._guess_gender(full_text)
        info["age_hint"] = self._guess_age(full_text)

        return info

    def _extract_clean_profile_text(self, page) -> str:
        """从页面 body.innerText 中提取干净的用户主页文本。

        策略：定位"抖音号"所在行，取附近上下文区域（包含昵称、指标、完整简介），
        过滤掉页脚版权、导航栏等框架噪音。
        """
        try:
            raw = page.evaluate("() => document.body.innerText")
        except Exception as e:
            logger.warning("提取 body.innerText 失败: {}", e)
            return ""

        lines = raw.split("\n")

        # 抖音年份版权行（动态匹配任意年份）
        _copyr_pat = re.compile(r"20\d{2}\s*©\s*抖音")
        # 噪音行 — 单个词导航/版权信息
        nav_words = {
            "开启读屏标签", "读屏标签已关闭", "搜索", "下载抖音精选",
            "充钻石", "客户端", "壁纸", "通知", "私信", "投稿",
            "精选", "推荐", "关注", "朋友", "我的", "直播", "放映厅", "短剧",
            "分享主页", "下载", "筛选",
        }
        footer_kw = ["京ICP备", "京公网安", "广播电视", "增值电信", "网络文化",
                     "互联网宗教", "药品医疗器械", "互联网新闻",
                     "广告投放", "用户服务协议", "隐私政策", "账号找回",
                     "营业执照", "站点地图", "下载抖音", "抖音电商",
                     "举报", "投诉", "反馈"]

        # 定位"抖音号"行
        douyin_idx = -1
        for i, ln in enumerate(lines):
            if "抖音号" in ln:
                douyin_idx = i
                break

        if douyin_idx < 0:
            return ""

        # 从抖音号往上取约 8 行（覆盖昵称、关注、粉丝、获赞等）
        start = max(0, douyin_idx - 8)

        # 往下取到页脚前（简介完整内容）
        end = len(lines)
        for i in range(douyin_idx, len(lines)):
            stripped = lines[i].strip()
            if any(kw in stripped for kw in footer_kw):
                end = i
                break

        # 提取干净行
        clean = []
        for ln in lines[start:end]:
            stripped = ln.strip()
            if not stripped:
                continue
            if stripped in nav_words or _copyr_pat.search(stripped):
                continue
            clean.append(stripped)

        return "\n".join(clean)[:5000]

    # ------------------------------------------------------------------
    # 识别匹配：判断用户是否符合目标画像
    # ------------------------------------------------------------------

    def match_target_profile(self, profile_info: dict, target_keywords: list[str] = None) -> dict:
        """基于主页信息判断用户是否匹配目标行业。

        Returns:
            dict with keys: matched (bool), matched_keywords (list), score (int), evidence (str)
        """
        if target_keywords is None:
            target_keywords = [
                "女装", "服装", "实体店", "店主", "穿搭", "工作室",
                "批发", "零售", "打版", "定制", "女装店", "妈妈装",
                "杭州女装", "广州女装", "深圳女装", "北京女装",
            ]

        text = " ".join([
            profile_info.get("profile_bio", ""),
            profile_info.get("profile_text", ""),
            profile_info.get("tags", ""),
        ]).lower()

        matched = []
        for kw in target_keywords:
            if kw.lower() in text:
                matched.append(kw)

        score = len(matched) * 20
        if profile_info.get("followers_text"):
            # 粉丝量超过1000加10分
            score += 10

        return {
            "matched": len(matched) > 0,
            "matched_keywords": matched,
            "score": min(score, 100),
            "evidence": "; ".join(matched[:5]) if matched else "未匹配到目标关键词",
        }

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    def _looks_like_profile_url(self, href: str) -> bool:
        if not href:
            return False
        patterns = ["/user/", "douyin.com/user/"]
        return any(p in href for p in patterns)

    # 常见噪音文本 — 读屏/无障碍/框架文字，不是真实昵称
    _NICKNAME_NOISE = {
        "开启读屏标签", "读屏标签", "读屏标签已关闭", "下载抖音精选",
        "京ICP备", "京公网安",
        "认证徽章",
    }

    def _is_noise_nickname(self, name: str) -> bool:
        """判断提取出来的昵称是否为噪音（非真实用户名）。"""
        if not name or len(name) < 2:
            return True
        for noise in self._NICKNAME_NOISE:
            if noise in name:
                return True
        # 抖音年份版权行（动态匹配任意年份）
        if re.search(r"20\d{2}\s*©\s*抖音", name):
            return True
        # 纯数字/符号的昵称也不可信
        alpha_count = sum(1 for c in name if c.isalpha())
        if alpha_count == 0 and len(name) > 1:
            return True
        return False

    def _guess_nickname(self, text: str) -> str:
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        if not lines:
            return ""

        # 策略1: 找 "抖音号" 前面的行（昵称通常在抖音号上方）
        for i, line in enumerate(lines):
            if "抖音号" in line and i > 0:
                prev = lines[i - 1]
                skip_words = {"关注", "粉丝", "获赞", "作品", "首页", "推荐", "搜索", "直播", "商品"}
                # 跳过数值行（如 "52.5万"、"329"、"0"）
                if re.match(r'^[\d.]+[万wW]?$', prev):
                    continue
                if not any(w in prev for w in skip_words) and len(prev) < 30:
                    if not self._is_noise_nickname(prev):
                        return prev[:50]

        # 策略2: 跳过标题/噪音行，取第一个短行
        skip_words = {"关注", "粉丝", "获赞", "作品", "抖音", "首页", "推荐",
                      "搜索", "直播", "商品"}
        skip_words.update(self._NICKNAME_NOISE)
        for line in lines:
            if not any(w in line for w in skip_words) and len(line) < 30:
                return line[:50]

        # 策略3: 取第一个超过1个字符的非噪音行
        for line in lines:
            if len(line) > 1 and not self._is_noise_nickname(line):
                return line[:50]

        return ""

    def _guess_douyin_id(self, text: str) -> str:
        patterns = [
            # 1) 最精确：抖音号: 后跟纯字母数字下划线（最常见格式）
            r"抖音号[:：\s]*([A-Za-z0-9_]+)",
            # 2) 允许包含点、横线
            r"抖音号[:：\s]*([A-Za-z0-9_.-]+)",
            # 3) 兜底：抖音号后面的任何非空白字符串
            r"抖音号[:：\s]*([^\s\n\r，。、,!！?？]+)",
            # 4) ID: 后跟字母数字（部分页面用 "ID:" 标签）
            r"(?:ID|账号)[:：\s]*([A-Za-z0-9_.-]+)",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group(1).strip()
        return ""

    def _extract_douyin_id_via_js(self, page) -> str:
        """通过 JS 直接读取页面中"抖音号"对应的 DOM 元素。

        抖音网页版主页中"抖音号"通常在 profile 信息区，
        格式如 '抖音号: abc123'，ID 值在相邻的文本节点或 span 中。
        """
        try:
            result = page.evaluate("""
                () => {
                    // 策略1：找包含"抖音号"文本的节点，提取紧随其后的 ID
                    const all = document.body.querySelectorAll('*');
                    for (const el of all) {
                        const text = (el.innerText || '').trim();
                        const m = text.match(/^抖音号[:：]\\s*([A-Za-z0-9_.-]+)$/);
                        if (m) return m[1];
                    }

                    // 策略2：找所有文本节点，匹配 "抖音号: xxx" 模式
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );
                    let node;
                    while (node = walker.nextNode()) {
                        const txt = (node.nodeValue || '').trim();
                        const m = txt.match(/抖音号[:：]\\s*([A-Za-z0-9_.-]+)/);
                        if (m) return m[1];
                    }

                    // 策略3：全局扫描所有元素的 innerText
                    for (const el of all) {
                        const text = (el.innerText || '').trim();
                        if (text.includes('抖音号')) {
                            const m = text.match(/抖音号[:：\\s]*([A-Za-z0-9_.-]+)/);
                            if (m) {
                                // 确保匹配的不是整段话里偶然包含的
                                const val = m[1];
                                if (val.length >= 3 && val.length <= 30) return val;
                            }
                        }
                    }

                    return '';
                }
            """)
            if result and len(result) >= 2:
                return result.strip()
        except Exception:
            pass
        return ""

    def extract_sec_uid_from_url(self, url: str) -> str:
        """从用户主页 URL 提取 sec_uid（抖音用户永久唯一标识）。

        格式: https://www.douyin.com/user/<sec_uid>
        """
        if not url:
            return ""
        m = re.search(r"/user/([A-Za-z0-9_-]+)", url)
        return m.group(1) if m else ""

    def _guess_bio(self, text: str) -> str:
        """提取个人简介内容。

        抖音主页布局：昵称行 → 关注/粉丝/获赞行 → 抖音号/IP行 → BIO区域 → 作品/数据行
        提取"抖音号"那行之后的文本，直到遇到作品数/视频标题行。
        """
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        if not lines:
            return ""

        # 找到"抖音号"所在行索引
        dy_index = -1
        for i, line in enumerate(lines):
            if "抖音号" in line:
                dy_index = i
                break

        if dy_index >= 0:
            # 从抖音号下一行开始收集，直到遇到作品/视频相关行
            bio_parts = []
            for line in lines[dy_index + 1:]:
                # 跳过纯 IP 属地行
                if line.startswith("IP属地") or line.startswith("IP 属地"):
                    continue
                # 年龄/性别提示
                if re.match(r'^\d+\s*岁$', line):
                    continue
                # 跳过"更多"展开按钮和"下载客户端"行
                if line in ("更多",) or line.startswith("下载电脑客户端"):
                    continue
                # 遇到作品/视频相关行停止
                if re.match(r'^(作品|推荐|喜欢|Ta 的作品|合集|搜索|\d+\s*)$', line):
                    break
                # 遇到明显的视频标题、话题行停止（带#号或视频时长）
                if line.startswith("#") or re.match(r'^\d+:\d+$', line):
                    break
                # 遇到"关注"按钮文字停止
                if line in ("关注", "私信"):
                    break
                bio_parts.append(line)

            if bio_parts:
                return " ".join(bio_parts)[:500]

        # 兜底 1: 找含行业关键词的行
        industry_words = ["女装", "服装", "实体", "门店", "批发", "零售",
                          "穿搭", "店主", "工作室", "定制", "工厂", "原创",
                          "设计", "品牌", "直营", "加盟", "代理", "主营",
                          "地址", "营业", "电话", "微信"]
        bio_lines = []
        for line in lines[1:8]:
            if any(w in line for w in industry_words):
                bio_lines.append(line)

        if bio_lines:
            return " ".join(bio_lines)[:500]

        # 兜底 2: 前几行中不含数字和已知关键词的行
        skip_words = {"关注", "粉丝", "获赞", "作品", "抖音", "首页", "搜索",
                      "推荐", "直播", "商品", "精选"}
        for line in lines[1:5]:
            if not any(w in line for w in skip_words) and len(line) > 4:
                return line[:500]

        return ""

    def _extract_metric(self, text: str, metric: str) -> str:
        """提取数值指标，如粉丝数、获赞数等。

        支持两种布局：
        - 同行："10.1万粉丝"、"粉丝 3149"
        - 跨行："10.1万\\n粉丝"、"粉丝\\n3149"
        """
        # 预替换：数字行 + 指标名行合并
        text = re.sub(r'([\d.]+[万wW亿]?)\s*\n\s*' + re.escape(metric), r'\1 ' + metric, text)
        text = re.sub(re.escape(metric) + r'\s*\n\s*([\d.]+[万wW亿]?)', metric + r' \1', text)

        # 数字 + 单位 + 指标名
        pattern = rf"([0-9\.]+)[ \t]*(万|w|W|亿)?[ \t]*{metric}"
        m = re.search(pattern, text)
        if m:
            return m.group(0)

        # 指标名 + 数字 + 单位
        pattern2 = rf"{metric}[ \t]*[:：]?[ \t]*([0-9\.]+)[ \t]*(万|w|W|亿)?"
        m = re.search(pattern2, text)
        if m:
            return m.group(0)

        # 宽松匹配
        pattern3 = rf"([0-9\.万wW亿亿]+)[ \t]*{metric}"
        m = re.search(pattern3, text)
        return m.group(0) if m else ""

    def _guess_region(self, text: str) -> str:
        """提取 IP 属地/所在地信息。"""
        for key in ["IP属地", "IP 属地", "地区", "地址", "所在地"]:
            idx = text.find(key)
            if idx >= 0:
                return text[idx:idx + 40]
        return ""

    def _guess_tags(self, text: str) -> str:
        """提取个人标签。"""
        lines = text.splitlines()
        tags = []
        # 找字数短、不含常用关键词的行作为标签
        skip = {"关注", "粉丝", "获赞", "作品", "抖音", "首页", "搜索",
                "推荐", "热点", "直播", "商品", "收藏", "评论", "转发"}
        for line in lines:
            line = line.strip()
            if not line or len(line) > 20:
                continue
            if any(w in line for w in skip):
                continue
            # 看起来像标签：短文本，不含标点符号
            if re.match(r'^[一-鿿\w]+$', line):
                tags.append(line)

        return ", ".join(tags[:8])

    def _guess_gender(self, text: str) -> str:
        """性别提示。"""
        if any(w in text for w in ["她", "女", "女士", "小姐姐"]):
            return "female"
        if any(w in text for w in ["他", "男", "先生", "小哥哥"]):
            return "male"
        return ""

    def _guess_age(self, text: str) -> str:
        """年龄/年代提示。"""
        age_words = {
            "70后": "70s", "80后": "80s", "90后": "90s",
            "00后": "00s", "95后": "95s",
        }
        for word, label in age_words.items():
            if word in text:
                return label
        return ""

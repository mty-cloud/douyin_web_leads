"""
抖音搜索结果诊断工具。

用法: python src/diagnose.py "关键词"
"""

import sys, time, json
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.settings import Settings, load_env
from src.browser.browser_manager import BrowserManager, ensure_chrome_running
from src.browser.actions import DouyinActions
from src.browser.page_state import PageStateDetector


def diagnose(keyword="黄冈市黄梅县女装店"):
    load_env()
    settings = Settings()

    print(f"🔍 诊断关键词: {keyword}")
    print("浏览器会自动打开（不要关闭已有Chrome窗口）")

    ensure_chrome_running(settings)
    bm = BrowserManager(settings)
    page = bm.start()
    import time

    try:
        # === 搜索 ===
        search_url = f"https://www.douyin.com/search/{quote(keyword)}"
        print(f"\n1. 导航到搜索页: {search_url}")
        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        print(f"   当前URL: {page.url}")

        # === 所有 a 标签信息 ===
        print(f"\n2. 页面中所有 <a> 标签概况:")
        total_links = page.evaluate("document.querySelectorAll('a').length")
        print(f"   总数: {total_links}")

        print(f"\n3. 所有含 /user/ 的链接（详细）:")
        details = page.evaluate("""
            () => {
                const results = [];
                const links = document.querySelectorAll('a');
                for (const link of links) {
                    let href = link.getAttribute('href') || '';
                    if (!href.includes('/user/')) continue;

                    if (!href.startsWith('http')) {
                        if (href.startsWith('//')) href = 'https:' + href;
                        else if (href.startsWith('/')) href = 'https://www.douyin.com' + href;
                    }

                    const rect = link.getBoundingClientRect();
                    const y = rect ? Math.round(rect.top) : -1;
                    const w = rect ? Math.round(rect.width) : 0;
                    const h = rect ? Math.round(rect.height) : 0;
                    const visible = rect ? (
                        rect.top >= 0 && rect.left >= 0 &&
                        rect.top <= window.innerHeight &&
                        rect.left <= window.innerWidth
                    ) : false;

                    const text = (link.innerText || '').trim().slice(0, 60);

                    // 找父容器文本
                    let pt = '';
                    let p = link.parentElement;
                    for (let i=0; i<3 && p && pt.length < 50; i++) {
                        pt = (p.innerText || '').trim();
                        p = p.parentElement;
                    }

                    results.push({
                        y, w, h, visible,
                        href: href.slice(0, 90),
                        text: text || '(空)',
                        parentText: (pt || text || '(空)').slice(0, 100),
                    });
                }
                return results;
            }
        """)

        print(f"   找到 {len(details)} 个")
        for i, d in enumerate(details):
            vis = "可见" if d['visible'] else "不可见"
            print(f"\n   [{i}] {vis} y={d['y']} {d['w']}x{d['h']}")
            print(f"       href: {d['href']}")
            print(f"       文本: {d['parentText']}")

        # === 点击"用户"tab 后的变化 ===
        print(f"\n4. 尝试点击「用户」tab...")
        try:
            # 找用户tab
            tabs = ["用户", "用户"]
            clicked = False
            for txt in tabs:
                try:
                    tab = page.get_by_text(txt, exact=True).first
                    if tab.count() > 0 and tab.is_visible(timeout=2000):
                        tab.click(timeout=3000)
                        clicked = True
                        break
                except:
                    pass

            if clicked:
                print("   已点击用户tab")
                time.sleep(3)
            else:
                print("   ⚠️ 未找到用户tab")
        except Exception as e:
            print(f"   点击用户tab报错: {e}")

        # 再次检查用户链接
        print(f"\n5. 点击「用户」tab后，含 /user/ 的链接:")
        details2 = page.evaluate("""
            () => {
                const results = [];
                const links = document.querySelectorAll('a');
                for (const link of links) {
                    let href = link.getAttribute('href') || '';
                    if (!href.includes('/user/')) continue;
                    if (!href.startsWith('http')) {
                        if (href.startsWith('//')) href = 'https:' + href;
                        else href = 'https://www.douyin.com' + href;
                    }
                    const rect = link.getBoundingClientRect();
                    let pt = '';
                    let p = link.parentElement;
                    for (let i=0; i<3 && p && pt.length < 50; i++) {
                        pt = (p.innerText || '').trim();
                        p = p.parentElement;
                    }
                    results.push({
                        y: rect ? Math.round(rect.top) : -1,
                        visible: rect ? (rect.top >= 0 && rect.top <= window.innerHeight) : false,
                        href: href.slice(0, 90),
                        text: (pt || link.innerText || '').trim().slice(0, 100),
                    });
                }
                return results;
            }
        """)
        print(f"   找到 {len(details2)} 个")
        for i, d in enumerate(details2[:20]):
            vis = "可见" if d['visible'] else "不可见"
            print(f"   [{i}] {vis} y={d['y']}  {d['text'][:60]}")
            print(f"         {d['href']}")

        # === 含"抖音号"的容器 ===
        print(f"\n6. 含「抖音号」的容器（搜索卡片特征）:")
        containers = page.evaluate("""
            () => {
                const results = [];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const text = (el.innerText || '').trim();
                    if (text.includes('抖音号') && text.length > 15 && text.length < 3000) {
                        const rect = el.getBoundingClientRect();
                        const links = el.querySelectorAll('a[href*="/user/"]');
                        const hrefs = [];
                        links.forEach(a => {
                            let h = a.getAttribute('href') || '';
                            if (h && !h.startsWith('http')) h = 'https://www.douyin.com' + h;
                            hrefs.push(h);
                        });
                        results.push({
                            y: rect ? Math.round(rect.top) : -1,
                            text: text.slice(0, 200),
                            links: hrefs.slice(0, 3),
                        });
                        if (results.length >= 10) return results;
                    }
                }
                return results;
            }
        """)
        print(f"   找到 {len(containers)} 个容器")
        for d in containers[:5]:
            print(f"\n   y={d['y']}")
            print(f"   链接: {d['links']}")
            print(f"   文本: {d['text'][:150]}")

    except Exception as e:
        print(f"\n❌ 出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        bm.close()
        print("\n✅ 诊断完成")


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else "黄冈市黄梅县女装店"
    diagnose(kw)

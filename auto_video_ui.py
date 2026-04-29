import re
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

CATALOG_URL_KEY = "/classBase/classStudy"
VIDEO_URL_KEY = "/course/"
FALLBACK_SECONDS = 40 * 60
EXTRA_BUFFER_SECONDS = 5
MAX_PAGE = 5


@dataclass
class RuntimeState:
    running: bool = False
    step: str = "待启动"
    status: str = "空闲"
    page_index: str = "未知"
    found_video: str = "否"
    open_video_ok: str = "否"
    parse_time_ok: str = "否"
    using_fallback: str = "否"
    countdown: str = "-"


class VideoAutomationUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("视频自动学习助手")
        self.root.geometry("860x620")

        self.state = RuntimeState()
        self.stop_event = threading.Event()
        self.skip_wait_event = threading.Event()
        self.worker: threading.Thread | None = None

        self.labels: dict[str, tk.StringVar] = {}
        self._build_ui()

    def _build_ui(self):
        panel = ttk.Frame(self.root, padding=12)
        panel.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(panel)
        btn_frame.pack(fill="x", pady=(0, 12))

        self.btn_start = ttk.Button(btn_frame, text="开始", command=self.start)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_stop = ttk.Button(btn_frame, text="停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.btn_skip_wait = ttk.Button(btn_frame, text="跳过当前等待", command=self.skip_wait, state="disabled")
        self.btn_skip_wait.pack(side="left")

        status_grid = ttk.LabelFrame(panel, text="实时状态", padding=10)
        status_grid.pack(fill="x")

        fields = [
            ("运行状态", "status"),
            ("当前步骤", "step"),
            ("当前目录页码", "page_index"),
            ("是否找到待看视频", "found_video"),
            ("是否成功打开视频页", "open_video_ok"),
            ("是否成功捕获剩余时间", "parse_time_ok"),
            ("是否使用40分钟兜底", "using_fallback"),
            ("当前倒计时", "countdown"),
        ]

        for i, (title, key) in enumerate(fields):
            ttk.Label(status_grid, text=f"{title}：").grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            var = tk.StringVar(value=getattr(self.state, key))
            ttk.Label(status_grid, textvariable=var).grid(row=i, column=1, sticky="w", pady=2)
            self.labels[key] = var

        log_frame = ttk.LabelFrame(panel, text="日志", padding=10)
        log_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.log = tk.Text(log_frame, height=18, wrap="word")
        self.log.pack(fill="both", expand=True)

    def set_state(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self.state, k):
                setattr(self.state, k, v)
                if k in self.labels:
                    self.root.after(0, lambda key=k, val=v: self.labels[key].set(str(val)))

    def append_log(self, msg: str):
        now = time.strftime("%H:%M:%S")
        line = f"[{now}] {msg}\n"

        def _write():
            self.log.insert("end", line)
            self.log.see("end")

        self.root.after(0, _write)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.skip_wait_event.clear()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.set_state(status="运行中", step="初始化", countdown="-")
        self.worker = threading.Thread(target=self.run_automation, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.append_log("收到停止信号，将在当前步骤后结束。")
        self.set_state(status="停止中")

    def skip_wait(self):
        self.skip_wait_event.set()
        self.append_log("收到跳过等待信号：将在步骤5立即继续下一步。")

    def finish(self, status="已停止"):
        self.set_state(status=status, running=False, step="结束", countdown="-")
        self.root.after(0, lambda: self.btn_start.config(state="normal"))
        self.root.after(0, lambda: self.btn_stop.config(state="disabled"))
        self.root.after(0, lambda: self.btn_skip_wait.config(state="disabled"))

    @staticmethod
    def parse_remaining_seconds(text: str) -> int | None:
        if not text:
            return None
        clean = text.strip().replace("−", "-")
        clean = clean.lstrip("-")
        if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", clean):
            return None
        parts = [int(x) for x in clean.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]



    @staticmethod
    def extract_remaining_text(video_page) -> str | None:
        try:
            text = video_page.evaluate("""() => {
                const candidates = [];
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    const raw = (walker.currentNode.nodeValue || '').trim();
                    if (!raw) continue;
                    const m = raw.match(/-\d{1,2}:\d{2}(:\d{2})?/);
                    if (m) candidates.push(m[0]);
                }
                return candidates.length ? candidates[candidates.length - 1] : null;
            }""")
            if isinstance(text, str) and text:
                return text
        except Exception:
            pass
        return None

    @staticmethod
    def detect_current_page(page) -> str:
        selectors = [
            ".pagination .active",
            ".layui-laypage-curr em:last-child",
            ".layui-laypage .layui-laypage-curr",
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            try:
                text = loc.inner_text().strip()
            except Exception:
                continue
            m = re.search(r"(\d+)", text)
            if m:
                return m.group(1)
        return "1"

    def goto_page(self, catalog_page, target_page: int):
        if target_page <= 1:
            return
        self.append_log(f"刷新后恢复到第 {target_page} 页。")
        for _ in range(target_page - 1):
            if self.stop_event.is_set():
                return
            catalog_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            catalog_page.wait_for_timeout(500)
            next_btn = catalog_page.locator("text='›'").last
            if next_btn.count() == 0:
                next_btn = catalog_page.locator("text='>'").last
            if next_btn.count() == 0:
                next_btn = catalog_page.locator(".layui-laypage-next,a[aria-label='Next']").first
            if next_btn.count() == 0:
                self.append_log("恢复页码失败：未找到下一页按钮。")
                return
            next_btn.click()
            catalog_page.wait_for_timeout(1000)

    def wait_with_countdown(self, total_seconds: int):
        self.skip_wait_event.clear()
        self.root.after(0, lambda: self.btn_skip_wait.config(state="normal"))
        for remain in range(total_seconds, -1, -1):
            if self.stop_event.is_set():
                self.root.after(0, lambda: self.btn_skip_wait.config(state="disabled"))
                return
            if self.skip_wait_event.is_set():
                self.append_log("已跳过当前等待。")
                self.root.after(0, lambda: self.btn_skip_wait.config(state="disabled"))
                return
            self.set_state(countdown=f"{remain}s")
            if remain % 10 == 0:
                self.append_log(f"等待中，剩余 {remain}s")
            time.sleep(1)
        self.root.after(0, lambda: self.btn_skip_wait.config(state="disabled"))

    def run_automation(self):
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                if not context.pages:
                    raise RuntimeError("未检测到已打开的标签页，请先打开目录页。")

                catalog_page = None
                for pg in context.pages:
                    if CATALOG_URL_KEY in pg.url:
                        catalog_page = pg
                        break
                if not catalog_page:
                    catalog_page = context.pages[0]

                self.append_log("已连接 Edge，开始执行流程。")
                self.set_state(step="步骤1：定位目录页")

                current_catalog_page = int(self.detect_current_page(catalog_page))
                self.set_state(page_index=str(current_catalog_page))

                while not self.stop_event.is_set():
                    catalog_page.bring_to_front()
                    catalog_page.wait_for_timeout(800)
                    self.set_state(step="步骤2：搜索待看视频", found_video="否", open_video_ok="否", parse_time_ok="否", using_fallback="否")

                    btn = catalog_page.locator(
                        "button:text-is('学习中'),a:text-is('学习中'),"
                        "button:text-is('开始学习'),a:text-is('开始学习')"
                    ).first
                    if btn.count() > 0:
                        self.set_state(found_video="是")
                        self.append_log("找到待看视频，准备打开。")
                        pages_before = set(context.pages)
                        btn.click()
                        catalog_page.wait_for_timeout(1500)

                        new_pages = [pg for pg in context.pages if pg not in pages_before]
                        video_page = new_pages[0] if new_pages else None
                        if not video_page:
                            for pg in context.pages:
                                if VIDEO_URL_KEY in pg.url and pg != catalog_page:
                                    video_page = pg
                                    break
                        if not video_page:
                            self.append_log("未检测到新视频页，跳过该条目。")
                            continue

                        self.set_state(step="步骤3：播放视频", open_video_ok="是")
                        video_page.bring_to_front()
                        video_page.wait_for_timeout(1200)

                        video_page.mouse.click(960, 540)
                        video_page.wait_for_timeout(600)
                        try:
                            video_page.eval_on_selector("video", "v => { v.muted = true; v.volume = 0; return v.play(); }")
                            self.append_log("已执行静音并尝试播放。")
                        except Exception:
                            self.append_log("未找到标准 video 标签，已保留点击播放结果。")

                        self.set_state(step="步骤4：读取剩余时间")
                        remaining_text = None
                        for _ in range(5):
                            video_page.bring_to_front()
                            video_page.mouse.move(960, 540)
                            video_page.wait_for_timeout(500)
                            loc = video_page.locator("text=/-\\d{1,2}:\\d{2}(:\\d{2})?/").last
                            if loc.count() > 0:
                                try:
                                    remaining_text = loc.inner_text(timeout=800)
                                except PlaywrightTimeoutError:
                                    remaining_text = None
                            if not remaining_text:
                                remaining_text = self.extract_remaining_text(video_page)
                            if remaining_text:
                                break

                        watch_seconds = None
                        if remaining_text:
                            watch_seconds = self.parse_remaining_seconds(remaining_text)

                        if watch_seconds is not None:
                            total_wait = watch_seconds + EXTRA_BUFFER_SECONDS
                            self.set_state(parse_time_ok="是", using_fallback="否")
                            self.append_log(f"捕获剩余时间 {remaining_text}，等待 {total_wait}s（含缓冲5s）。")
                        else:
                            total_wait = FALLBACK_SECONDS
                            self.set_state(parse_time_ok="否", using_fallback="是")
                            self.append_log("捕获剩余时间失败，启用40分钟兜底计时。")

                        self.set_state(step="步骤5：延时观看")
                        self.wait_with_countdown(total_wait)
                        if self.stop_event.is_set():
                            break

                        self.set_state(step="步骤6：关闭视频并返回目录")
                        try:
                            video_page.close()
                        except Exception:
                            pass

                        for pg in context.pages:
                            if CATALOG_URL_KEY in pg.url:
                                catalog_page = pg
                                break

                        catalog_page.bring_to_front()
                        catalog_page.reload(wait_until="domcontentloaded")
                        self.goto_page(catalog_page, current_catalog_page)
                        self.set_state(page_index=str(current_catalog_page))
                        self.append_log("已返回目录页并刷新状态。")
                        continue

                    self.append_log("当前页未找到待看视频，准备翻页。")
                    self.set_state(step="步骤7：翻页检查")
                    catalog_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    catalog_page.wait_for_timeout(800)

                    current_page_num = self.detect_current_page(catalog_page)
                    try:
                        current_catalog_page = int(current_page_num)
                    except ValueError:
                        current_catalog_page = 1
                    self.set_state(page_index=str(current_catalog_page))

                    if current_catalog_page >= MAX_PAGE:
                        self.append_log("已到第5页且无待看视频，任务结束。")
                        break

                    next_btn = catalog_page.locator("text='›'").last
                    if next_btn.count() == 0:
                        next_btn = catalog_page.locator(".layui-laypage-next,a[aria-label='Next']").first

                    if next_btn.count() > 0:
                        next_btn.click()
                        catalog_page.wait_for_timeout(1500)
                        self.append_log("已翻到下一页。")
                    else:
                        self.append_log("未找到下一页按钮，结束任务。")
                        break

                self.finish("已完成")
        except Exception as exc:
            self.append_log(f"程序异常：{exc}")
            self.finish("异常停止")


def main():
    root = tk.Tk()
    app = VideoAutomationUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

import os
import time
import requests
from playwright.sync_api import sync_playwright

# 1. 从环境变量读取配置
LOGIN_URL = os.environ["LOGIN_URL"]
SERVERS_API_URL = os.environ["SERVERS_API_URL"]
PANEL_URL = os.environ["PANEL_URL"]
ACTION_API_URL = os.environ["ACTION_API_URL"]

TARGET_HOST = os.environ["TARGET_HOST"]
TARGET_ORIGIN = os.environ["TARGET_ORIGIN"]

SITE_LABEL = os.getenv("SITE_LABEL", "AUTO_TASK")

# 2. 从环境变量获取账号及 Telegram 配置
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
EMAIL = os.environ["LOGIN_EMAIL"]
PASSWORD = os.environ["LOGIN_PASSWORD"]


def send_telegram_msg(text):
    """发送文字消息到 Telegram"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print(f"[{SITE_LABEL}] Telegram 配置缺失，跳过发送消息。")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": f"[{SITE_LABEL}] {text}"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"发送Telegram消息失败: {e}")


def send_telegram_photo(photo_path, caption=""):
    """发送图片到 Telegram"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            data = {
                "chat_id": TG_CHAT_ID,
                "caption": f"[{SITE_LABEL}] {caption}"
            }
            requests.post(url, data=data, files=files, timeout=20)
    except Exception as e:
        print(f"发送Telegram图片失败: {e}")


def run():
    send_telegram_msg("🚀 开始执行模拟登录及自动服务器操作流程...")

    with sync_playwright() as p:
        # 使用原生 Chrome 启动参数覆盖自动化特征
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            ignore_https_errors=True
        )

        # 原生 JS 注入：抹除 navigator.webdriver 自动化标记，效果与 stealth 一致且不会报错
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        page = context.new_page()

        try:
            print("正在打开登录页面...")
            
            # 使用 domcontentloaded 加载页面
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=40000)

            print("页面已响应，正在等待 Load assets 进度条跑完...")
            
            # 1. 动态监控：等待包含 "Load assets" 的进度条消失
            try:
                page.locator("text=/load assets/i").wait_for(state="detached", timeout=15000)
                print("检测到 Load assets 提示已消失。")
            except Exception:
                print("未捕捉到 Load assets 消失事件或资源加载较快，继续检查表单...")

            # 2. 关键等待：等待邮箱输入框彻底处于 可见 (visible) 状态
            email_locator = page.locator('//*[@id="email"]')
            email_locator.wait_for(state="visible", timeout=40000)

            # 3. 留出 3 秒给前端框架（React/Vue）绑定事件
            time.sleep(3)

            print("登录页面及前端资源完全加载完成！")
            page.screenshot(path="01_login_page.png")
            send_telegram_photo("01_login_page.png", "1. Load assets 跑完，登录页彻底就绪")

            print("正在填写账号信息...")
            email_locator.click()
            email_locator.fill(EMAIL)
            time.sleep(0.5)

            pwd_locator = page.locator('//*[@id="password"]')
            pwd_locator.click()
            pwd_locator.fill(PASSWORD)
            time.sleep(0.5)

            # 拦截登录后页面自动发起的 /api/servers 响应
            print("点击登录，同时监听拦截 /api/servers 请求响应...")
            with page.expect_response(lambda response: SERVERS_API_URL in response.url, timeout=25000) as response_info:
                page.locator('//*[@id="submitBtn"]').click()

            response = response_info.value
            request = response.request

            time.sleep(3)
            page.screenshot(path="02_after_login.png")
            send_telegram_photo("02_after_login.png", "2. 登录操作已完成，并成功拦截 API 响应")

            # 从拦截请求中提取凭据
            req_headers = request.headers
            auth_header = req_headers.get("authorization")
            cookie_header = req_headers.get("cookie")

            if not cookie_header:
                cookies = context.cookies()
                session_id = next((c["value"] for c in cookies if c["name"] == "session_id"), None)
                if session_id:
                    cookie_header = f"session_id={session_id}"

            if not auth_header or not cookie_header:
                raise Exception("未能成功拦截到 authorization 或 cookie！")

            msg_info = f"提取凭证成功！\nAuthorization: {auth_header[:25]}...\nCookie: {cookie_header[:25]}..."
            print(msg_info)
            send_telegram_msg(msg_info)

            # 解析响应检查 status 状态
            res_json = response.json()
            servers_list = res_json.get("servers", [])

            if not servers_list:
                raise Exception("未找到任何服务器节点数据！")

            server_info = servers_list[0]
            server_status = server_info.get("status", "Unknown")
            print(f"服务器当前状态: {server_status}")

            if server_status != "Offline":
                cancel_msg = f"⚠️ 服务器当前状态为 [{server_status}]，非 Offline 状态，已自动取消启动操作！"
                print(cancel_msg)

                page.goto(PANEL_URL)
                time.sleep(3)
                page.screenshot(path="03_skipped_status.png")

                send_telegram_photo("03_skipped_status.png", cancel_msg)
                return

            # status 为 Offline 时才发送 Start 操作
            exact_headers = {
                "accept": "*/*",
                "accept-encoding": "gzip, deflate, br, zstd",
                "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                "authorization": auth_header,
                "connection": "keep-alive",
                "content-type": "application/json",
                "cookie": cookie_header,
                "host": TARGET_HOST,
                "origin": TARGET_ORIGIN,
                "referer": PANEL_URL,
                "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
            }

            payload = {"action": "Start"}

            print("服务器为 Offline 状态，发送 Start POST 请求...")
            post_res = requests.post(ACTION_API_URL, headers=exact_headers, json=payload, timeout=15)

            res_text = post_res.text
            print(f"POST 响应结果: {res_text}")

            page.goto(PANEL_URL)
            time.sleep(3)
            page.screenshot(path="03_action_result.png")

            send_telegram_photo("03_action_result.png", f"3. POST 请求完成！\n响应状态码: {post_res.status_code}\n响应内容: {res_text}")
            send_telegram_msg("✅ 自动化流程全过程成功执行完毕！")

        except Exception as e:
            error_msg = f"❌ 执行过程中发生错误: {str(e)}"
            print(error_msg)
            page.screenshot(path="error.png")
            send_telegram_photo("error.png", error_msg)
        finally:
            browser.close()


if __name__ == "__main__":
    run()

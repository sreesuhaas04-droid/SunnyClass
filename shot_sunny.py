import time, pathlib
from playwright.sync_api import sync_playwright
OUT = pathlib.Path("preview"); OUT.mkdir(exist_ok=True)
BASE = "http://localhost:8000"

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-ui-for-media-stream",
                                "--use-fake-device-for-media-stream", "--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1440, "height": 980}, device_scale_factor=2,
                        permissions=["camera", "microphone"])
    page = ctx.new_page()
    page.goto(BASE, wait_until="networkidle")
    page.click("#fill-student"); page.click("#auth-submit")
    page.wait_for_url("**/dashboard.html", timeout=20000)
    page.wait_for_selector("#stats .stat-value", timeout=20000)
    time.sleep(2)

    page.click("#sunny-fab"); time.sleep(1.2)
    for q in ["What is my attendance, and am I at risk?",
              "How does face recognition work?",
              "Summarise today's lecture"]:
        page.fill("#sunny-input", q)
        page.click("#sunny-send")
        time.sleep(2.2)
    time.sleep(1)
    page.screenshot(path=str(OUT / "12-sunny-grounded.png"))
    print("captured 12-sunny-grounded")

    # scroll the chat up to show the first answer too
    page.evaluate("document.getElementById('sunny-messages').scrollTop = 0")
    time.sleep(.8)
    page.screenshot(path=str(OUT / "13-sunny-top.png"))
    print("captured 13-sunny-top")
    ctx.close(); b.close()

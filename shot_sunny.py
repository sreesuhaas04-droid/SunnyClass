"""Render SunnyClass in a headless browser with a fake camera and capture previews."""
import sys, time, pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "preview"); OUT.mkdir(exist_ok=True)
BASE = "http://localhost:8000"
errors = []

def shot(page, name, full=False):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print("  captured", name)

with sync_playwright() as p:
    browser = p.chromium.launch(args=[
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        "--autoplay-policy=no-user-gesture-required",
        "--no-sandbox",
    ])

    def new_ctx(w, h, mobile=False):
        ctx = browser.new_context(
            viewport={"width": w, "height": h},
            device_scale_factor=2,
            is_mobile=mobile, has_touch=mobile,
            permissions=["camera", "microphone"],
        )
        ctx.grant_permissions(["camera", "microphone"], origin=BASE)
        pg = ctx.new_page()
        pg.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
        return ctx, pg

    # ---------- landing (dark + light) ----------
    ctx, page = new_ctx(1440, 900)
    page.goto(BASE, wait_until="networkidle")
    time.sleep(1)
    shot(page, "01-landing-dark")
    page.click("#btn-theme"); time.sleep(.6)
    shot(page, "02-landing-light")
    page.click("#btn-theme"); time.sleep(.4)

    # ---------- student login ----------
    page.click("#fill-student")
    page.click("#auth-submit")
    page.wait_for_url("**/dashboard.html", timeout=15000)
    page.wait_for_selector("#stats .stat-value", timeout=15000)
    time.sleep(2.5)
    shot(page, "03-dashboard-student-dark", full=True)
    page.click("#btn-theme"); time.sleep(1.2)
    shot(page, "04-dashboard-student-light", full=True)
    page.click("#btn-theme"); time.sleep(.8)

    # SUNNY
    page.click("#sunny-fab"); time.sleep(1)
    page.fill("#sunny-input", "What is my attendance?")
    page.click("#sunny-send"); time.sleep(2.5)
    shot(page, "05-sunny-chat")
    page.click("#sunny-close"); time.sleep(.4)
    ctx.close()

    # ---------- classroom itself (teacher hosts, so no face gate) ----------
    ctx, page = new_ctx(1440, 900)
    page.goto(BASE, wait_until="networkidle")
    page.fill("#f-email", "ramesh.iyer@sunnyclass.edu"); page.fill("#f-pass", "teach1234")
    page.click("#auth-submit")
    page.wait_for_url("**/dashboard.html", timeout=15000)
    page.wait_for_selector("#btn-join:not([hidden])", timeout=20000)
    page.click("#btn-join")
    page.wait_for_url("**/app.html**", timeout=15000)
    time.sleep(14)
    try:
        btn = page.locator("#gate-action")
        print("  host gate:", btn.inner_text(), "| enabled:", btn.is_enabled())
        if btn.is_enabled():
            btn.click()
            page.wait_for_selector("#shell:not([hidden])", timeout=12000)
            time.sleep(7)
            shot(page, "07-classroom")
            page.click('.side-tab[data-panel="attendance"]'); time.sleep(2.5)
            shot(page, "08-classroom-attendance")
            page.click('.side-tab[data-panel="transcript"]'); time.sleep(1.5)
            shot(page, "08b-classroom-transcript")
    except Exception as e:
        print("  classroom entry failed:", type(e).__name__, str(e)[:160])
        shot(page, "07-classroom-failed")
    ctx.close()

    # ---------- teacher dashboard ----------
    ctx, page = new_ctx(1440, 900)
    page.goto(BASE, wait_until="networkidle")
    page.fill("#f-email", "ramesh.iyer@sunnyclass.edu")
    page.fill("#f-pass", "teach1234")
    page.click("#auth-submit")
    page.wait_for_url("**/dashboard.html", timeout=15000)
    page.wait_for_selector("#roster-table table", timeout=15000)
    time.sleep(2.5)
    shot(page, "09-dashboard-teacher", full=True)
    ctx.close()

    # ---------- phone ----------
    ctx, page = new_ctx(390, 844, mobile=True)
    page.goto(BASE, wait_until="networkidle")
    page.click("#fill-student"); page.click("#auth-submit")
    page.wait_for_url("**/dashboard.html", timeout=15000)
    page.wait_for_selector("#stats .stat-value", timeout=15000)
    time.sleep(2.5)
    shot(page, "10-dashboard-phone", full=True)
    ctx.close()

    browser.close()

print("\n--- console errors ---")
seen = set()
for e in errors:
    if e not in seen:
        seen.add(e); print(" ", e[:200])
if not errors: print("  none")

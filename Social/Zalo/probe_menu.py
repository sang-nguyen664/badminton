"""Probe menu chuot phai / nut '...' tren 1 conversation item de tim 'Danh dau chua doc'."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SOCIAL_DIR = Path(__file__).resolve().parents[1]
if str(SOCIAL_DIR) not in sys.path:
    sys.path.insert(0, str(SOCIAL_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from social_scheduler import (  # noqa: E402
    RuntimeOptions,
    build_context,
    close_all_pages,
    close_context,
    configure_stdout_for_windows,
)
from zalo_auto_reply import LOG_DIR, ZALO_HOME, make_account_config, profile_dir_for  # noqa: E402

MENU_JS = r"""() => {
    const out = [];
    document.querySelectorAll("[class*='zmenu'], [class*='context'], [class*='popover']").forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 10) {
            out.push({cls: (el.className || '').toString().slice(0, 80),
                      text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 300)});
        }
    });
    return out.slice(0, 10);
}"""

HOVER_BTNS_JS = r"""() => {
    const item = document.querySelector("div.msg-item");
    if (!item) return [];
    const out = [];
    item.querySelectorAll("[class*='more'], [class*='action'], [role='button'], [class*='btn']").forEach(el => {
        const r = el.getBoundingClientRect();
        out.push({cls: (el.className || '').toString().slice(0, 90), w: Math.round(r.width),
                  title: el.getAttribute('title') || '', aria: el.getAttribute('aria-label') || ''});
    });
    return out.slice(0, 20);
}"""


async def main() -> int:
    configure_stdout_for_windows()
    options = RuntimeOptions(headless=False, dry_run=False, debug_composer=False,
                             pause_for_debugger=False, performance_mode=True, concurrency=1)
    profile_dir = profile_dir_for("main")
    context = await build_context(profile_dir=profile_dir, account_config=make_account_config(profile_dir), options=options)
    page = await close_all_pages(context)
    result: dict = {}
    try:
        await page.goto(ZALO_HOME, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)
        first = page.locator("div.msg-item").first
        item_id = await first.get_attribute("anim-data-id")
        print(f"[INFO] item dau tien: {item_id}")

        print("[INFO] thu chuot phai...")
        try:
            await first.click(button="right", timeout=4000)
            await page.wait_for_timeout(1500)
            result["rightclick_menu"] = await page.evaluate(MENU_JS)
            print(f"[INFO] menu: {json.dumps(result['rightclick_menu'], ensure_ascii=False)[:800]}")
        except Exception as exc:
            print(f"[WARN] right-click loi: {str(exc)[:150]}")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)

        print("[INFO] thu hover tim nut ...")
        await first.hover()
        await page.wait_for_timeout(1000)
        result["hover_buttons"] = await page.evaluate(HOVER_BTNS_JS)
        print(f"[INFO] hover buttons: {json.dumps(result['hover_buttons'], ensure_ascii=False)[:800]}")

        out_file = LOG_DIR / "zalo_menu_dump.json"
        out_file.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        shot = LOG_DIR / "zalo_menu.png"
        await page.screenshot(path=str(shot), full_page=False)
        print(f"[OK] Dump: {out_file}")
    finally:
        try:
            await page.close()
        except Exception:
            pass
        await close_context(context)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

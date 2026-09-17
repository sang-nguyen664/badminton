from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from social_scheduler import (
        RuntimeOptions,
        build_context,
        close_all_pages,
        close_context,
        configure_stdout_for_windows,
        discover_account_files,
        ensure_logged_in,
        load_account_config,
        read_json_file,
        resolve_from_base,
    )
except ModuleNotFoundError as exc:
    if exc.name == "playwright":
        print("[ERROR] Missing Python package: playwright")
        print(f"[INFO] Run with project venv: {Path(__file__).resolve().parent}\\.venv\\Scripts\\python.exe {Path(__file__).resolve()}")
        sys.exit(1)
    raise

# ==================== CAU HINH (chinh sua tai day) ====================
ACCOUNT = "linh"                    # Ten account trong config/account_<ten>.json. De trong "" = hien menu chon.
TARGET_FILES = [                # Cac file target can doc, viet theo duong dan tuong doi tu folder Social
    "config/targets.json",
]
START_ID = 1                   # Chi xu ly cac target co so id >= gia tri nay (bo qua target cu)
# ======================================================================

BASE_DIR = Path(__file__).resolve().parent
LOCK_DIR = BASE_DIR / "logs" / "locks"
JOIN_LOG_DIR = BASE_DIR / "logs" / "join_groups"
DELAY_SECONDS = 8

JOIN_BUTTON_SELECTORS = [
    "div[role='button'][aria-label*='Tham gia nhóm']",
    "div[role='button'][aria-label*='Join group']",
    "div[role='button'][aria-label*='Tham gia']",
]

PENDING_SELECTORS = [
    "div[role='button'][aria-label*='Đã gửi yêu cầu']",
    "div[role='button'][aria-label*='Hủy yêu cầu']",
    "div[role='button'][aria-label*='Cancel request']",
    "div[role='button'][aria-label*='Requested']",
]

JOINED_SELECTORS = [
    "div[role='button'][aria-label*='Đã tham gia']",
    "div[role='button'][aria-label*='Joined']",
]

CLOSE_DIALOG_SELECTORS = [
    "div[role='dialog'] div[role='button'][aria-label='Đóng']",
    "div[role='dialog'] div[role='button'][aria-label='Close']",
    "div[role='dialog'] div[aria-label='Đóng']",
    "div[role='dialog'] div[aria-label='Close']",
]
DIALOG_SUBMIT_SELECTORS = [
    "div[role='dialog'] div[role='button'][aria-label*='Gửi']",
    "div[role='dialog'] div[role='button'][aria-label*='Submit']",
    "div[role='dialog'] div[role='button'][aria-label*='Đồng ý']",
    "div[role='dialog'] div[role='button'][aria-label*='Agree']",
    "div[role='dialog'] div[role='button'][aria-label*='Xác nhận']",
    "div[role='dialog'] div[role='button'][aria-label*='Confirm']",
    "div[role='dialog'] div[role='button'][aria-label*='Tham gia']",
    "div[role='dialog'] div[role='button'][aria-label*='Join']",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tham gia cac group moi trong file target bang 1 account.")
    parser.add_argument("--account", default=None, help=f"Ten account trong config (mac dinh doc tu CAU HINH: {ACCOUNT or 'menu chon'}).")
    parser.add_argument("--targets", nargs="*", default=None, help="Cac file target (mac dinh doc tu CAU HINH TARGET_FILES).")
    parser.add_argument("--start-id", type=int, default=None, help=f"Chi xu ly target co so id >= gia tri nay (mac dinh: {START_ID}).")
    parser.add_argument("--dry-run", action="store_true", help="Chi doc trang thai, khong bam tham gia.")
    parser.add_argument("--max", type=int, default=0, help="Gioi han so group xu ly (0 = tat ca).")
    parser.add_argument("--ids", default="", help="Chi xu ly cac id cu the, phan cach dau phay (vd: target-25,target-29).")
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def load_new_targets(targets_paths: list[str], start_id: int, only_ids: set[str] | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for targets_path in targets_paths:
        resolved = resolve_from_base(targets_path)
        if not resolved.exists():
            raise SystemExit(f"[ERROR] File target khong ton tai: {targets_path}")
        payload = read_json_file(resolved)
        for item in payload.get("targets", []):
            try:
                numeric_id = int(str(item.get("id", "")).split("-")[1])
            except (IndexError, ValueError):
                continue
            if numeric_id < start_id:
                continue
            if only_ids is not None and item.get("id") not in only_ids:
                continue
            results.append(item)
    return results


def discover_accounts() -> list[str]:
    """Doc danh sach account tu config/account_*.json."""
    selected, _ = discover_account_files(resolve_from_base("config"))
    return sorted(path.stem.replace("account_", "") for path in selected)


def resolve_account(arg_value: str) -> str:
    names = discover_accounts()
    if not names:
        raise SystemExit("[ERROR] Khong tim thay account nao trong config/account_*.json")
    if arg_value:
        if arg_value not in names:
            raise SystemExit(f"[ERROR] Account '{arg_value}' khong ton tai. Co san: {', '.join(names)}")
        return arg_value

    print("Chon account de tham gia group (doc tu config):")
    for index, name in enumerate(names, start=1):
        print(f"  {index}. {name}")
    try:
        choice = input("Nhap so thu tu: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("[ERROR] Khong doc duoc lua chon. Dung --account <ten> de chi dinh truc tiep.")
    if choice.isdigit() and 1 <= int(choice) <= len(names):
        return names[int(choice) - 1]
    if choice in names:
        return choice
    raise SystemExit(f"[ERROR] Lua chon khong hop le: '{choice}'. Co san: {', '.join(names)}")


async def first_visible_button(page: Any, selectors: list[str], timeout_ms: int = 300) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(min(count, 5)):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible(timeout=timeout_ms):
                    return candidate
            except Exception:
                continue
    return None


async def detect_state(page: Any) -> str:
    if await first_visible_button(page, JOINED_SELECTORS) is not None:
        return "already_member"
    if await first_visible_button(page, PENDING_SELECTORS) is not None:
        return "pending"
    if await first_visible_button(page, JOIN_BUTTON_SELECTORS) is not None:
        return "not_joined"
    return "unknown"


async def detect_questions_dialog(page: Any) -> str:
    """Tra ve text tom tat neu co dialog cau hoi gia nhap, nguoc lai chuoi rong."""
    dialog = page.locator("div[role='dialog']")
    try:
        count = await dialog.count()
    except Exception:
        return ""
    for index in range(min(count, 3)):
        candidate = dialog.nth(index)
        try:
            if not await candidate.is_visible(timeout=200):
                continue
            inputs = candidate.locator("input[type='text'], textarea, div[contenteditable='true']")
            if await inputs.count() > 0:
                text = (await candidate.inner_text()) or ""
                return " ".join(text.split())[:250]
        except Exception:
            continue
    return ""


async def close_dialog(page: Any) -> None:
    for selector in CLOSE_DIALOG_SELECTORS:
        locator = page.locator(selector)
        try:
            if await locator.count() > 0 and await locator.first.is_visible(timeout=300):
                await locator.first.click(timeout=1500)
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    except Exception:
        pass


async def process_group(page: Any, target: dict[str, Any], dry_run: bool) -> tuple[str, str]:
    url = target["url"]
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as exc:
        return "error", f"khong mo duoc link: {exc}"
    await page.wait_for_timeout(2500)

    state = await detect_state(page)
    if state == "already_member":
        return "already_member", "da la thanh vien"
    if state == "pending":
        return "pending", "da gui yeu cau truoc do, cho duyet"
    if state == "unknown":
        # Thu scroll nhe de header render day du roi do lai
        try:
            await page.mouse.wheel(0, 400)
            await page.wait_for_timeout(1200)
        except Exception:
            pass
        state = await detect_state(page)
        if state == "unknown":
            return "error", "khong xac dinh duoc trang thai nut tham gia"

    if dry_run:
        return "dry_run", "chua tham gia, se bam khi chay that"

    join_button = await first_visible_button(page, JOIN_BUTTON_SELECTORS)
    if join_button is None:
        return "error", "khong tim thay nut tham gia"
    try:
        await join_button.click(timeout=3000)
    except Exception as exc:
        return "error", f"khong bam duoc nut tham gia: {exc}"

    await page.wait_for_timeout(2500)

    questions = await detect_questions_dialog(page)
    if questions:
        await close_dialog(page)
        return "needs_questions", questions

    # Dialog xac nhan khong co o nhap (luat nhom, dong y dieu khoan...) -> bam tiep nut xac nhan
    submit_button = await first_visible_button(page, DIALOG_SUBMIT_SELECTORS)
    if submit_button is not None:
        try:
            await submit_button.click(timeout=2500)
            await page.wait_for_timeout(2500)
        except Exception:
            pass

    # Do lai trang thai toi da 3 lan, moi lan cach 2s (Facebook doi khi render cham)
    new_state = "unknown"
    for _ in range(3):
        new_state = await detect_state(page)
        if new_state != "unknown":
            break
        await page.wait_for_timeout(2000)

    if new_state == "already_member":
        return "joined", "tham gia thanh cong (cong khai)"
    if new_state == "pending":
        return "pending", "da gui yeu cau, cho admin duyet"
    if new_state == "not_joined":
        return "error", "da bam nhung trang thai khong doi"
    return "uncertain", "khong xac nhan duoc trang thai sau khi bam"


def runtime_options(headless: bool) -> RuntimeOptions:
    return RuntimeOptions(
        headless=headless,
        dry_run=False,
        debug_composer=False,
        pause_for_debugger=False,
        performance_mode=True,
        concurrency=1,
    )


async def async_main() -> int:
    args = parse_args()
    account = resolve_account(args.account if args.account is not None else ACCOUNT)
    target_files = args.targets if args.targets else TARGET_FILES
    start_id = args.start_id if args.start_id is not None else START_ID
    only_ids = {item.strip() for item in args.ids.split(",") if item.strip()} or None
    targets = load_new_targets(target_files, start_id, only_ids=only_ids)
    if args.max > 0:
        targets = targets[: args.max]
    if not targets:
        print("[INFO] Khong co group nao can xu ly.")
        return 0

    poster_lock = LOCK_DIR / f"poster_{account}.lock"
    if poster_lock.exists():
        age_min = (time.time() - poster_lock.stat().st_mtime) / 60
        if age_min <= 45:
            print(f"[ERROR] Account {account} dang trong lich dang bai (lock {age_min:.0f} phut truoc). Thu lai sau.")
            return 1
        poster_lock.unlink(missing_ok=True)

    account_config = load_account_config(resolve_from_base(f"config/account_{account}.json"), require_status=False)
    profile_dir = resolve_from_base(account_config.profile_dir)
    options = runtime_options(headless=args.headless)

    # Giu poster lock de tool bump nhuong duong trong luc tham gia group
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    poster_lock.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")

    JOIN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = JOIN_LOG_DIR / f"join_{account}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    log_lines: list[str] = []

    def log(line: str) -> None:
        print(line)
        log_lines.append(line)

    results: list[tuple[dict[str, Any], str, str]] = []
    context = None
    try:
        log(f"[INFO] Account: {account} | target files: {', '.join(target_files)} | start_id: {start_id}")
        log(f"[INFO] So group can xu ly: {len(targets)} | dry_run={args.dry_run}")
        context = await build_context(profile_dir=profile_dir, account_config=account_config, options=options)
        page = await close_all_pages(context)
        if not args.headless:
            try:
                session = await context.new_cdp_session(page)
                window_info = await session.send("Browser.getWindowForTarget")
                await session.send("Browser.setWindowBounds", {
                    "windowId": window_info["windowId"],
                    "bounds": {"windowState": "minimized"},
                })
            except Exception as exc:
                print(f"[WARN] Khong minimize duoc cua so browser: {exc}")

        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
        await ensure_logged_in(page, account_config, "https://www.facebook.com/", options)

        for position, target in enumerate(targets, start=1):
            name = target.get("name", target["url"])
            log(f"[INFO] ({position}/{len(targets)}) {name}")
            log(f"       {target['url']}")
            status, detail = await process_group(page, target, dry_run=args.dry_run)
            results.append((target, status, detail))
            log(f"[RESULT] {status}: {detail}")
            if position < len(targets):
                await page.wait_for_timeout(DELAY_SECONDS * 1000)

        try:
            await page.close()
        except Exception:
            pass
    finally:
        if context is not None:
            await close_context(context)
        poster_lock.unlink(missing_ok=True)

    joined = [r for r in results if r[1] == "joined"]
    already = [r for r in results if r[1] == "already_member"]
    pending = [r for r in results if r[1] == "pending"]
    questions = [r for r in results if r[1] == "needs_questions"]
    errors = [r for r in results if r[1] == "error"]
    uncertain = [r for r in results if r[1] == "uncertain"]
    dry = [r for r in results if r[1] == "dry_run"]

    log("")
    log("===== Join Summary =====")
    log(f"joined={len(joined)}, already_member={len(already)}, pending={len(pending)}, "
        f"needs_questions={len(questions)}, error={len(errors)}, uncertain={len(uncertain)}, dry_run={len(dry)}")
    if questions:
        log("Group can tra loi cau hoi thu cong:")
        for target, _, detail in questions:
            log(f"  - {target.get('name', target['url'])}")
            log(f"    {target['url']}")
            log(f"    cau hoi: {detail}")
    if errors:
        log("Group loi:")
        for target, _, detail in errors:
            log(f"  - {target.get('name', target['url'])}: {detail}")
    log("========================")

    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print(f"[OK] Log saved: {log_path}")

    return 0 if not errors else 1


def main() -> int:
    configure_stdout_for_windows()
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())

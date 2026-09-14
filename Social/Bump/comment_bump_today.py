from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SOCIAL_DIR = Path(__file__).resolve().parents[1]
if str(SOCIAL_DIR) not in sys.path:
    sys.path.insert(0, str(SOCIAL_DIR))

try:
    from social_scheduler import (  # noqa: E402
        RuntimeOptions,
        build_context,
        close_context,
        configure_stdout_for_windows,
        ensure_logged_in,
        load_account_config,
        resolve_from_base,
    )
except ModuleNotFoundError as exc:
    if exc.name == "playwright":
        print("[ERROR] Missing Python package: playwright")
        print(f"[INFO] Run with project venv: {SOCIAL_DIR}\\.venv\\Scripts\\python.exe {Path(__file__).resolve()}")
        print("[INFO] Or run: Bump\\run_comment_bump_today.bat")
        sys.exit(1)
    raise

DAILY_LOG_DIR = SOCIAL_DIR / "logs" / "daily_log"
COMMENT_TEXT = "."
COMMENT_DELAY_SECONDS = 12
BUMP_LOG_PREFIX = "comment_bump"

# Account map: log cua account nay thi dung account kia de comment.
ACCOUNT_MAP = {
    "linh": "config/account_sang.json",
    "sang": "config/account_linh.json",
}

COMMENT_EDITOR_SELECTORS = [
    "div[contenteditable='true'][role='textbox'][aria-label*='comment' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='Comment' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='bình luận' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='Bình luận' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='Viết bình luận' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='Write a comment' i]",
    "div[contenteditable='true'][role='textbox'][data-lexical-editor='true']",
    "div[contenteditable='true'][role='textbox']",
]

LOG_FILE_PATTERN = re.compile(
    r"^social_scheduler_(?P<account>[a-z]+)_(?P<time>\d{4})_(?P<date>\d{8})_(?P<clock>\d{6})\.txt$"
)
POST_URL_PATTERN = re.compile(r"^Link bai viet:\s*(?P<url>\S+)\s*$")


@dataclass(frozen=True)
class BumpJob:
    post_url: str
    group_name: str
    source_account: str
    comment_account: str
    log_file: str


@dataclass
class BumpResult:
    job: BumpJob
    status: str
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Comment '.' vao cac bai viet da dang hom nay (bump).")
    parser.add_argument("--dry-run", action="store_true", help="Chi mo bai viet, khong comment that.")
    parser.add_argument("--headless", action="store_true", help="Chay browser an.")
    parser.add_argument("--max-posts", type=int, default=0, help="Gioi han so bai comment (0 = khong gioi han).")
    parser.add_argument("--date", default="", help="Ngay can xu ly dang YYYYMMDD (mac dinh: hom nay).")
    parser.add_argument(
        "--slot",
        default="",
        choices=["", "1200", "1400", "1600", "1800"],
        help="Chi lay link tu dot dang nay. Mac dinh: tu dong theo gio hien tai.",
    )
    return parser.parse_args()


# Khung gio bump cho tung dot dang bai: (gio_bat_dau_bump, gio_ket_thuc_bump) tinh bang phut.
# Khung co the chong lan; khi chong, slot bat dau sau (bai moi dang hon) duoc uu tien.
SLOT_WINDOWS = {
    "1200": (12 * 60 + 30, 16 * 60),       # linh dang 12:00 -> bump 12:30-16:00
    "1400": (14 * 60 + 30, 18 * 60),       # sang dang 14:00 -> bump 14:30-18:00
    "1600": (16 * 60 + 30, 19 * 60 + 30),  # linh dang 16:00 -> bump 16:30-19:30
    "1800": (18 * 60 + 30, 19 * 60 + 30),  # sang dang 18:00 -> bump 18:30-19:30
}


def resolve_slot(now: datetime, slot_arg: str) -> str:
    """Xac dinh slot dang chay theo gio hien tai; uu tien slot bat dau gan nhat."""
    if slot_arg:
        return slot_arg
    minutes = now.hour * 60 + now.minute
    best = ""
    best_start = -1
    for slot, (start, end) in SLOT_WINDOWS.items():
        if start <= minutes <= end and start > best_start:
            best = slot
            best_start = start
    return best


def resolve_today_log_files(target_date: str, slot: str = "") -> list[Path]:
    if not DAILY_LOG_DIR.exists():
        return []
    files: list[Path] = []
    for path in sorted(DAILY_LOG_DIR.glob("social_scheduler_*.txt")):
        match = LOG_FILE_PATTERN.match(path.name)
        if not match:
            continue
        if match.group("date") != target_date:
            continue
        if slot and match.group("time") != slot:
            continue
        account = match.group("account").lower()
        if account not in ACCOUNT_MAP:
            continue
        files.append(path)
    return files


def bump_log_path(target_date: str) -> Path:
    return DAILY_LOG_DIR / f"{BUMP_LOG_PREFIX}_{target_date}.txt"


def append_bump_log(target_date: str, result: BumpResult) -> None:
    """Ghi nhan 1 luot comment vao file tracking theo ngay, append ngay khi xong moi bai."""
    try:
        DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%H:%M:%S")
        job = result.job
        line = (
            f"{timestamp} | {result.status} | "
            f"{job.source_account}->{Path(job.comment_account).stem.replace('account_', '')} | "
            f"{job.group_name} | {job.post_url} | {result.detail}\n"
        )
        with bump_log_path(target_date).open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception as exc:
        print(f"[WARN] Khong ghi duoc bump log: {exc}")


def parse_daily_log(path: Path) -> list[tuple[str, str]]:
    """Tra ve list (group_name, post_url) tu 1 file daily log."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return []

    results: list[tuple[str, str]] = []
    in_section = False
    current_group = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Link bai viet da dang thanh cong"):
            in_section = True
            continue
        if not in_section:
            continue
        if stripped.startswith("Ten group:"):
            current_group = stripped.split(":", 1)[1].strip()
            continue
        match = POST_URL_PATTERN.match(stripped)
        if match:
            url = match.group("url").strip()
            if url and not url.startswith("("):
                results.append((current_group, url))
            current_group = ""
    return results


def build_jobs(target_date: str, slot: str = "") -> list[BumpJob]:
    jobs: list[BumpJob] = []
    seen: set[str] = set()
    for log_file in resolve_today_log_files(target_date, slot=slot):
        match = LOG_FILE_PATTERN.match(log_file.name)
        if not match:
            continue
        source_account = match.group("account").lower()
        comment_account = ACCOUNT_MAP[source_account]
        for group_name, post_url in parse_daily_log(log_file):
            if post_url in seen:
                continue
            seen.add(post_url)
            jobs.append(
                BumpJob(
                    post_url=post_url,
                    group_name=group_name,
                    source_account=source_account,
                    comment_account=comment_account,
                    log_file=log_file.name,
                )
            )
    return jobs


async def find_comment_editor(page: Any) -> Any | None:
    for selector in COMMENT_EDITOR_SELECTORS:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(min(count, 8)):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible(timeout=300):
                    return candidate
            except Exception:
                continue
    return None


async def scroll_to_comment_area(page: Any) -> None:
    try:
        await page.mouse.wheel(0, 900)
        await page.wait_for_timeout(900)
    except Exception:
        pass


async def submit_comment(page: Any, editor: Any, text: str) -> bool:
    try:
        await editor.click(timeout=2000)
    except Exception:
        return False
    try:
        await page.keyboard.type(text, delay=40)
    except Exception:
        return False
    await page.wait_for_timeout(300)
    try:
        await page.keyboard.press("Enter")
    except Exception:
        return False
    return True


async def confirm_comment_posted(page: Any, text: str, timeout_ms: int = 6000) -> bool:
    """Kiem tra comment da xuat hien trong feed."""
    end_at = asyncio.get_event_loop().time() + timeout_ms / 1000.0
    needle = text.strip()
    while asyncio.get_event_loop().time() < end_at:
        try:
            # Tim comment vua dang trong cac article/comment container
            locator = page.locator(f"div[role='article']:has-text('{needle}'), div[aria-label*='Comment' i]:has-text('{needle}')")
            if await locator.count() > 0:
                return True
        except Exception:
            pass
        await page.wait_for_timeout(400)
    return False


async def process_job(page: Any, job: BumpJob, dry_run: bool) -> BumpResult:
    try:
        await page.goto(job.post_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as exc:
        return BumpResult(job, "error", f"Khong mo duoc link: {exc}")

    await page.wait_for_timeout(1800)
    await scroll_to_comment_area(page)

    editor = await find_comment_editor(page)
    if editor is None:
        # Thu scroll them 1 lan nua
        await scroll_to_comment_area(page)
        editor = await find_comment_editor(page)
    if editor is None:
        return BumpResult(job, "error", "Khong tim thay o comment")

    if dry_run:
        return BumpResult(job, "dry_run", "Tim thay o comment, bo qua buoc comment that")

    ok = await submit_comment(page, editor, COMMENT_TEXT)
    if not ok:
        return BumpResult(job, "error", "Khong go duoc comment")

    confirmed = await confirm_comment_posted(page, COMMENT_TEXT)
    if confirmed:
        return BumpResult(job, "commented", "Da comment '.'")
    return BumpResult(job, "uncertain", "Da bam Enter nhung chua xac nhan duoc comment hien thi")


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
    now = datetime.now()
    target_date = args.date or now.strftime("%Y%m%d")
    slot = resolve_slot(now, args.slot)
    if not slot:
        windows = ", ".join(f"{s} ({w[0]//60:02d}:{w[0]%60:02d}-{w[1]//60:02d}:{w[1]%60:02d})" for s, w in SLOT_WINDOWS.items())
        print(f"[INFO] Ngoai khung gio bump [{windows}]. Gio hien tai: {now.strftime('%H:%M')}. Thoat.")
        return 0

    jobs = build_jobs(target_date, slot=slot)
    if not jobs:
        print(f"[INFO] Khong co bai viet nao trong daily_log ngay {target_date} cho slot {slot}.")
        return 0

    pending = list(jobs)

    print(f"[INFO] Ngay xu ly: {target_date}, slot: {slot}")
    print(f"[INFO] Tong link trong log slot {slot}: {len(jobs)}, se comment lai tat ca (khong dedupe)")
    if args.max_posts > 0:
        pending = pending[: args.max_posts]
        print(f"[INFO] Gioi han --max-posts: chi xu ly {len(pending)} bai dau tien")

    # Group theo account comment de mo browser 1 lan cho moi account
    by_account: dict[str, list[BumpJob]] = {}
    for job in pending:
        by_account.setdefault(job.comment_account, []).append(job)

    results: list[BumpResult] = []
    for account_config_path, account_jobs in by_account.items():
        account_id = Path(account_config_path).stem.replace("account_", "")
        print(f"\n[INFO] Dang comment bang account: {account_id} ({len(account_jobs)} bai)")
        account_config = load_account_config(resolve_from_base(account_config_path), require_status=False)
        profile_dir = resolve_from_base(account_config.profile_dir)
        options = runtime_options(headless=args.headless)
        context = await build_context(profile_dir=profile_dir, account_config=account_config, options=options)
        page = await context.new_page()
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
        try:
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
            await ensure_logged_in(page, account_config, "https://www.facebook.com/", options)

            for position, job in enumerate(account_jobs, start=1):
                print(f"[INFO] ({position}/{len(account_jobs)}) [{job.source_account}->{account_id}] {job.group_name}")
                print(f"       {job.post_url}")
                result = await process_job(page, job, dry_run=args.dry_run)
                results.append(result)
                print(f"[RESULT] {result.status}: {result.detail}")
                if result.status in ("commented", "uncertain", "error"):
                    append_bump_log(target_date, result)
                if position < len(account_jobs):
                    await page.wait_for_timeout(COMMENT_DELAY_SECONDS * 1000)
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await close_context(context)

    commented = [item for item in results if item.status == "commented"]
    uncertain = [item for item in results if item.status == "uncertain"]
    failed = [item for item in results if item.status == "error"]
    dry_run = [item for item in results if item.status == "dry_run"]

    print("\n===== Comment Bump Summary =====")
    print(f"commented={len(commented)}, uncertain={len(uncertain)}, error={len(failed)}, dry_run={len(dry_run)}, total={len(results)}")
    if failed:
        print("Loi:")
        for item in failed:
            print(f"  - {item.job.group_name}: {item.detail}")
    if uncertain:
        print("Chua xac nhan:")
        for item in uncertain:
            print(f"  - {item.job.group_name}: {item.detail}")
    print("================================")

    return 0 if not failed else 1


def main() -> int:
    configure_stdout_for_windows()
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())

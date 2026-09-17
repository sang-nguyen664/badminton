from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
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
        close_all_pages,
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
LOCK_DIR = SOCIAL_DIR / "logs" / "locks"
GLOBAL_BUMP_LOCK = LOCK_DIR / "bump_active.lock"   # chi 1 luot bump chay tren toan he thong tai 1 thoi diem
LOCK_STALE_MINUTES = 45
POSTER_WAIT_MINUTES = 20   # bump cho poster toi da 20p; qua han coi nhu poster crash
LOCK_POLL_SECONDS = 15
COMMENT_TEXT = "."
COMMENT_DELAY_SECONDS = 12
MAX_ATTEMPTS = 3                  # so lan thu toi da cho 1 bai viet truoc khi ghi nhan error
RETRY_BACKOFF_SECONDS = [5, 10]   # thoi gian cho giua cac lan thu lai
BUMP_LOG_PREFIX = "comment_bump"

# Account map: log cua account nay thi dung account kia de comment.
ACCOUNT_MAP = {
    "linh": "config/account_sang.json",
    "sang": "config/account_linh.json",
    "chau": "config/account_linh.json",
}

COMMENT_EDITOR_SELECTORS = [
    "div[contenteditable='true'][role='textbox'][aria-label*='comment' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='bình luận' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='Viết bình luận' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='Write a comment' i]",
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
        choices=["", "1100", "1200", "1300", "1400", "1500", "1600", "1700", "1800"],
        help="Chi lay link tu dot dang nay. Mac dinh: tu dong theo gio hien tai.",
    )
    return parser.parse_args()


# Khung gio bump cho tung dot dang bai: (gio_bat_dau_bump, gio_ket_thuc_bump) tinh bang phut.
# Moi bai bump dung 3 lan: :15, :30, :45 sau gio dang roi ngung. Khung khong chong lan nhau.
SLOT_WINDOWS = {
    "1100": (11 * 60 + 15, 11 * 60 + 45),  # linh dang 11:00 -> sang bump 11:15-11:45
    "1200": (12 * 60 + 15, 12 * 60 + 45),  # sang dang 12:00 -> linh bump 12:15-12:45
    "1300": (13 * 60 + 15, 13 * 60 + 45),  # chau dang 13:00 -> linh bump 13:15-13:45
    "1400": (14 * 60 + 15, 14 * 60 + 45),  # linh dang 14:00 -> sang bump 14:15-14:45
    "1500": (15 * 60 + 15, 15 * 60 + 45),  # sang dang 15:00 -> linh bump 15:15-15:45
    "1600": (16 * 60 + 15, 16 * 60 + 45),  # chau dang 16:00 -> linh bump 16:15-16:45
    "1700": (17 * 60 + 15, 17 * 60 + 45),  # linh dang 17:00 -> sang bump 17:15-17:45
    "1800": (18 * 60 + 15, 18 * 60 + 45),  # sang dang 18:00 -> linh bump 18:15-18:45
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

def is_lock_active(path: Path, stale_minutes: int = LOCK_STALE_MINUTES) -> bool:
    """Lock con hieu luc neu ton tai va chua qua han stale; lock cu thi xoa."""
    if not path.exists():
        return False
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except Exception:
        return False
    if age_seconds > stale_minutes * 60:
        try:
            path.unlink()
        except Exception:
            pass
        return False
    return True


def touch_lock(path: Path) -> None:
    """Heartbeat: cap nhat mtime de lock khong bi coi la stale khi run con dang chay."""
    try:
        if path.exists():
            os.utime(path, None)
    except Exception:
        pass


def acquire_lock(path: Path) -> bool:
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        return True
    except Exception as exc:
        print(f"[WARN] Khong tao duoc lock {path.name}: {exc}")
        return False


def release_lock(path: Path) -> None:
    try:
        path.unlink()
    except Exception:
        pass

async def wait_for_poster(path: Path, account_id: str) -> None:
    """Cho den khi poster nha lock. Poster chay ~5-8p; lock qua POSTER_WAIT_MINUTES
    khong duoc nha coi nhu poster crash -> xoa lock va cho bump chay."""
    waited_seconds = 0
    while is_lock_active(path):
        if waited_seconds >= POSTER_WAIT_MINUTES * 60:
            print(f"[WARN] Poster lock cua {account_id} van con sau {POSTER_WAIT_MINUTES}p cho, coi nhu crash va tiep tuc bump.")
            release_lock(path)
            return
        print(f"[INFO] Account {account_id} dang dang bai, bump cho den khi dang xong... (da cho {waited_seconds // 60}p)")
        await asyncio.sleep(LOCK_POLL_SECONDS)
        waited_seconds += LOCK_POLL_SECONDS


async def wait_for_global_bump(path: Path) -> None:
    """Cho luot bump khac (account khac) chay xong.
    Bump lock co heartbeat moi ~20s nen qua 10 phut khong thay doi mtime = holder da chet."""
    waited_seconds = 0
    while is_lock_active(path, stale_minutes=10):
        print(f"[INFO] Mot luot bump khac dang chay, cho den khi xong... (da cho {waited_seconds // 60}p)")
        await asyncio.sleep(LOCK_POLL_SECONDS)
        waited_seconds += LOCK_POLL_SECONDS


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


def post_id_of(url: str) -> str:
    match = re.search(r"/(?:posts|permalink)/(\d+)", url)
    return match.group(1) if match else ""


async def find_largest_article(page: Any) -> Any | None:
    """Chon article co box va dien tich lon nhat tren trang (= bai viet chinh)."""
    locator = page.locator("div[role='article']")
    try:
        count = await locator.count()
    except Exception:
        return None
    best: Any | None = None
    best_area = 0.0
    for index in range(min(count, 15)):
        candidate = locator.nth(index)
        try:
            box = await candidate.bounding_box()
        except Exception:
            continue
        if box is None:
            continue
        area = box["width"] * box["height"]
        if area > best_area:
            best = candidate
            best_area = area
    return best


async def find_target_article(page: Any, post_id: str) -> Any | None:
    """Tim article chua dung bai viet muc tieu. Bat buoc phai co truoc khi comment -
    tranh comment nham bai khac tren newsfeed.
    Cach 1 (manh nhat): meta og:url chua post id -> trang dang render dung bai viet.
    Cach 2 (du phong): article chua link post id (bai da co comment thi de co)."""
    if not post_id:
        return None
    try:
        og = await page.locator("meta[property='og:url']").first.get_attribute("content", timeout=800)
    except Exception:
        og = None
    if og and post_id in og:
        return await find_largest_article(page)

    locator = page.locator("div[role='article']").filter(has=page.locator(f"a[href*='{post_id}']"))
    try:
        count = await locator.count()
    except Exception:
        return None
    best: Any | None = None
    best_area = 0.0
    for index in range(min(count, 15)):
        candidate = locator.nth(index)
        try:
            box = await candidate.bounding_box()
        except Exception:
            continue
        if box is None:
            continue
        area = box["width"] * box["height"]
        if area > best_area:
            best = candidate
            best_area = area
    return best


async def find_comment_editor(page: Any, anchor: Any | None = None) -> Any | None:
    """Tim o nhap comment tren trang. Co anchor (article bai viet muc tieu) thi chon o
    gan anchor nhat - tranh bam nham o comment cua bai khac hoac hop chat."""
    anchor_bottom = None
    if anchor is not None:
        try:
            box = await anchor.bounding_box()
            if box is not None:
                anchor_bottom = box["y"] + box["height"]
        except Exception:
            anchor_bottom = None
        if anchor_bottom is None:
            # Co anchor nhung bai viet khong hien thi (bi an/render loi) ->
            # khong duoc chon editor bua, tranh comment nham bai dau newsfeed.
            return None

    best: Any | None = None
    best_distance = float("inf")
    for selector in COMMENT_EDITOR_SELECTORS:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(min(count, 8)):
            candidate = locator.nth(index)
            try:
                box = await candidate.bounding_box()
            except Exception:
                continue
            if box is None:
                continue
            if anchor_bottom is None:
                return candidate
            distance = abs(box["y"] - anchor_bottom)
            if distance < best_distance:
                best = candidate
                best_distance = distance
    return best


async def scroll_to_comment_area(page: Any) -> None:
    try:
        await page.mouse.wheel(0, 900)
        await page.wait_for_timeout(900)
    except Exception:
        pass

COMMENTS_DISABLED_TOKENS = [
    "comments are turned off",
    "commenting is turned off",
    "bình luận đã bị tắt",
    "đã tắt bình luận",
    "không thể bình luận",
    "đang chờ phê duyệt",
    "pending review",
]

COMMENT_ACTION_SELECTORS = [
    "div[role='button'][aria-label='Bình luận']",
    "div[role='button'][aria-label='Comment']",
    "div[role='button'][aria-label*='Để lại bình luận']",
    "div[role='button'][aria-label*='Leave a comment']",
    "div[role='button'][aria-label*='Write a comment']",
]

OVERLAY_CLOSE_SELECTORS = [
    "div[role='dialog'] div[role='button'][aria-label='Đóng']",
    "div[role='dialog'] div[role='button'][aria-label='Close']",
    "div[role='dialog'] div[role='button']:has-text('Để sau')",
    "div[role='dialog'] div[role='button']:has-text('Not now')",
    "div[role='dialog'] div[role='button']:has-text('Không phải bây giờ')",
]


async def dismiss_overlays(page: Any) -> None:
    """Dong cac popup/dialog che phu (bat thong bao, cookie...) chan click vao o comment.
    KHONG bam Escape: Escape dong luon composer comment tren trang permalink."""
    for selector in OVERLAY_CLOSE_SELECTORS:
        locator = page.locator(selector)
        try:
            if await locator.count() > 0 and await locator.first.is_visible(timeout=200):
                await locator.first.click(timeout=1200)
                await page.wait_for_timeout(400)
        except Exception:
            continue


async def click_comment_action(scope: Any) -> bool:
    """Bam nut 'Binh luan' duoi bai viet de mo/focus o nhap comment."""
    for selector in COMMENT_ACTION_SELECTORS:
        locator = scope.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(min(count, 3)):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible(timeout=200):
                    await candidate.click(timeout=1500)
                    await page.wait_for_timeout(800)
                    return True
            except Exception:
                continue
    return False


async def detect_comments_disabled(page: Any) -> str:
    """Phat hien bai viet tat binh luan / cho duyet (retry vo ich)."""
    try:
        text = (await page.locator("body").inner_text())[:6000].lower()
    except Exception:
        return ""
    for token in COMMENTS_DISABLED_TOKENS:
        if token in text:
            return token
    return ""


async def submit_comment(page: Any, editor: Any, text: str) -> bool:
    try:
        await editor.scroll_into_view_if_needed(timeout=1500)
    except Exception:
        pass
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


async def confirm_comment_posted(page: Any, editor: Any, timeout_ms: int = 6000) -> bool:
    """Xac nhan comment da dang: o nhap bi xoa trang hoac bien mat sau khi Enter.
    (Cach cu tim has-text('.') luon dung vi moi bai viet deu co dau cham.)"""
    end_at = asyncio.get_event_loop().time() + timeout_ms / 1000.0
    while asyncio.get_event_loop().time() < end_at:
        try:
            text = (await editor.inner_text()).strip()
            if text == "":
                return True
        except Exception:
            # Editor bien mat do Facebook re-render sau khi dang thanh cong
            return True
        await page.wait_for_timeout(400)
    return False


async def process_job(page: Any, job: BumpJob, dry_run: bool) -> BumpResult:
    try:
        await page.goto(job.post_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as exc:
        return BumpResult(job, "error", f"Khong mo duoc link: {exc}")

    await page.wait_for_timeout(1800)
    await dismiss_overlays(page)

    # Bat buoc xac minh trang dang hien dung bai viet muc tieu truoc khi tim o comment,
    # tranh comment nham vao bai dau newsfeed khi trang render loi/bi throttle.
    # Link chua post id nam trong phan comment load lazy -> poll toi da 10s kem scroll kich render.
    post_id = post_id_of(job.post_url)
    article = None
    find_deadline = time.monotonic() + 10.0
    while time.monotonic() < find_deadline:
        article = await find_target_article(page, post_id)
        if article is not None:
            break
        await scroll_to_comment_area(page)
    if article is None:
        return BumpResult(job, "error", "Khong tim thay bai viet tren trang (trang loi hoac bi throttle)")
    # Dua bai viet vao viewport va bat buoc co bounding box hop le truoc khi comment
    try:
        await article.scroll_into_view_if_needed(timeout=3000)
        await page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        article_box = await article.bounding_box()
    except Exception:
        article_box = None
    if article_box is None:
        return BumpResult(job, "error", "Bai viet khong hien thi tren trang (bi an hoac render loi)")

    # Editor render cham hon article -> poll toi da 8s, lan dau bam nut "Binh luan" de kich mo
    editor = None
    editor_deadline = time.monotonic() + 8.0
    clicked_action = False
    while time.monotonic() < editor_deadline:
        editor = await find_comment_editor(page, anchor=article)
        if editor is not None:
            break
        if not clicked_action:
            await click_comment_action(article)
            clicked_action = True
        await page.wait_for_timeout(800)
    if editor is None:
        disabled_reason = await detect_comments_disabled(page)
        if disabled_reason:
            return BumpResult(job, "restricted", f"Bai viet khong cho binh luan ({disabled_reason})")
        return BumpResult(job, "error", "Khong tim thay o comment trong bai viet")

    if dry_run:
        return BumpResult(job, "dry_run", "Tim thay dung bai viet + o comment, bo qua buoc comment that")

    ok = await submit_comment(page, editor, COMMENT_TEXT)
    if not ok:
        return BumpResult(job, "error", "Khong go duoc comment")

    confirmed = await confirm_comment_posted(page, editor)
    if confirmed:
        return BumpResult(job, "commented", "Da comment '.'")
    return BumpResult(job, "uncertain", "Da bam Enter nhung chua xac nhan duoc comment hien thi")


async def process_job_with_retry(page: Any, job: BumpJob, dry_run: bool) -> BumpResult:
    """Thu toi da MAX_ATTEMPTS lan cho 1 bai viet. Chi retry khi error (chua dang duoc gi);
    khong retry 'uncertain' vi co the da dang roi, retry se bi trung comment."""
    last_result: BumpResult | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = await process_job(page, job, dry_run)
        if result.status != "error":
            if attempt > 1 and result.status in ("commented", "dry_run"):
                result.detail = f"{result.detail} (thanh cong o lan thu {attempt})"
            return result
        last_result = result
        if attempt < MAX_ATTEMPTS:
            backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            print(f"[INFO] Loi lan {attempt}/{MAX_ATTEMPTS}, thu lai sau {backoff}s...")
            await page.wait_for_timeout(backoff * 1000)

    last_result.detail = f"{last_result.detail} (da thu {MAX_ATTEMPTS} lan)"
    return last_result


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
    # Lock toan cuc: chi 1 luot bump chay tai 1 thoi diem (account nay xong moi toi account kia)
    await wait_for_global_bump(GLOBAL_BUMP_LOCK)
    acquire_lock(GLOBAL_BUMP_LOCK)
    try:
      for account_config_path, account_jobs in by_account.items():
        account_id = Path(account_config_path).stem.replace("account_", "")
        poster_lock = LOCK_DIR / f"poster_{account_id}.lock"
        await wait_for_poster(poster_lock, account_id)
        bump_lock = LOCK_DIR / f"bump_{account_id}.lock"
        if not acquire_lock(bump_lock):
            print(f"\n[WARN] Khong tao duoc lock cho account {account_id}, bo qua de tranh xung dot profile.")
            continue
        print(f"\n[INFO] Dang comment bang account: {account_id} ({len(account_jobs)} bai)")
        account_config = load_account_config(resolve_from_base(account_config_path), require_status=False)
        profile_dir = resolve_from_base(account_config.profile_dir)
        options = runtime_options(headless=args.headless)
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
        try:
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
            await ensure_logged_in(page, account_config, "https://www.facebook.com/", options)

            for position, job in enumerate(account_jobs, start=1):
                print(f"[INFO] ({position}/{len(account_jobs)}) [{job.source_account}->{account_id}] {job.group_name}")
                print(f"       {job.post_url}")
                result = await process_job_with_retry(page, job, dry_run=args.dry_run)
                results.append(result)
                print(f"[RESULT] {result.status}: {result.detail}")
                if result.status in ("commented", "uncertain", "error", "restricted"):
                    append_bump_log(target_date, result)
                touch_lock(GLOBAL_BUMP_LOCK)
                touch_lock(bump_lock)
                if position < len(account_jobs):
                    await page.wait_for_timeout(COMMENT_DELAY_SECONDS * 1000)
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await close_context(context)
            release_lock(bump_lock)
    finally:
        release_lock(GLOBAL_BUMP_LOCK)

    commented = [item for item in results if item.status == "commented"]
    uncertain = [item for item in results if item.status == "uncertain"]
    failed = [item for item in results if item.status == "error"]
    restricted = [item for item in results if item.status == "restricted"]
    dry_run = [item for item in results if item.status == "dry_run"]

    print("\n===== Comment Bump Summary =====")
    print(f"commented={len(commented)}, uncertain={len(uncertain)}, error={len(failed)}, restricted={len(restricted)}, dry_run={len(dry_run)}, total={len(results)}")
    if restricted:
        print("Bai viet tat binh luan / cho duyet:")
        for item in restricted:
            print(f"  - {item.job.group_name}: {item.detail}")
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

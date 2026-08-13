from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None

from playwright.async_api import (
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)


def configure_stdout_for_windows() -> None:
    """Avoid cp1252 crashes when printing Vietnamese names to Windows terminal."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                continue


BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
DEFAULT_PROFILE_DIR = "data/playwright-profile"
DEFAULT_ACCOUNT_CONFIG = "config/account_config.json"
DEFAULT_ACCOUNTS_DIR = "config"
DEFAULT_POSTS_CONFIG = "config/posts.json"
DEFAULT_TARGETS_CONFIG = "config/targets.json"
DEFAULT_JOB_ATTEMPTS = 2
DEBUG_DIR = BASE_DIR / "logs" / "composer-debug"
IMAGE_CACHE_DIR = BASE_DIR / "logs" / "optimized-image-cache"

COMPOSER_ACTIVATION_SELECTORS = [
    "div[role='button'][aria-label*='Write something']",
    'div[role="button"][aria-label*="What\'s on your mind"]',
    "div[role='button'][aria-label*='Bạn viết gì đi']",
    "div[role='button'][aria-label*='Ban viet gi di']",
    "span:has-text('Write something')",
    'span:has-text("What\'s on your mind")',
    "span:has-text('Bạn viết gì đi')",
    "span:has-text('Ban viet gi di')",
]

COMPOSER_EDITOR_SELECTORS = [
    "[contenteditable='true'][role='textbox']:visible",
    "div[role='textbox'][contenteditable='true']:visible",
    "div[role='textbox']:visible",
    "[contenteditable='true']:visible",
    "textarea:visible",
    "label textarea:visible",
]

COMPOSER_ROOT_SELECTORS = [
    "div[role='dialog']:visible",
    "div[aria-modal='true']:visible",
    "div[aria-label='Create post']:visible",
    "div[aria-label='Tạo bài viết']:visible",
    "div[aria-label='Tao bai viet']:visible",
]

COMPOSER_HEADING_SELECTORS = [
    "div[role='heading']:has-text('Create post')",
    "div[role='heading']:has-text('Tạo bài viết')",
    "div[role='heading']:has-text('Tao bai viet')",
    "span:has-text('Create post')",
    "span:has-text('Tạo bài viết')",
    "span:has-text('Tao bai viet')",
]

POST_BUTTON_SELECTORS = [
    "div[role='button'][aria-label='Post']",
    "div[role='button'][aria-label*='Post']",
    "div[role='button'][aria-label='Đăng']",
    "div[role='button'][aria-label*='Đăng']",
    "div[role='button'][aria-label='Dang']",
    "div[role='button'][aria-label*='Dang']",
    "button:has-text('Post')",
    "button:has-text('Đăng')",
    "button:has-text('Dang')",
    "span:has-text('Post')",
    "span:has-text('Đăng')",
    "span:has-text('Dang')",
]

FILE_INPUT_SELECTORS = [
    "input[type='file'][accept*='image']",
    "input[type='file'][accept*='video']",
    "input[type='file'][accept*='/*']",
    "input[type='file']",
]

PHOTO_VIDEO_CONTROL_SELECTORS = [
    "div[role='button'][aria-label*='Photo/video']",
    "div[role='button'][aria-label*='Photo']",
    "div[role='button'][aria-label*='Ảnh/video']",
    "div[role='button'][aria-label*='Anh/video']",
    "div[role='button'][aria-label*='Ảnh']",
    "div[role='button'][aria-label*='Anh']",
    "span:has-text('Photo/video')",
    "span:has-text('Ảnh/video')",
    "span:has-text('Anh/video')",
]

IMAGE_ATTACHMENT_READY_SELECTORS = [
    "img[src^='blob:']:visible",
    "img[src*='scontent']:visible",
    "div[aria-label*='Remove photo']:visible",
    "div[aria-label*='Xóa ảnh']:visible",
    "div[aria-label*='Xoa anh']:visible",
]

COMPOSER_OPEN_STATE_SELECTORS = [
    *COMPOSER_EDITOR_SELECTORS,
    *COMPOSER_HEADING_SELECTORS,
    *POST_BUTTON_SELECTORS,
    *PHOTO_VIDEO_CONTROL_SELECTORS,
]

COMPOSER_DIALOG_SELECTOR = "div:is([role='dialog'], [aria-modal='true'])"

BLOCKER_TOKENS = [
    "checkpoint",
    "captcha",
    "security check",
    "two_factor",
    "two_step_verification",
    "temporarily blocked",
    "suspended",
    "account restricted",
    "rate limit",
]


@dataclass(frozen=True)
class Job:
    job_id: str
    source_file: str
    source_name: str
    post_id: str
    post_name: str
    target_id: str
    target_name: str
    url: str
    content: str
    image_path: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.post_name} -> {self.target_name}"


@dataclass(frozen=True)
class AccountConfig:
    account_id: str
    display_name: str
    status: str
    profile_dir: str
    browser_channel: str
    chrome_profile_directory: str
    facebook_login: str
    facebook_password: str
    source_path: str


@dataclass
class RuntimeOptions:
    headless: bool
    dry_run: bool
    debug_composer: bool
    pause_for_debugger: bool
    performance_mode: bool
    concurrency: int


@dataclass
class StageStats:
    values: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        self.values.append(value)


@dataclass
class JobMetrics:
    job_id: str
    job_name: str
    source_file: str
    worker_id: int
    stages: dict[str, float] = field(default_factory=dict)
    retries: int = 0
    browser_restarts: int = 0
    submit_clicked: bool = False
    submit_uncertain: bool = False
    success: bool = False
    failure_reason: str = ""
    detail_times: dict[str, float] = field(default_factory=dict)
    detail_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class BatchMetrics:
    started_at: float = field(default_factory=time.monotonic)
    completed_at: float = 0.0
    job_metrics: list[JobMetrics] = field(default_factory=list)
    retry_count: int = 0
    browser_restart_count: int = 0

    def finish(self) -> None:
        self.completed_at = time.monotonic()

    @property
    def total_seconds(self) -> float:
        end = self.completed_at if self.completed_at else time.monotonic()
        return max(0.0, end - self.started_at)


@dataclass
class ImageAuditEntry:
    source_path: str
    optimized_path: str
    format: str
    width: int
    height: int
    source_bytes: int
    optimized_bytes: int


@dataclass
class RunState:
    context: BrowserContext
    account: AccountConfig
    options: RuntimeOptions
    stop_new_jobs: asyncio.Event
    stop_reason: str = ""


@dataclass
class WorkerPageState:
    worker_id: int
    page: Page
    upload_strategy: str = "auto"
    popup_none_streak: int = 0
    last_composer_signature: str = ""


class StageTimer:
    def __init__(self, metrics: JobMetrics, stage_name: str) -> None:
        self.metrics = metrics
        self.stage_name = stage_name
        self.start = 0.0

    def __enter__(self) -> "StageTimer":
        self.start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.metrics.stages[self.stage_name] = time.monotonic() - self.start


def add_detail_time(metrics: JobMetrics, key: str, seconds: float) -> None:
    if seconds <= 0:
        return
    metrics.detail_times[key] = metrics.detail_times.get(key, 0.0) + seconds


def inc_detail_count(metrics: JobMetrics, key: str, step: int = 1) -> None:
    metrics.detail_counts[key] = metrics.detail_counts.get(key, 0) + step


async def tracked_sleep(page: Page, wait_ms: int, metrics: JobMetrics, detail_key: str) -> None:
    start = time.monotonic()
    await page.wait_for_timeout(wait_ms)
    add_detail_time(metrics, detail_key, time.monotonic() - start)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dang bai Facebook vao cac khung gio dinh san.")
    parser.add_argument(
        "--account-config",
        default="",
        help="File account JSON cu the. Neu bo trong se chon account enabled trong --accounts-dir.",
    )
    parser.add_argument(
        "--accounts-dir",
        default=DEFAULT_ACCOUNTS_DIR,
        help="Thu muc chua account_*.json.",
    )
    parser.add_argument(
        "--posts",
        default=DEFAULT_POSTS_CONFIG,
        help="Duong dan file posts JSON.",
    )
    parser.add_argument(
        "--targets",
        action="append",
        default=[],
        help="Co the truyen nhieu lan. Vi du: --targets config/targets.json --targets config/targets_admin.json",
    )
    parser.add_argument(
        "--targets-files",
        default="",
        help="Danh sach file targets, ngan cach boi dau phay.",
    )
    parser.add_argument(
        "--profile-dir",
        default="",
        help="Ghi de profile dir.",
    )
    parser.add_argument("--headless", action="store_true", help="Chay an browser.")
    parser.add_argument("--dry-run", action="store_true", help="Khong bam Post.")
    parser.add_argument("--debug-composer", action="store_true", help="Dump composer debug info.")
    parser.add_argument("--pause-for-debugger", action="store_true", help="Tam dung cho inspect sau khi mo composer.")
    parser.add_argument("--performance-mode", action="store_true", help="Bat che do toi uu wait/slow_mo.")
    parser.add_argument("--concurrency", type=int, default=1, help="So worker page (1 hoac 2).")
    return parser.parse_args()


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_from_base(path_value: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def natural_sort_key(path: Path) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def list_asset_images() -> list[Path]:
    if not ASSETS_DIR.exists():
        return []
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    image_paths = [
        path.resolve()
        for path in ASSETS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in allowed_suffixes
    ]
    return sorted(image_paths, key=natural_sort_key)


def build_target_image_map(targets: dict[str, dict[str, Any]]) -> dict[str, str]:
    asset_images = list_asset_images()
    if not asset_images:
        return {}
    enabled_target_ids = [target_id for target_id, target in targets.items() if target["enabled"]]
    if not enabled_target_ids:
        return {}
    target_images: dict[str, str] = {}
    for index, target_id in enumerate(enabled_target_ids):
        image_path = asset_images[index % len(asset_images)]
        target_images[target_id] = str(image_path)
    return target_images


def normalize_profile_token(path_value: str) -> str:
    return path_value.replace("\\", "/").strip().lower()


def infer_account_id_from_profile(profile_value: str) -> str:
    token = normalize_profile_token(profile_value)
    match = re.search(r"(account_[a-z0-9_-]+)", token)
    return match.group(1) if match else ""


def validate_account_schema(payload: Any, path: Path, require_status: bool = True) -> AccountConfig:
    if not isinstance(payload, dict):
        raise ValueError(f"File account khong dung schema object: {path}")

    account_id = str(payload.get("account_id", "")).strip()
    status = str(payload.get("status", "")).strip().lower()
    if not account_id and not require_status:
        account_id = path.stem
    if not status and not require_status:
        status = "enabled"

    display_name = str(payload.get("display_name", "")).strip() or account_id
    profile_dir = str(payload.get("profile_dir", "")).strip()
    browser_channel = str(payload.get("browser_channel", "chromium")).strip() or "chromium"
    chrome_profile_directory = str(payload.get("chrome_profile_directory", "")).strip()
    facebook_login = (
        str(payload.get("facebook_login", "")).strip()
        or str(payload.get("facebook_phone", "")).strip()
        or str(payload.get("facebook_email", "")).strip()
    )
    facebook_password = str(payload.get("facebook_password", "")).strip()

    missing_fields: list[str] = []
    if not account_id:
        missing_fields.append("account_id")
    if not profile_dir:
        missing_fields.append("profile_dir")
    if not browser_channel:
        missing_fields.append("browser_channel")
    if require_status and not status:
        missing_fields.append("status")
    if missing_fields:
        raise ValueError(f"File account thieu truong bat buoc {missing_fields}: {path}")

    if status not in {"enabled", "disabled"}:
        raise ValueError(f"status khong hop le trong {path}: '{status}'. Chi nhan enabled/disabled.")

    profile_account_id = infer_account_id_from_profile(profile_dir)
    if profile_account_id and account_id.lower().startswith("account_") and profile_account_id != account_id.lower():
        raise ValueError(
            f"profile_dir co dau hieu tro nham account khac ({profile_dir}) voi account_id={account_id} trong {path}"
        )

    return AccountConfig(
        account_id=account_id,
        display_name=display_name,
        status=status,
        profile_dir=profile_dir,
        browser_channel=browser_channel,
        chrome_profile_directory=chrome_profile_directory,
        facebook_login=facebook_login,
        facebook_password=facebook_password,
        source_path=str(path),
    )


def load_account_config(path: Path, require_status: bool = True) -> AccountConfig:
    try:
        payload = read_json_file(path)
    except json.JSONDecodeError as exc:
        raise ValueError(f"File JSON loi ({path}): {exc}") from exc
    return validate_account_schema(payload, path, require_status=require_status)


def should_skip_account_file(path: Path) -> bool:
    name = path.name.lower()
    if name == "account_example.json":
        return True
    if not re.match(r"^account_.*\.json$", name):
        return True
    if name.endswith(".bak") or ".bak." in name:
        return True
    for marker in ("probe", "test", "tmp", "temp"):
        if marker in name:
            return True
    return False


def discover_account_files(accounts_dir: Path) -> tuple[list[Path], list[Path]]:
    if not accounts_dir.exists() or not accounts_dir.is_dir():
        raise ValueError(f"Thu muc account khong ton tai: {accounts_dir}")

    selected: list[Path] = []
    skipped: list[Path] = []
    for path in sorted(accounts_dir.glob("account_*.json"), key=lambda item: item.name.lower()):
        if should_skip_account_file(path):
            skipped.append(path)
            continue
        selected.append(path)
    return selected, skipped


def assert_unique_accounts(accounts: list[AccountConfig]) -> None:
    by_id: dict[str, str] = {}
    by_profile: dict[str, str] = {}
    for account in accounts:
        account_id_key = account.account_id.lower()
        profile_key = normalize_profile_token(account.profile_dir)
        if account_id_key in by_id:
            raise ValueError(f"Duplicate account_id '{account.account_id}' giua {by_id[account_id_key]} va {account.source_path}")
        by_id[account_id_key] = account.source_path
        if profile_key in by_profile:
            raise ValueError(f"Duplicate profile_dir '{account.profile_dir}' giua {by_profile[profile_key]} va {account.source_path}")
        by_profile[profile_key] = account.source_path


def print_account_scan(accounts: list[AccountConfig], skipped: list[Path], selected: AccountConfig | None) -> None:
    print("[INFO] Ket qua quet account trong config:")
    for account in accounts:
        state = "selected" if selected is not None and selected.source_path == account.source_path else "disabled"
        if account.status != "enabled":
            state = "disabled"
        print(
            "[INFO] "
            f"account_id={account.account_id} display_name={account.display_name} status={account.status} "
            f"profile={Path(account.profile_dir).as_posix()} decision={state}"
        )
    for path in skipped:
        print(f"[INFO] skip_account_file={path.name}")


def select_enabled_account_from_dir(accounts_dir: Path) -> AccountConfig:
    account_files, skipped_files = discover_account_files(accounts_dir)
    if not account_files:
        raise ValueError(f"Khong tim thay file account_*.json hop le trong {accounts_dir}")

    accounts = [load_account_config(path, require_status=True) for path in account_files]
    assert_unique_accounts(accounts)

    enabled_accounts = [account for account in accounts if account.status == "enabled"]
    if not enabled_accounts:
        print_account_scan(accounts, skipped_files, selected=None)
        raise RuntimeError("Không có account Facebook nào đang được bật.")
    if len(enabled_accounts) > 1:
        print_account_scan(accounts, skipped_files, selected=None)
        raise RuntimeError("Có nhiều account đang enabled. Chỉ được bật một account cho mỗi lần chạy.")

    selected = enabled_accounts[0]
    print_account_scan(accounts, skipped_files, selected=selected)
    return selected


def select_account_config(args: argparse.Namespace) -> AccountConfig:
    if args.account_config.strip():
        account_path = resolve_from_base(args.account_config.strip())
        if not account_path.exists():
            raise ValueError(f"Khong tim thay file account config: {account_path}")
        account = load_account_config(account_path, require_status=False)
        print(
            "[INFO] "
            f"account_id={account.account_id} display_name={account.display_name} status={account.status} "
            f"profile={Path(account.profile_dir).as_posix()} decision=selected-by-flag"
        )
        return account

    accounts_dir = resolve_from_base(args.accounts_dir)
    return select_enabled_account_from_dir(accounts_dir)


def collect_target_files(args: argparse.Namespace) -> list[Path]:
    values: list[str] = []
    values.extend(args.targets)
    if args.targets_files.strip():
        values.extend([item.strip() for item in args.targets_files.split(",") if item.strip()])
    if not values:
        values = [DEFAULT_TARGETS_CONFIG]

    result: list[Path] = []
    seen: set[str] = set()
    for raw in values:
        path = resolve_from_base(raw)
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def load_targets(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json_file(path)
    raw_targets = payload.get("targets", [])
    targets: dict[str, dict[str, Any]] = {}
    for index, raw_target in enumerate(raw_targets, start=1):
        target_id = str(raw_target.get("id") or f"target-{index}").strip()
        name = str(raw_target.get("name") or target_id).strip()
        url = str(raw_target.get("url") or "").strip()
        enabled = bool(raw_target.get("enabled", True))
        if not url:
            raise ValueError(f"Target '{target_id}' thieu url trong {path.name}.")
        if target_id in targets:
            raise ValueError(f"Target id bi trung '{target_id}' trong {path.name}.")
        targets[target_id] = {"name": name, "url": url, "enabled": enabled}
    return targets


def load_posts_for_targets(posts_path: Path, targets: dict[str, dict[str, Any]], source_file: Path) -> list[Job]:
    payload = read_json_file(posts_path)
    raw_posts = payload.get("posts", [])
    jobs: list[Job] = []
    base_dir = posts_path.parent
    enabled_target_ids = [target_id for target_id, target in targets.items() if target["enabled"]]
    target_image_map = build_target_image_map(targets)

    for post_index, raw_post in enumerate(raw_posts, start=1):
        post_id = str(raw_post.get("id") or f"post-{post_index}").strip()
        post_name = str(raw_post.get("name") or post_id).strip()
        content = str(raw_post.get("content") or "")
        image_path_raw = str(raw_post.get("image_path") or "").strip()
        post_enabled = bool(raw_post.get("enabled", True))
        raw_target_ids = raw_post.get("target_ids", [])
        target_ids = [str(value).strip() for value in raw_target_ids if str(value).strip()]

        if not content.strip():
            raise ValueError(f"Post '{post_name}' thieu content.")
        if not enabled_target_ids:
            raise ValueError(f"Khong co target nao dang bat trong {source_file.name}.")
        if not target_ids:
            target_ids = enabled_target_ids.copy()

        image_path: str | None = None
        if image_path_raw:
            image_file = Path(image_path_raw)
            if not image_file.is_absolute():
                image_file = (base_dir / image_file).resolve()
            else:
                image_file = image_file.resolve()
            if not image_file.exists() and not target_image_map:
                raise ValueError(f"Post '{post_name}' co image_path khong ton tai: '{image_file}'")
            if image_file.exists():
                image_path = str(image_file)

        for target_id in target_ids:
            target = targets.get(target_id)
            if target is None:
                raise ValueError(f"Post '{post_name}' tham chieu target_id khong ton tai: '{target_id}'.")
            if not target["enabled"] or not post_enabled:
                continue
            target_image_path = target_image_map.get(target_id, image_path)
            source_name = source_file.name
            job_id = f"{source_name}:{post_id}:{target_id}"
            jobs.append(
                Job(
                    job_id=job_id,
                    source_file=str(source_file),
                    source_name=source_name,
                    post_id=post_id,
                    post_name=post_name,
                    target_id=target_id,
                    target_name=target["name"],
                    url=target["url"],
                    content=content,
                    image_path=target_image_path,
                )
            )

    return jobs


def validate_duplicates(all_jobs: list[Job], url_sources: dict[str, list[str]]) -> None:
    duplicate_urls = {url: srcs for url, srcs in url_sources.items() if len(srcs) > 1}
    if duplicate_urls:
        lines = []
        for url, srcs in duplicate_urls.items():
            lines.append(f"{url} <- {', '.join(srcs)}")
        raise ValueError("Target URL bi trung giua cac file targets:\n" + "\n".join(lines))

    seen_job_keys: dict[tuple[str, str], str] = {}
    for job in all_jobs:
        key = (job.url.strip().lower(), job.post_name.strip().lower())
        if key in seen_job_keys:
            raise ValueError(
                "Job bi trung (url + post_name): "
                f"'{job.post_name}' / {job.url} giua {seen_job_keys[key]} va {job.source_name}"
            )
        seen_job_keys[key] = job.source_name


def load_jobs(posts_path: Path, target_files: list[Path]) -> tuple[list[Job], dict[str, int]]:
    all_jobs: list[Job] = []
    counts_by_source: dict[str, int] = {}
    url_sources: dict[str, list[str]] = {}

    for target_file in target_files:
        targets = load_targets(target_file)
        for target_id, target in targets.items():
            if not target["enabled"]:
                continue
            url_key = target["url"].strip().lower()
            url_sources.setdefault(url_key, []).append(f"{target_file.name}:{target_id}")

        jobs = load_posts_for_targets(posts_path, targets, target_file)
        all_jobs.extend(jobs)
        counts_by_source[target_file.name] = len(jobs)

    validate_duplicates(all_jobs, url_sources)
    return all_jobs, counts_by_source


def validate_effective_profile(account: AccountConfig, profile_path: Path) -> None:
    account_profile_id = infer_account_id_from_profile(account.profile_dir)
    effective_profile_id = infer_account_id_from_profile(str(profile_path))
    if effective_profile_id and account.account_id.lower().startswith("account_") and effective_profile_id != account.account_id.lower():
        raise ValueError(f"profile path dang dung co dau hieu thuoc account khac: {profile_path} (account_id={account.account_id})")
    if account_profile_id and account.account_id.lower().startswith("account_") and account_profile_id != account.account_id.lower():
        raise ValueError(f"profile_dir trong account config co dau hieu tro nham account khac: {account.profile_dir}")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def slugify_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "debug"


async def is_login_page(page: Page) -> bool:
    return await page.locator("input[name='email']").count() > 0 and await page.locator("input[name='pass']").count() > 0


async def find_first_visible(
    scope: Any,
    selectors: list[str],
    metrics: JobMetrics | None = None,
    check_timeout_ms: int = 40,
) -> Locator | None:
    for selector in selectors:
        if metrics is not None:
            inc_detail_count(metrics, "selector.try")
        locator = scope.locator(selector)
        start = time.monotonic()
        count = await locator.count()
        if metrics is not None:
            add_detail_time(metrics, "selector.count_wait", time.monotonic() - start)
        if count == 0:
            continue
        try:
            start = time.monotonic()
            visible = await locator.first.is_visible(timeout=check_timeout_ms)
            if metrics is not None:
                add_detail_time(metrics, "selector.visible_wait", time.monotonic() - start)
            if visible:
                return locator.first
        except Exception:
            continue
    return None


async def find_clickable(
    scope: Any,
    selectors: list[str],
    timeout_ms: int = 3500,
    metrics: JobMetrics | None = None,
) -> Locator | None:
    end_at = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < end_at:
        candidate = await find_first_visible(scope, selectors, metrics=metrics)
        if candidate is not None:
            return candidate
        if metrics is not None:
            inc_detail_count(metrics, "selector.poll")
        await asyncio.sleep(0.06)
    if metrics is not None:
        inc_detail_count(metrics, "selector.timeout_exhausted")
    return None


async def find_visible_locator(scope: Any, selectors: list[str], timeout_ms: int = 1200) -> Locator | None:
    for selector in selectors:
        locator = scope.locator(selector)
        try:
            await locator.first.wait_for(state="visible", timeout=timeout_ms)
            return locator.first
        except PlaywrightTimeoutError:
            continue
    return None


async def read_bbox(locator: Locator) -> dict[str, float]:
    try:
        bbox = await locator.bounding_box()
    except Exception:
        bbox = None
    if not bbox:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    return {
        "x": float(bbox.get("x", 0.0)),
        "y": float(bbox.get("y", 0.0)),
        "width": float(bbox.get("width", 0.0)),
        "height": float(bbox.get("height", 0.0)),
    }


async def is_editor_interactable(editor: Locator, composer_scope: Any | None = None) -> bool:
    try:
        await editor.wait_for(state="visible", timeout=180)
    except PlaywrightTimeoutError:
        return False

    if composer_scope is not None:
        scope_bbox = await read_bbox(composer_scope)
        editor_bbox = await read_bbox(editor)
        if editor_bbox["width"] < 24 or editor_bbox["height"] < 14:
            return False
        if (
            editor_bbox["x"] + editor_bbox["width"] < scope_bbox["x"]
            or editor_bbox["x"] > scope_bbox["x"] + scope_bbox["width"]
            or editor_bbox["y"] + editor_bbox["height"] < scope_bbox["y"]
            or editor_bbox["y"] > scope_bbox["y"] + scope_bbox["height"]
        ):
            return False

    try:
        if await editor.is_editable(timeout=180):
            return True
    except Exception:
        pass

    try:
        return bool(
            await editor.evaluate(
                """
                (element) => {
                    if (!element || !element.getAttribute) return false;
                    const contenteditable = element.getAttribute('contenteditable');
                    if (contenteditable && contenteditable.toLowerCase() === 'true') return true;
                    if ('readOnly' in element && element.readOnly) return false;
                    if ('disabled' in element && element.disabled) return false;
                    const role = (element.getAttribute('role') || '').toLowerCase();
                    return role === 'textbox' || element.tagName === 'TEXTAREA';
                }
                """
            )
        )
    except Exception:
        return False


async def find_scoped_editor(scope: Any, timeout_ms: int = 300) -> Locator | None:
    for selector in COMPOSER_EDITOR_SELECTORS:
        group = scope.locator(selector)
        count = min(await group.count(), 8)
        for index in range(count):
            editor = group.nth(index)
            try:
                await editor.wait_for(state="visible", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                continue
            if await is_editor_interactable(editor, composer_scope=scope):
                return editor
    return None


async def count_visible_matches(scope: Any, selectors: list[str], max_per_selector: int = 8) -> int:
    total = 0
    for selector in selectors:
        locator = scope.locator(selector)
        count = min(await locator.count(), max_per_selector)
        for index in range(count):
            try:
                if await locator.nth(index).is_visible(timeout=120):
                    total += 1
            except Exception:
                continue
    return total


async def collect_composer_candidates(page: Page, include_html: bool = False) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    locator = page.locator(COMPOSER_DIALOG_SELECTOR)
    total = await locator.count()

    for index in range(total):
        candidate = locator.nth(index)
        try:
            if not await candidate.is_visible(timeout=120):
                continue
        except Exception:
            continue

        try:
            aria_label = (await candidate.get_attribute("aria-label") or "").strip()
        except Exception:
            aria_label = ""

        heading_text = ""
        heading = await find_visible_locator(candidate, COMPOSER_HEADING_SELECTORS, timeout_ms=120)
        if heading is not None:
            heading_text = ((await get_locator_text(heading)) or "").strip().replace("\n", " ")[:220]

        editor = await find_scoped_editor(candidate, timeout_ms=120)
        editor_count = await count_visible_matches(candidate, COMPOSER_EDITOR_SELECTORS)
        post_button_count = await count_visible_matches(candidate, POST_BUTTON_SELECTORS)
        file_input_count = await count_visible_matches(candidate, FILE_INPUT_SELECTORS)
        photo_control_count = await count_visible_matches(candidate, PHOTO_VIDEO_CONTROL_SELECTORS)
        heading_count = await count_visible_matches(candidate, COMPOSER_HEADING_SELECTORS)
        bbox = await read_bbox(candidate)
        area = bbox["width"] * bbox["height"]

        score = 0.0
        reasons: list[str] = []
        rejected: str | None = None

        if editor is not None:
            score += 120
            reasons.append("editor visible+interactable")
        else:
            reasons.append("editor missing")

        if post_button_count > 0:
            score += 26
            reasons.append("has post button")
        if file_input_count > 0:
            score += 18
            reasons.append("has file input")
        if photo_control_count > 0:
            score += 16
            reasons.append("has photo/video control")
        if heading_count > 0:
            score += 6
            reasons.append("has create-post heading")

        score += min(area / 50000.0, 20.0)
        if area > 0:
            reasons.append(f"bbox area={int(area)}")

        if editor is None and heading_count > 0 and post_button_count == 0 and file_input_count == 0 and photo_control_count == 0:
            score -= 160
            rejected = "heading-only dialog"

        html = ""
        if include_html:
            try:
                html = await candidate.evaluate("(element) => element.outerHTML || ''") or ""
            except Exception:
                html = ""

        candidates.append(
            {
                "locator": candidate,
                "index": index,
                "aria_label": aria_label,
                "heading": heading_text,
                "editor_count": editor_count,
                "editor_interactable": editor is not None,
                "post_button_count": post_button_count,
                "file_input_count": file_input_count,
                "photo_control_count": photo_control_count,
                "bbox": bbox,
                "score": score,
                "reasons": reasons,
                "rejected": rejected,
                "html": html,
            }
        )

    return candidates


async def select_best_composer_scope(page: Page, require_editor: bool = False) -> tuple[Any | None, list[dict[str, Any]]]:
    candidates = await collect_composer_candidates(page, include_html=False)
    ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)

    for candidate in ranked:
        if candidate["rejected"]:
            continue
        if require_editor and not candidate["editor_interactable"]:
            continue
        candidate["selected_reason"] = "best score"
        return candidate["locator"], ranked
    return None, ranked


def redact_debug_text(raw_value: str, max_chars: int = 20000) -> str:
    value = raw_value or ""
    patterns = [
        r"(?i)(access_token\s*[=:]\s*)[^\s'\"&<]+",
        r"(?i)(fb_dtsg\s*[=:]\s*)[^\s'\"&<]+",
        r"(?i)(jazoest\s*[=:]\s*)[^\s'\"&<]+",
        r"(?i)(lsd\s*[=:]\s*)[^\s'\"&<]+",
        r"(?i)(xs\s*[=:]\s*)[^\s'\"&<]+",
        r"(?i)(c_user\s*[=:]\s*)[^\s'\"&<]+",
        r"(?i)(datr\s*[=:]\s*)[^\s'\"&<]+",
        r"(?i)(fr\s*[=:]\s*)[^\s'\"&<]+",
    ]
    for pattern in patterns:
        value = re.sub(pattern, r"\1[REDACTED]", value)
    if len(value) > max_chars:
        return value[:max_chars] + "\n...[TRUNCATED]"
    return value


async def get_locator_text(locator: Locator) -> str:
    try:
        value = await locator.evaluate(
            """
            (element) => {
                const parts = [];
                if ('value' in element && typeof element.value === 'string' && element.value) parts.push(element.value);
                if (typeof element.innerText === 'string' && element.innerText) parts.push(element.innerText);
                if (typeof element.textContent === 'string' && element.textContent) parts.push(element.textContent);
                const ariaLabel = element.getAttribute && element.getAttribute('aria-label');
                if (ariaLabel) parts.push(ariaLabel);
                for (const node of element.querySelectorAll('[data-text="true"], span, div, p')) {
                    if (parts.length >= 24) break;
                    const text = node.innerText || node.textContent || '';
                    if (text) parts.push(text);
                }
                return parts.join('\n');
            }
            """
        )
        if value:
            return value
    except Exception:
        pass

    try:
        value = await locator.input_value(timeout=500)
        if value:
            return value
    except Exception:
        pass

    for reader in (locator.inner_text, locator.text_content):
        try:
            value = await reader(timeout=500)
            if value:
                return value
        except Exception:
            continue
    return ""


async def get_scope_text(scope: Any) -> str:
    for reader in (scope.inner_text, scope.text_content):
        try:
            value = await reader(timeout=800)
            if value:
                return value
        except Exception:
            continue
    return ""


async def dump_composer_debug(page: Page, job_name: str, stage: str, job_url: str = "", failure_reason: str = "") -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = slugify_filename(job_name)
    safe_stage = slugify_filename(stage)
    base_path = DEBUG_DIR / f"{timestamp}_{safe_name}_{safe_stage}"

    scope, ranked = await select_best_composer_scope(page, require_editor=False)
    target = scope or page.locator("body")
    candidates_with_html = await collect_composer_candidates(page, include_html=True)

    try:
        await page.screenshot(path=str(base_path.with_suffix(".png")), full_page=True)
    except Exception:
        pass

    lines = [
        f"url={job_url or page.url}",
        f"job={job_name}",
        f"stage={stage}",
        f"failure_reason={failure_reason}",
        f"visible_dialogs={len(candidates_with_html)}",
    ]

    for selector in COMPOSER_EDITOR_SELECTORS:
        try:
            count = await target.locator(selector).count()
        except Exception:
            count = 0
        lines.append(f"editor_selector[{selector}]={count}")

    for index, candidate in enumerate(candidates_with_html, start=1):
        selected = ""
        for ranked_candidate in ranked:
            if ranked_candidate["index"] == candidate["index"] and ranked_candidate.get("selected_reason"):
                selected = ranked_candidate.get("selected_reason", "")
                break
        lines.append(f"--- dialog[{index}] ---")
        lines.append(f"index={candidate['index']}")
        lines.append(f"aria_label={candidate['aria_label']}")
        lines.append(f"heading={candidate['heading']}")
        lines.append(f"editor_count={candidate['editor_count']}")
        lines.append(f"editor_interactable={candidate['editor_interactable']}")
        lines.append(f"file_input_count={candidate['file_input_count']}")
        lines.append(f"post_button_count={candidate['post_button_count']}")
        lines.append(f"photo_control_count={candidate['photo_control_count']}")
        lines.append(f"bbox={candidate['bbox']}")
        lines.append(f"score={round(candidate['score'], 2)}")
        if candidate["rejected"]:
            lines.append(f"rejected={candidate['rejected']}")
        if selected:
            lines.append(f"selected={selected}")
        lines.append(f"reasons={'; '.join(candidate['reasons'])}")
        html = redact_debug_text(candidate.get("html", ""), max_chars=4000)
        if html:
            lines.append("candidate_html=")
            lines.append(html)

    try:
        text = await get_scope_text(target)
    except Exception:
        text = ""
    if text:
        lines.append("--- composer text ---")
        lines.append(redact_debug_text(text, max_chars=4000))

    base_path.with_suffix(".txt").write_text(redact_debug_text("\n".join(lines), max_chars=120000), encoding="utf-8")


async def maybe_pause_for_composer_debug(page: Page, job_name: str, options: RuntimeOptions) -> None:
    if not options.debug_composer:
        return
    await dump_composer_debug(page, job_name, "composer-open")
    print(f"[DEBUG] Da dump composer vao {DEBUG_DIR}")
    if options.pause_for_debugger:
        print("[DEBUG] Composer dang mo. Inspect roi nhan Enter de tiep tuc.")
        await asyncio.to_thread(input)


async def wait_for_composer_ready(page: Page, timeout_ms: int = 20000, poll_ms: int = 250) -> Any:
    end_at = time.monotonic() + (timeout_ms / 1000.0)
    opening_seen = False

    while time.monotonic() < end_at:
        scope, ranked = await select_best_composer_scope(page, require_editor=True)
        if scope is not None:
            print("[INFO] Da mo xong khung tao bai viet.")
            return scope

        if not opening_seen:
            for candidate in ranked:
                if candidate["heading"] or candidate["post_button_count"] > 0:
                    opening_seen = True
                    print("[INFO] Composer dang mo, dang cho editor render...")
                    break

        await page.wait_for_timeout(poll_ms)

    _, ranked = await select_best_composer_scope(page, require_editor=False)
    print(f"[DEBUG] Composer timeout. visible_dialogs={len(ranked)}")
    for index, candidate in enumerate(ranked, start=1):
        print(
            "[DEBUG] "
            f"dialog#{index} aria='{candidate['aria_label']}' heading='{candidate['heading'][:80]}' "
            f"editor={candidate['editor_count']}/{candidate['editor_interactable']} file={candidate['file_input_count']} "
            f"post={candidate['post_button_count']} photo={candidate['photo_control_count']} "
            f"bbox={candidate['bbox']} score={round(candidate['score'], 2)} reject={candidate['rejected'] or '-'}"
        )

    raise RuntimeError("Khung tao bai viet chua san sang: heading co the da hien nhung editor that chua xuat hien.")


async def get_dialog_signature(page: Page) -> str:
    dialog_count = await page.locator(COMPOSER_DIALOG_SELECTOR).count()
    editor_count = await page.locator(COMPOSER_EDITOR_SELECTORS[0]).count()
    heading_count = await page.locator(COMPOSER_HEADING_SELECTORS[0]).count()
    return f"d={dialog_count};e={editor_count};h={heading_count}"


async def wait_for_composer_ready_fast(
    page: Page,
    metrics: JobMetrics,
    state: WorkerPageState,
    timeout_ms: int = 20000,
    poll_ms: int = 160,
) -> Any:
    end_at = time.monotonic() + (timeout_ms / 1000.0)
    opening_seen = False
    loop_index = 0

    while time.monotonic() < end_at:
        loop_index += 1
        signature = await get_dialog_signature(page)
        need_rescore = signature != state.last_composer_signature or (loop_index % 5 == 0)

        if need_rescore:
            state.last_composer_signature = signature
            scope, ranked = await select_best_composer_scope(page, require_editor=True)
            if scope is not None:
                return scope
            if not opening_seen:
                for candidate in ranked:
                    if candidate["heading"] or candidate["post_button_count"] > 0:
                        opening_seen = True
                        print("[INFO] Composer dang mo, dang cho editor render...")
                        break
        else:
            quick_editor = await find_first_visible(page, COMPOSER_EDITOR_SELECTORS, metrics=metrics)
            if quick_editor is not None:
                scope, _ = await select_best_composer_scope(page, require_editor=True)
                if scope is not None:
                    return scope

        inc_detail_count(metrics, "composer.poll")
        await tracked_sleep(page, poll_ms, metrics, "composer.fixed_sleep")

    inc_detail_count(metrics, "composer.timeout_exhausted")
    # Keep fast polling first, then fall back to the proven full scoring wait
    # path to preserve 26/26 correctness when Facebook renders slowly.
    return await wait_for_composer_ready(page, timeout_ms=7000, poll_ms=max(poll_ms, 220))


async def open_composer(page: Page, options: RuntimeOptions, metrics: JobMetrics, state: WorkerPageState) -> Any:
    end_at = time.monotonic() + 20.0
    trigger = None
    while time.monotonic() < end_at:
        trigger = await find_first_visible(page, COMPOSER_ACTIVATION_SELECTORS, metrics=metrics)
        if trigger is not None:
            break
        inc_detail_count(metrics, "composer.trigger_poll")
        await tracked_sleep(page, 120 if options.performance_mode else 220, metrics, "composer.fixed_sleep")

    if trigger is None:
        inc_detail_count(metrics, "composer.trigger_timeout_exhausted")
        raise RuntimeError("Khong tim thay nut mo khung tao bai viet. Hay dang nhap va mo dung page/group.")

    await trigger.click()
    poll = 160 if options.performance_mode else 380
    scope = await wait_for_composer_ready_fast(page, metrics=metrics, state=state, timeout_ms=20000, poll_ms=poll)
    print("[INFO] Da mo xong khung tao bai viet.")
    return scope


async def insert_content(locator: Locator, page: Page, content: str) -> None:
    try:
        await locator.fill(content, timeout=1000)
        return
    except Exception:
        pass

    try:
        await locator.click(force=True, timeout=1000)
    except Exception:
        pass

    try:
        await locator.evaluate(
            """
            (element, value) => {
                const target = element;
                target.focus();
                if ('value' in target) target.value = value;
                else target.textContent = value;
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            content,
        )
        return
    except Exception:
        pass

    await page.keyboard.insert_text(content)


async def ensure_content_inserted(page: Page, content: str, editor: Locator, timeout_ms: int = 5000, poll_ms: int = 250) -> None:
    expected = normalize_text(content)
    snippet = expected[: min(len(expected), 48)]
    end_at = time.monotonic() + timeout_ms / 1000.0

    while time.monotonic() < end_at:
        actual = normalize_text(await get_locator_text(editor))
        if actual and (expected in actual or snippet in actual):
            return
        await page.wait_for_timeout(poll_ms)

    raise RuntimeError("Khong xac nhan duoc noi dung marker nam trong editor cua composer da chon.")


async def fill_content(page: Page, content: str, composer_scope: Any, options: RuntimeOptions) -> None:
    editor = None
    end_at = time.monotonic() + 12.0
    poll = 180 if options.performance_mode else 500

    while time.monotonic() < end_at:
        editor = await find_scoped_editor(composer_scope, timeout_ms=300)
        if editor is not None:
            break
        await page.wait_for_timeout(poll)

    if editor is None:
        raise RuntimeError("Khong tim thay editor tuong tac duoc trong composer root da chon.")

    await insert_content(editor, page, content)
    await ensure_content_inserted(page, content, editor=editor, timeout_ms=5000, poll_ms=poll)


async def count_visible_attachments(scope: Any) -> int:
    total = 0
    for selector in IMAGE_ATTACHMENT_READY_SELECTORS:
        locator = scope.locator(selector)
        count = await locator.count()
        for index in range(count):
            try:
                if await locator.nth(index).is_visible(timeout=200):
                    total += 1
            except Exception:
                continue
    return total


async def file_input_has_selected_file(file_input: Locator) -> bool:
    try:
        return bool(await file_input.evaluate("(element) => Boolean(element.files && element.files.length > 0)"))
    except Exception:
        return False


async def find_file_input(scope: Any) -> Locator | None:
    for selector in FILE_INPUT_SELECTORS:
        locator = scope.locator(selector)
        count = await locator.count()
        for index in range(count - 1, -1, -1):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible(timeout=150):
                    return candidate
            except Exception:
                continue
    return None


async def find_photo_video_control(scope: Any) -> Locator | None:
    for selector in PHOTO_VIDEO_CONTROL_SELECTORS:
        locator = scope.locator(selector)
        count = await locator.count()
        for index in range(count):
            control = locator.nth(index)
            try:
                if await control.is_visible(timeout=150):
                    return control
            except Exception:
                continue
    return None


async def wait_for_file_input(
    page: Page,
    dialog: Any,
    timeout_ms: int = 8000,
    poll_ms: int = 250,
    metrics: JobMetrics | None = None,
) -> Locator | None:
    end_at = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < end_at:
        file_input = await find_file_input(dialog)
        if file_input is not None:
            return file_input
        if metrics is not None:
            inc_detail_count(metrics, "upload.file_input_poll")
            await tracked_sleep(page, poll_ms, metrics, "upload.fixed_sleep")
        else:
            await page.wait_for_timeout(poll_ms)
    return None


async def wait_for_image_attached(
    page: Page,
    dialog: Any,
    file_input: Locator | None,
    previous_attachment_count: int,
    timeout_ms: int = 12000,
    poll_ms: int = 250,
    metrics: JobMetrics | None = None,
) -> bool:
    end_at = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < end_at:
        current_dialog, _ = await select_best_composer_scope(page, require_editor=False)
        current_dialog = current_dialog or dialog
        if file_input is not None and await file_input_has_selected_file(file_input):
            return True
        if await count_visible_attachments(current_dialog) > previous_attachment_count:
            return True
        if metrics is not None:
            inc_detail_count(metrics, "upload.preview_poll")
            await tracked_sleep(page, poll_ms, metrics, "upload.preview_wait")
        else:
            await page.wait_for_timeout(poll_ms)
    return False


async def click_photo_video_and_prepare_input(
    page: Page,
    dialog: Any,
    image_file: Path,
    options: RuntimeOptions,
    metrics: JobMetrics,
) -> tuple[Locator | None, str]:
    control = await find_photo_video_control(dialog)
    if control is None:
        return None, "no-control"

    try:
        async with page.expect_file_chooser(timeout=2200) as chooser_info:
            await control.click(timeout=4000)
        chooser = await chooser_info.value
        await chooser.set_files(str(image_file))
        return None, "photo-video-file-chooser"
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass

    try:
        await control.click(timeout=4000)
    except Exception:
        return None, "photo-video-click-failed"

    file_input = await wait_for_file_input(
        page,
        dialog,
        timeout_ms=7000,
        poll_ms=(180 if options.performance_mode else 300),
        metrics=metrics,
    )
    if file_input is None:
        return None, "photo-video-input-not-created"
    return file_input, "photo-video-input-created"


async def attach_image(page: Page, image_path: str, options: RuntimeOptions, metrics: JobMetrics, state: WorkerPageState) -> tuple[str, bool]:
    dialog, _ = await select_best_composer_scope(page, require_editor=False)
    if dialog is None:
        raise RuntimeError("Khong xac dinh duoc composer de tai anh.")

    image_file = Path(image_path)
    if not image_file.exists():
        raise RuntimeError(f"Khong tim thay file anh: {image_file}")

    previous_attachment_count = await count_visible_attachments(dialog)
    print("[INFO] Dang tim input upload anh trong composer root...")
    file_input: Locator | None = None
    method = "direct-input"

    if state.upload_strategy == "photo-video-file-chooser":
        file_input, method = await click_photo_video_and_prepare_input(page, dialog, image_file, options, metrics)
        if method == "photo-video-file-chooser":
            confirmed = await wait_for_image_attached(
                page,
                dialog,
                file_input=None,
                previous_attachment_count=previous_attachment_count,
                poll_ms=(120 if options.performance_mode else 320),
                metrics=metrics,
            )
            if confirmed:
                return method, True
            state.upload_strategy = "auto"

    if state.upload_strategy != "photo-video-file-chooser":
        file_input = await wait_for_file_input(
            page,
            dialog,
            timeout_ms=600,
            poll_ms=(120 if options.performance_mode else 220),
            metrics=metrics,
        )

    if file_input is None:
        print("[INFO] Chua co file input. Dang bam control Photo/video de tao input...")
        file_input, method = await click_photo_video_and_prepare_input(page, dialog, image_file, options, metrics)
        if method == "photo-video-file-chooser":
            state.upload_strategy = "photo-video-file-chooser"
            print("[INFO] Da gan file qua native file chooser tu control Photo/video.")
            confirmed = await wait_for_image_attached(
                page,
                dialog,
                file_input=None,
                previous_attachment_count=previous_attachment_count,
                poll_ms=(120 if options.performance_mode else 320),
                metrics=metrics,
            )
            return method, confirmed
        if file_input is None:
            raise RuntimeError("Khong tim thay input upload trong composer, ke ca sau khi bam Photo/video.")
        state.upload_strategy = "direct-input"

    print(f"[INFO] Da tim thay input upload anh ({method}), dang gan file...")
    await file_input.set_input_files(str(image_file), timeout=15000)
    print("[INFO] Da gan file anh vao input, dang cho Facebook nhan anh...")
    confirmed = await wait_for_image_attached(
        page,
        dialog,
        file_input=file_input,
        previous_attachment_count=previous_attachment_count,
        poll_ms=(120 if options.performance_mode else 320),
        metrics=metrics,
    )
    return method, confirmed


async def wait_for_submit_completion(page: Page, timeout_ms: int = 30000, poll_ms: int = 250) -> None:
    end_at = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < end_at:
        root_visible = False
        for selector in COMPOSER_ROOT_SELECTORS:
            locator = page.locator(selector)
            count = await locator.count()
            if count == 0:
                continue
            try:
                if await locator.last.is_visible(timeout=200):
                    root_visible = True
                    break
            except Exception:
                continue

        if not root_visible:
            return

        has_open_state = False
        for selector in COMPOSER_OPEN_STATE_SELECTORS:
            locator = page.locator(selector)
            count = await locator.count()
            if count == 0:
                continue
            try:
                await locator.first.wait_for(state="visible", timeout=180)
                has_open_state = True
                break
            except PlaywrightTimeoutError:
                continue

        if not has_open_state:
            return

        await page.wait_for_timeout(poll_ms)

    raise RuntimeError("Khong xac nhan duoc bai viet da gui xong; composer van con mo.")


async def close_composer(page: Page, composer_scope: Any | None = None) -> None:
    dialog = composer_scope
    if dialog is None:
        dialog, _ = await select_best_composer_scope(page, require_editor=False)
    if dialog is None:
        return

    close_button = await find_clickable(
        dialog,
        [
            "div[role='button'][aria-label='Close']",
            "div[role='button'][aria-label='Đóng']",
            "div[role='button'][aria-label='Dong']",
            "div[aria-label='Close']",
            "div[aria-label='Đóng']",
            "div[aria-label='Dong']",
        ],
        timeout_ms=1200,
    )
    if close_button is None:
        return

    try:
        await close_button.click(timeout=2500)
        await page.wait_for_timeout(180)
    except Exception:
        pass


async def close_optional_popups(page: Page, options: RuntimeOptions, metrics: JobMetrics, state: WorkerPageState) -> None:
    close_selectors = [
        "div[aria-label='Close']",
        "div[aria-label='Đóng']",
        "div[aria-label='Dong']",
        "div[role='button'][aria-label='Close']",
    ]
    closer = await find_clickable(page, close_selectors, timeout_ms=800, metrics=metrics)
    if closer is not None:
        try:
            await closer.click()
            state.popup_none_streak = 0
            await tracked_sleep(page, 80 if options.performance_mode else 180, metrics, "popup.cleanup_wait")
        except Exception:
            pass
    else:
        state.popup_none_streak += 1
        inc_detail_count(metrics, "popup.none")


async def wait_for_manual_login(page: Page, destination_url: str) -> None:
    print("[INFO] Facebook yeu cau dang nhap tay/2FA. Hoan tat tren browser, sau do nhan Enter de tiep tuc.")
    await asyncio.to_thread(input)
    await page.goto(destination_url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(600)
    if await is_login_page(page):
        raise RuntimeError("LOGIN_REQUIRED: Van chua dang nhap Facebook sau khi nhan Enter.")


async def login_to_facebook(page: Page, account_config: AccountConfig, destination_url: str, options: RuntimeOptions) -> None:
    if not account_config.facebook_login or not account_config.facebook_password:
        await wait_for_manual_login(page, destination_url)
        return

    email_input = page.locator("input[name='email']").first
    password_input = page.locator("input[name='pass']").first
    await email_input.wait_for(state="visible", timeout=10000)
    await password_input.wait_for(state="visible", timeout=10000)

    await email_input.fill(account_config.facebook_login)
    await password_input.fill(account_config.facebook_password)

    login_button = await find_clickable(
        page,
        [
            "button[name='login']",
            "div[role='button'][aria-label='Log in']",
            "div[role='button'][aria-label='Đăng nhập']",
            "span:has-text('Log in')",
            "span:has-text('Đăng nhập')",
        ],
        timeout_ms=5000,
    )
    if login_button is not None:
        try:
            await login_button.click(timeout=5000)
        except PlaywrightTimeoutError:
            await password_input.press("Enter")
    else:
        await password_input.press("Enter")

    await page.wait_for_load_state("domcontentloaded", timeout=30000)
    await page.wait_for_timeout(600 if options.performance_mode else 1500)

    current_url = page.url.lower()
    if "two_step_verification" in current_url or "checkpoint" in current_url:
        await wait_for_manual_login(page, destination_url)
        return

    if await is_login_page(page):
        await wait_for_manual_login(page, destination_url)
        return

    await page.goto(destination_url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(500 if options.performance_mode else 1000)


def detect_blocker_signal(url: str, message: str) -> str:
    text = f"{url} {message}".lower()
    for token in BLOCKER_TOKENS:
        if token in text:
            return token
    return ""


async def ensure_logged_in(page: Page, account_config: AccountConfig, destination_url: str, options: RuntimeOptions) -> None:
    if await is_login_page(page):
        print("[INFO] Phat hien chua dang nhap Facebook, dang thu tu dong dang nhap...")
        await login_to_facebook(page, account_config, destination_url, options)


async def ensure_initial_login(context: BrowserContext, account_config: AccountConfig, options: RuntimeOptions) -> None:
    page = await context.new_page()
    try:
        print("[INFO] Kiem tra dang nhap Facebook truoc khi load danh sach group...")
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(600 if options.performance_mode else 1200)
        await ensure_logged_in(page, account_config, "https://www.facebook.com/", options)
        if await is_login_page(page):
            raise RuntimeError("LOGIN_REQUIRED: Chua dang nhap Facebook, khong load group dang bai.")
        print("[INFO] Dang nhap Facebook da san sang, bat dau load group dang bai.")
    finally:
        await page.close()


async def ensure_initial_login_for_profile(profile_dir: Path, account_config: AccountConfig, options: RuntimeOptions) -> None:
    context = await build_context(profile_dir=profile_dir, account_config=account_config, options=options)
    try:
        await ensure_initial_login(context, account_config, options)
    finally:
        await close_context(context)


async def is_enabled_button(locator: Locator) -> bool:
    try:
        return bool(
            await locator.evaluate(
                """
                (element) => {
                    const target = element.closest('[role="button"], button') || element;
                    const ariaDisabled = target.getAttribute && target.getAttribute('aria-disabled');
                    if (ariaDisabled === 'true') return false;
                    if ('disabled' in target && target.disabled) return false;
                    return true;
                }
                """
            )
        )
    except Exception:
        return False


async def submit_post(page: Page, composer_scope: Any, dry_run: bool, options: RuntimeOptions, metrics: JobMetrics) -> tuple[bool, bool]:
    dialog = composer_scope
    if dialog is None:
        raise RuntimeError("Khong xac dinh duoc composer de bam Dang/Post.")

    post_button = await find_first_visible(dialog, POST_BUTTON_SELECTORS, metrics=metrics)
    if post_button is None:
        post_button = await find_clickable(page, POST_BUTTON_SELECTORS, timeout_ms=1800, metrics=metrics)
    if post_button is None:
        raise RuntimeError("Khong tim thay nut Dang/Post trong composer.")

    if dry_run:
        if not await is_enabled_button(post_button):
            raise RuntimeError("Khong tim thay Post button dang enabled trong dry-run.")
        print("[DRY-RUN] Da dien noi dung xong, bo qua buoc bam Dang/Post.")
        return False, True

    await post_button.click()
    try:
        await wait_for_submit_completion(page, timeout_ms=30000, poll_ms=(180 if options.performance_mode else 500))
        return True, True
    except Exception:
        return True, False


async def run_job_attempt(
    page: Page,
    job: Job,
    account: AccountConfig,
    options: RuntimeOptions,
    metrics: JobMetrics,
    state: WorkerPageState,
) -> dict[str, str]:
    matrix = {
        "job": job.display_name,
        "source": job.source_name,
        "target_id": job.target_id,
        "url": job.url,
        "composer_opened": "no",
        "correct_root_selected": "no",
        "editor_found": "no",
        "content_confirmed": "no",
        "file_input_mode": "n/a",
        "preview_confirmed": "n/a",
        "failure_reason": "",
        "worker": str(metrics.worker_id),
    }

    with StageTimer(metrics, "page_navigation"):
        nav_start = time.monotonic()
        await page.goto(job.url, wait_until="domcontentloaded", timeout=45000)
        add_detail_time(metrics, "navigation.network_render", time.monotonic() - nav_start)

    with StageTimer(metrics, "login_check"):
        await ensure_logged_in(page, account, job.url, options)

    with StageTimer(metrics, "popup_cleanup"):
        await close_optional_popups(page, options, metrics, state)

    with StageTimer(metrics, "composer_open"):
        composer_scope = await open_composer(page, options, metrics, state)

    matrix["composer_opened"] = "yes"
    if await find_scoped_editor(composer_scope, timeout_ms=500) is not None:
        matrix["correct_root_selected"] = "yes"

    await maybe_pause_for_composer_debug(page, job.display_name, options)

    with StageTimer(metrics, "editor_fill_confirm"):
        await fill_content(page, job.content, composer_scope=composer_scope, options=options)

    matrix["editor_found"] = "yes"
    matrix["content_confirmed"] = "yes"

    if job.image_path:
        with StageTimer(metrics, "image_file_chooser"):
            print(f"[INFO][worker={metrics.worker_id}][{job.target_id}] Dang tai anh {job.image_path}")
            mode, preview_confirmed = await attach_image(page, job.image_path, options, metrics, state)
            matrix["file_input_mode"] = mode
            matrix["preview_confirmed"] = "yes" if preview_confirmed else "no"

        with StageTimer(metrics, "preview_confirm"):
            pass
    else:
        metrics.stages["image_file_chooser"] = 0.0
        metrics.stages["preview_confirm"] = 0.0

    with StageTimer(metrics, "post_click"):
        clicked, confirmed = await submit_post(
            page,
            composer_scope=composer_scope,
            dry_run=options.dry_run,
            options=options,
            metrics=metrics,
        )
        metrics.submit_clicked = clicked

    if options.dry_run:
        metrics.stages["submit_completion"] = 0.0
    else:
        with StageTimer(metrics, "submit_completion"):
            if metrics.submit_clicked and not confirmed:
                raise RuntimeError("SUBMIT_UNCERTAIN: Da click Post nhung khong xac nhan duoc ket qua.")

    if options.dry_run:
        await close_composer(page, composer_scope=composer_scope)

    return matrix


async def build_context(profile_dir: Path, account_config: AccountConfig, options: RuntimeOptions) -> BrowserContext:
    slow_mo = 0 if options.performance_mode or options.headless else 80
    launch_options: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": options.headless,
        "slow_mo": slow_mo,
    }

    if account_config.browser_channel != "chromium":
        launch_options["channel"] = account_config.browser_channel

    launch_args: list[str] = []
    if account_config.chrome_profile_directory:
        launch_args.append(f"--profile-directory={account_config.chrome_profile_directory}")
    else:
        profile_dir.mkdir(parents=True, exist_ok=True)

    if options.debug_composer:
        launch_args.append("--auto-open-devtools-for-tabs")

    if launch_args:
        launch_options["args"] = launch_args

    pw = await async_playwright().start()
    try:
        context = await pw.chromium.launch_persistent_context(**launch_options)
    except Exception:
        await pw.stop()
        raise
    setattr(context, "_scheduler_playwright", pw)
    return context


async def close_context(context: BrowserContext) -> None:
    pw = getattr(context, "_scheduler_playwright", None)
    try:
        await context.close()
    except Exception:
        pass
    if pw is not None:
        await pw.stop()


def get_quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    pos = (len(sorted_values) - 1) * q
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def summarize_stage_metrics(batch: BatchMetrics) -> dict[str, dict[str, float]]:
    buckets: dict[str, StageStats] = {}
    for job in batch.job_metrics:
        for stage, duration in job.stages.items():
            buckets.setdefault(stage, StageStats()).add(duration)

    summary: dict[str, dict[str, float]] = {}
    for stage, stats in buckets.items():
        values = stats.values
        summary[stage] = {
            "avg": float(statistics.mean(values)) if values else 0.0,
            "median": float(statistics.median(values)) if values else 0.0,
            "p90": float(get_quantile(values, 0.9)) if values else 0.0,
            "count": float(len(values)),
        }
    return summary


def summarize_detail_times(batch: BatchMetrics) -> dict[str, dict[str, float]]:
    keys: dict[str, list[float]] = {}
    for job in batch.job_metrics:
        for key, value in job.detail_times.items():
            keys.setdefault(key, []).append(value)

    result: dict[str, dict[str, float]] = {}
    for key, values in keys.items():
        result[key] = {
            "avg": float(statistics.mean(values)) if values else 0.0,
            "median": float(statistics.median(values)) if values else 0.0,
            "p90": float(get_quantile(values, 0.9)) if values else 0.0,
        }
    return result


def summarize_detail_counts(batch: BatchMetrics) -> dict[str, int]:
    total: dict[str, int] = {}
    for job in batch.job_metrics:
        for key, value in job.detail_counts.items():
            total[key] = total.get(key, 0) + value
    return total


def print_batch_summary(
    jobs_total: int,
    succeeded: list[JobMetrics],
    failed: list[JobMetrics],
    source_counts: dict[str, int],
    batch: BatchMetrics,
) -> int:
    stage_summary = summarize_stage_metrics(batch)
    detail_time_summary = summarize_detail_times(batch)
    detail_count_summary = summarize_detail_counts(batch)
    slowest_stage = "n/a"
    slowest_avg = -1.0
    for stage, values in stage_summary.items():
        if values["avg"] > slowest_avg:
            slowest_stage = stage
            slowest_avg = values["avg"]

    summary_lines = [
        f"[INFO] Tong ket: thanh cong={len(succeeded)}, that bai={len(failed)}, tong={jobs_total}",
        "[INFO] Summary theo source target file:",
    ]
    for source_name, count in source_counts.items():
        src_ok = sum(1 for item in succeeded if Path(item.source_file).name == source_name)
        src_fail = sum(1 for item in failed if Path(item.source_file).name == source_name)
        summary_lines.append(f"  - {source_name}: jobs={count}, success={src_ok}, failed={src_fail}")

    summary_file = os.environ.get("SOCIAL_SCHEDULER_SUMMARY_FILE", "").strip()
    if summary_file:
        Path(summary_file).write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    else:
        for line in summary_lines:
            print(line)

    print(f"[PERF] total_batch_seconds={batch.total_seconds:.2f}")
    print(f"[PERF] retry_count={batch.retry_count}")
    print(f"[PERF] browser_restart_count={batch.browser_restart_count}")
    print(f"[PERF] slowest_stage={slowest_stage} avg={max(slowest_avg, 0):.2f}s")

    ordered_stages = [
        "page_navigation",
        "login_check",
        "popup_cleanup",
        "composer_open",
        "editor_fill_confirm",
        "image_file_chooser",
        "preview_confirm",
        "post_click",
        "submit_completion",
        "page_cleanup",
        "total_job",
    ]
    for stage in ordered_stages:
        info = stage_summary.get(stage)
        if not info:
            continue
        print(
            "[PERF] "
            f"stage={stage} avg={info['avg']:.2f}s median={info['median']:.2f}s p90={info['p90']:.2f}s count={int(info['count'])}"
        )

    print("[PERF][DETAIL] Timing phan ra theo loai cho 4 stage nong:")
    for key in [
        "navigation.network_render",
        "popup.cleanup_wait",
        "composer.fixed_sleep",
        "upload.fixed_sleep",
        "upload.preview_wait",
        "selector.count_wait",
        "selector.visible_wait",
    ]:
        info = detail_time_summary.get(key)
        if info is None:
            continue
        print(
            "[PERF][DETAIL] "
            f"key={key} avg={info['avg']:.2f}s median={info['median']:.2f}s p90={info['p90']:.2f}s"
        )

    for count_key in [
        "selector.try",
        "selector.poll",
        "selector.timeout_exhausted",
        "composer.poll",
        "composer.trigger_poll",
        "composer.timeout_exhausted",
        "composer.trigger_timeout_exhausted",
        "upload.file_input_poll",
        "upload.preview_poll",
        "popup.none",
    ]:
        if count_key in detail_count_summary:
            print(f"[PERF][DETAIL] count={count_key} total={detail_count_summary[count_key]}")

    slowest_jobs = sorted(batch.job_metrics, key=lambda item: item.stages.get("total_job", 0.0), reverse=True)[:3]
    if slowest_jobs:
        print("[PERF] Top 3 job cham nhat:")
        for item in slowest_jobs:
            print(
                f"  - worker={item.worker_id} job={item.job_name} total_job={item.stages.get('total_job', 0.0):.2f}s"
            )

    uncertain = [item for item in failed if item.submit_uncertain]
    if uncertain:
        print("[WARN] Co job SUBMIT_UNCERTAIN, khong retry de tranh dang trung:")
        for item in uncertain:
            print(f"  - {item.job_name}: {item.failure_reason}")

    if failed:
        print("[WARN] Nhom dang bai that bai:")
        for item in failed:
            print(f"  - {item.job_name}: {item.failure_reason}")
        return 1
    return 0


async def optimize_images_for_jobs(jobs: list[Job], performance_mode: bool) -> tuple[dict[str, str], list[ImageAuditEntry]]:
    if not performance_mode:
        return {}, []

    image_paths = sorted({job.image_path for job in jobs if job.image_path})
    if not image_paths:
        return {}, []

    if Image is None:
        print("[WARN] Chua cai dat Pillow, bo qua toi uu anh. (pip install pillow)")
        return {}, []

    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path_map: dict[str, str] = {}
    audit: list[ImageAuditEntry] = []

    for raw in image_paths:
        source = Path(raw)
        if not source.exists():
            continue

        source_bytes = source.stat().st_size
        file_hash = hashlib.sha256(source.read_bytes()).hexdigest()[:16]

        with Image.open(source) as img:
            img_format = (img.format or source.suffix.replace(".", "") or "IMG").upper()
            width, height = img.size
            max_edge = 2048
            resized = img.copy()
            resized.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            out_ext = ".jpg" if img_format in {"JPEG", "JPG", "PNG", "WEBP", "BMP"} else source.suffix.lower()
            out_file = IMAGE_CACHE_DIR / f"{source.stem}_{file_hash}{out_ext}"

            if not out_file.exists():
                save_kwargs: dict[str, Any] = {"optimize": True}
                if out_ext == ".jpg":
                    if resized.mode not in ("RGB", "L"):
                        resized = resized.convert("RGB")
                    save_kwargs.update({"quality": 85, "progressive": True})
                resized.save(out_file, **save_kwargs)

            optimized_bytes = out_file.stat().st_size

        path_map[str(source.resolve())] = str(out_file.resolve())
        audit.append(
            ImageAuditEntry(
                source_path=str(source.resolve()),
                optimized_path=str(out_file.resolve()),
                format=img_format,
                width=width,
                height=height,
                source_bytes=source_bytes,
                optimized_bytes=optimized_bytes,
            )
        )

    return path_map, audit


def apply_optimized_images(jobs: list[Job], optimized_map: dict[str, str]) -> list[Job]:
    result: list[Job] = []
    for job in jobs:
        image_path = job.image_path
        if image_path:
            key = str(Path(image_path).resolve())
            image_path = optimized_map.get(key, image_path)
        result.append(
            Job(
                job_id=job.job_id,
                source_file=job.source_file,
                source_name=job.source_name,
                post_id=job.post_id,
                post_name=job.post_name,
                target_id=job.target_id,
                target_name=job.target_name,
                url=job.url,
                content=job.content,
                image_path=image_path,
            )
        )
    return result


def print_image_audit(audit: list[ImageAuditEntry]) -> None:
    if not audit:
        return
    total_before = sum(item.source_bytes for item in audit)
    total_after = sum(item.optimized_bytes for item in audit)
    saved = total_before - total_after
    ratio = (saved / total_before * 100.0) if total_before else 0.0

    print("[PERF][IMAGE] Audit ket qua:")
    for item in audit:
        print(
            "[PERF][IMAGE] "
            f"file={Path(item.source_path).name} format={item.format} "
            f"dims={item.width}x{item.height} bytes_before={item.source_bytes} bytes_after={item.optimized_bytes}"
        )
    print(
        "[PERF][IMAGE] "
        f"total_before={total_before} total_after={total_after} saved={saved} ({ratio:.2f}%)"
    )


async def recreate_worker_page(worker: WorkerPageState, run_state: RunState) -> None:
    try:
        await worker.page.close()
    except Exception:
        pass
    worker.page = await run_state.context.new_page()
    worker.upload_strategy = "auto"
    worker.popup_none_streak = 0
    worker.last_composer_signature = ""


async def execute_job_with_retry(state: RunState, worker: WorkerPageState, job: Job, batch: BatchMetrics) -> JobMetrics:
    metrics = JobMetrics(
        job_id=job.job_id,
        job_name=job.display_name,
        source_file=job.source_file,
        worker_id=worker.worker_id,
    )

    for attempt in range(1, DEFAULT_JOB_ATTEMPTS + 1):
        total_start = time.monotonic()
        last_error_message = ""
        submit_clicked = False

        try:
            print(f"[INFO][worker={worker.worker_id}][{job.target_id}] Chay job '{job.display_name}' attempt={attempt}")
            matrix = await run_job_attempt(worker.page, job, state.account, state.options, metrics, worker)
            metrics.success = True
            print(
                "[MATRIX] "
                f"worker={worker.worker_id} source={matrix['source']} target_id={matrix['target_id']} job={matrix['job']} "
                f"composer_opened={matrix['composer_opened']} correct_root_selected={matrix['correct_root_selected']} "
                f"editor_found={matrix['editor_found']} content_confirmed={matrix['content_confirmed']} "
                f"file_input={matrix['file_input_mode']} preview_confirmed={matrix['preview_confirmed']} failure_reason=-"
            )
            return metrics
        except Exception as exc:
            last_error_message = str(exc).strip() or exc.__class__.__name__
            metrics.failure_reason = last_error_message
            submit_clicked = metrics.submit_clicked
            blocker = detect_blocker_signal(worker.page.url if worker.page else "", last_error_message)
            if blocker:
                state.stop_reason = f"BLOCKER_DETECTED:{blocker}"
                state.stop_new_jobs.set()

            if state.options.debug_composer:
                try:
                    await dump_composer_debug(worker.page, job.display_name, "failure", job_url=job.url, failure_reason=last_error_message)
                except Exception:
                    pass

            if submit_clicked:
                metrics.submit_uncertain = True
                print(f"[WARN][worker={worker.worker_id}] SUBMIT_UNCERTAIN '{job.display_name}': {last_error_message}")
                return metrics

            if attempt < DEFAULT_JOB_ATTEMPTS and not state.stop_new_jobs.is_set():
                metrics.retries += 1
                batch.retry_count += 1
                print(
                    f"[WARN][worker={worker.worker_id}] Job '{job.display_name}' loi lan {attempt}/{DEFAULT_JOB_ATTEMPTS}: "
                    f"{last_error_message}. Dang thu lai..."
                )
                if "has been closed" in last_error_message.lower() or "target page" in last_error_message.lower():
                    await recreate_worker_page(worker, state)
                else:
                    try:
                        await close_composer(worker.page)
                    except Exception:
                        await recreate_worker_page(worker, state)
            else:
                print(f"[ERROR][worker={worker.worker_id}] Job that bai '{job.display_name}': {last_error_message}")
                return metrics
        finally:
            if metrics.success:
                metrics.stages["page_cleanup"] = 0.0
            else:
                cleanup_start = time.monotonic()
                try:
                    await close_composer(worker.page)
                except Exception:
                    pass
                metrics.stages["page_cleanup"] = time.monotonic() - cleanup_start

            metrics.stages["total_job"] = time.monotonic() - total_start
            if "has been closed" in metrics.failure_reason.lower():
                state.stop_reason = "BROWSER_OR_CONTEXT_CLOSED"
                state.stop_new_jobs.set()

    return metrics


async def worker_loop(worker_id: int, queue: asyncio.Queue[Job], state: RunState, batch: BatchMetrics) -> list[JobMetrics]:
    results: list[JobMetrics] = []
    page = await state.context.new_page()
    worker = WorkerPageState(worker_id=worker_id, page=page)

    try:
        # Warm-up once per worker so later navigation is closer to steady state.
        await worker.page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass

    while True:
        if state.stop_new_jobs.is_set() and queue.empty():
            break
        try:
            job = queue.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.05)
            if queue.empty():
                break
            continue

        if state.stop_new_jobs.is_set():
            queue.task_done()
            continue

        metrics = await execute_job_with_retry(state, worker, job, batch)
        results.append(metrics)
        queue.task_done()

        if state.stop_new_jobs.is_set():
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break
            break

    try:
        await worker.page.close()
    except Exception:
        pass

    return results


async def run_pending_jobs(
    jobs_to_run: list[Job],
    account_config: AccountConfig,
    profile_dir: Path,
    options: RuntimeOptions,
) -> BatchMetrics:
    batch = BatchMetrics()
    context = await build_context(profile_dir=profile_dir, account_config=account_config, options=options)
    queue: asyncio.Queue[Job] = asyncio.Queue()
    for job in jobs_to_run:
        queue.put_nowait(job)

    state = RunState(context=context, account=account_config, options=options, stop_new_jobs=asyncio.Event())

    try:
        worker_count = options.concurrency
        tasks = [asyncio.create_task(worker_loop(i + 1, queue, state, batch)) for i in range(worker_count)]
        worker_results = await asyncio.gather(*tasks)
        for worker_items in worker_results:
            batch.job_metrics.extend(worker_items)
    finally:
        await close_context(context)
        batch.finish()

    if state.stop_reason:
        print(f"[WARN] Da dung nhan job moi do: {state.stop_reason}")

    return batch


def validate_runtime_options(args: argparse.Namespace) -> RuntimeOptions:
    if args.concurrency < 1:
        raise ValueError("--concurrency phai >= 1")
    if args.concurrency > 2:
        raise ValueError("Khong ho tro concurrency > 2 de tranh conflict profile/session.")

    return RuntimeOptions(
        headless=args.headless,
        dry_run=args.dry_run,
        debug_composer=args.debug_composer,
        pause_for_debugger=args.pause_for_debugger,
        performance_mode=args.performance_mode,
        concurrency=args.concurrency,
    )


async def async_main() -> int:
    args = parse_args()

    try:
        options = validate_runtime_options(args)
    except Exception as exc:
        print(f"[ERROR] Cau hinh runtime khong hop le: {exc}")
        return 1

    posts_path = resolve_from_base(args.posts)
    target_files = collect_target_files(args)

    all_input_paths = [posts_path, *target_files]
    missing_paths = [path for path in all_input_paths if not path.exists()]
    if missing_paths:
        for path in missing_paths:
            print(f"[ERROR] Khong tim thay file cau hinh: {path}")
        return 1

    try:
        account_config = select_account_config(args)
        jobs, source_counts = load_jobs(posts_path, target_files)
    except Exception as exc:
        print(f"[ERROR] Doc cau hinh that bai: {exc}")
        return 1

    profile_dir_value = args.profile_dir.strip() or account_config.profile_dir
    profile_dir = resolve_from_base(profile_dir_value)
    try:
        validate_effective_profile(account_config, profile_dir)
    except Exception as exc:
        print(f"[ERROR] Cau hinh profile khong hop le: {exc}")
        return 1

    try:
        await ensure_initial_login_for_profile(profile_dir, account_config, options)
    except Exception as exc:
        print(f"[ERROR] Dang nhap Facebook chua san sang: {exc}")
        return 1

    print(f"[INFO] Bat dau dang bai ngay. jobs={len(jobs)}, dry_run={options.dry_run}, concurrency={options.concurrency}")
    print(f"[INFO] Dang chay voi account_id={account_config.account_id} display_name={account_config.display_name}")
    print(f"[INFO] Browser session profile={profile_dir}")
    if account_config.browser_channel == "chromium":
        print("[INFO] Dang dung Playwright Chromium profile rieng, khong phai session Chrome thuong ngay.")
    else:
        print(f"[INFO] Dang dung browser channel={account_config.browser_channel}")
    print("[INFO] Target files:")
    for target_file in target_files:
        print(f"  - {target_file}")

    if options.dry_run:
        print("[DRY-RUN] Che do test: script se dien noi dung/anh nhung se KHONG bam Dang/Post.")

    if not jobs:
        print("[WARN] Khong co job nao dang bat de chay.")
        return 0

    optimized_map, image_audit = await optimize_images_for_jobs(jobs, options.performance_mode)
    if optimized_map:
        jobs = apply_optimized_images(jobs, optimized_map)
    print_image_audit(image_audit)

    batch = await run_pending_jobs(
        jobs_to_run=jobs,
        account_config=account_config,
        profile_dir=profile_dir,
        options=options,
    )

    succeeded = [item for item in batch.job_metrics if item.success]
    failed = [item for item in batch.job_metrics if not item.success]

    return print_batch_summary(
        jobs_total=len(jobs),
        succeeded=succeeded,
        failed=failed,
        source_counts=source_counts,
        batch=batch,
    )


def main() -> int:
    configure_stdout_for_windows()
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

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
        print("[INFO] Or run: Bump\\run_like_today_posts_linh.bat")
        sys.exit(1)
    raise

DEFAULT_TARGETS = ["config/targets.json", "config/targets_admin.json"]
DEFAULT_ACCOUNT_CONFIG = "config/account_linh.json"
DEFAULT_SEARCH_QUERY = "vĩnh sang"
DEFAULT_POSTER_NAME = "Vinh Sang"

LIKE_BUTTON_SELECTORS = [
    "div[role='button'][aria-label='Like']",
    "div[role='button'][aria-label*='Like']",
    "div[role='button'][aria-label='Thích']",
    "div[role='button'][aria-label*='Thích']",
    "span:has-text('Like')",
    "span:has-text('Thích')",
]

ALREADY_LIKED_TOKENS = [
    "unlike",
    "remove like",
    "bo thich",
    "bỏ thích",
    "da thich",
    "đã thích",
]


@dataclass(frozen=True)
class Target:
    source_file: str
    target_id: str
    name: str
    url: str


@dataclass
class LikeResult:
    target: Target
    status: str
    detail: str


@dataclass
class InspectResult:
    target: Target
    status: str
    poster_name: str
    posted_time: str
    is_target_poster: bool
    is_today: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect Facebook group search results for poster name and post time.")
    parser.add_argument("--account-config", default=DEFAULT_ACCOUNT_CONFIG, help="Account JSON to use.")
    parser.add_argument("--targets", action="append", default=[], help="Targets JSON file. Can be passed multiple times.")
    parser.add_argument("--search-query", default=DEFAULT_SEARCH_QUERY, help="Text to search inside each group.")
    parser.add_argument("--poster-name", default=DEFAULT_POSTER_NAME, help="Poster name to confirm.")
    parser.add_argument("--max-scrolls", type=int, default=6, help="Search result scroll attempts per group.")
    parser.add_argument("--headless", action="store_true", help="Run browser headless.")
    parser.add_argument("--debug-time-signals", action="store_true", help="Print DOM timestamp signals found in matching articles.")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_targets(paths: list[str]) -> list[Target]:
    targets: list[Target] = []
    seen_urls: set[str] = set()
    for raw_path in paths:
        path = resolve_from_base(raw_path)
        payload = read_json(path)
        for index, raw_target in enumerate(payload.get("targets", []), start=1):
            if not bool(raw_target.get("enabled", True)):
                continue
            url = str(raw_target.get("url", "")).strip()
            if not url:
                continue
            url_key = url.rstrip("/").lower()
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            targets.append(
                Target(
                    source_file=path.name,
                    target_id=str(raw_target.get("id") or f"target-{index}"),
                    name=str(raw_target.get("name") or url),
                    url=url,
                )
            )
    return targets


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(value).lower()).strip()


def build_today_patterns(now: datetime) -> list[str]:
    day = now.day
    month = now.month
    month_name = now.strftime("%B").lower()
    month_short = now.strftime("%b").lower()
    return [
        "hom nay",
        "today",
        "vua xong",
        "just now",
        f"{day} thang {month}",
        f"{day} thg {month}",
        f"{day}/{month}",
        f"{month}/{day}",
        f"{month_name} {day}",
        f"{month_short} {day}",
        f"{day} {month_name}",
        f"{day} {month_short}",
    ]


def looks_like_today_post(text: str, now: datetime) -> bool:
    normalized = normalize_text(text)
    if any(pattern in normalized for pattern in build_today_patterns(now)):
        return True
    if re.search(r"\b\d+\s*(phut|minute|minutes|min|m)\b", normalized):
        return True
    if re.search(r"\b\d+\s*(gio|hour|hours|hr|hrs|h)\b", normalized):
        return True
    return False


async def collect_article_time_signals(article: Any) -> list[str]:
    try:
        values = await article.evaluate(
            r"""
            (root) => {
                const out = [];
                const push = (value) => {
                    if (!value || typeof value !== 'string') return;
                    const text = value.replace(/\s+/g, ' ').trim();
                    if (!text || text.length > 240) return;
                    if (!out.includes(text)) out.push(text);
                };

                for (const element of root.querySelectorAll('a, span, abbr, time, [aria-label], [title], [datetime]')) {
                    push(element.getAttribute && element.getAttribute('aria-label'));
                    push(element.getAttribute && element.getAttribute('title'));
                    push(element.getAttribute && element.getAttribute('datetime'));

                    if (element.tagName === 'A') {
                        const href = element.getAttribute('href') || '';
                        if (/\/posts\/|permalink|story_fbid|multi_permalinks|groups\//i.test(href)) push(href);
                    }

                    const text = element.innerText || element.textContent || '';
                    if (/\b(\d+\s*(m|min|h|hr|hrs)|phút|gio|giờ|today|hôm nay|vừa xong|just now|tháng|thg)\b/i.test(text)) {
                        push(text);
                    }
                }
                return out.slice(0, 80);
            }
            """
        )
    except Exception:
        return []

    return [str(value) for value in values if str(value).strip()]


def looks_like_today_from_dom_signals(article_text: str, time_signals: list[str], now: datetime) -> bool:
    combined = "\n".join([article_text, *time_signals])
    return looks_like_today_post(combined, now)


def is_same_person(actual_name: str, expected_name: str) -> bool:
    actual = normalize_text(actual_name)
    expected = normalize_text(expected_name)
    return actual == expected or expected in actual


def choose_poster_name(candidates: list[str], expected_name: str) -> str:
    for candidate in candidates:
        if is_same_person(candidate, expected_name):
            return candidate
    for candidate in candidates:
        normalized = normalize_text(candidate)
        if normalized and normalized not in {"like", "thich", "comment", "binh luan", "share", "chia se"}:
            return candidate
    return ""


def choose_posted_time(signals: list[str], now: datetime) -> str:
    normalized_patterns = build_today_patterns(now)
    for signal in signals:
        normalized = normalize_text(signal)
        if any(pattern in normalized for pattern in normalized_patterns):
            return signal
        if re.search(r"\b\d+\s*(phut|minute|minutes|min|m|gio|hour|hours|hr|hrs|h)\b", normalized):
            return signal
    return signals[0] if signals else ""


async def collect_article_identity(article: Any) -> dict[str, list[str] | str]:
    try:
        payload = await article.evaluate(
            r"""
            (root) => {
                const unique = (values) => {
                    const out = [];
                    for (const value of values) {
                        if (!value || typeof value !== 'string') continue;
                        const text = value.replace(/\s+/g, ' ').trim();
                        if (!text || text.length > 220 || out.includes(text)) continue;
                        out.push(text);
                    }
                    return out;
                };

                const posterValues = [];
                const posterSelectors = [
                    'h2 a[role="link"]',
                    'h3 a[role="link"]',
                    'strong a[role="link"]',
                    'span[dir="auto"] a[role="link"]',
                    'a[role="link"] span[dir="auto"]',
                    'a[role="link"][href*="facebook.com"]'
                ];
                for (const selector of posterSelectors) {
                    for (const element of root.querySelectorAll(selector)) {
                        posterValues.push(element.innerText || element.textContent || element.getAttribute('aria-label') || '');
                    }
                }

                const timeValues = [];
                for (const element of root.querySelectorAll('a, span, abbr, time, [aria-label], [title], [datetime]')) {
                    const attrs = [
                        element.getAttribute && element.getAttribute('aria-label'),
                        element.getAttribute && element.getAttribute('title'),
                        element.getAttribute && element.getAttribute('datetime')
                    ];
                    for (const value of attrs) timeValues.push(value);

                    const text = element.innerText || element.textContent || '';
                    if (/\b(\d+\s*(m|min|h|hr|hrs)|phút|giờ|gio|today|hôm nay|vừa xong|just now|tháng|thg)\b/i.test(text)) {
                        timeValues.push(text);
                    }
                }

                return {
                    posterCandidates: unique(posterValues).slice(0, 20),
                    timeSignals: unique(timeValues).slice(0, 80),
                    text: (root.innerText || root.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 1200)
                };
            }
            """
        )
    except Exception:
        return {"posterCandidates": [], "timeSignals": [], "text": ""}

    return {
        "posterCandidates": [str(value) for value in payload.get("posterCandidates", []) if str(value).strip()],
        "timeSignals": [str(value) for value in payload.get("timeSignals", []) if str(value).strip()],
        "text": str(payload.get("text", "")),
    }


def group_search_url(group_url: str, query: str) -> str:
    return f"{group_url.rstrip('/')}/search/?q={quote_plus(query)}"


async def get_locator_text(locator: Any) -> str:
    try:
        return await locator.inner_text(timeout=1200)
    except Exception:
        try:
            return await locator.text_content(timeout=1200) or ""
        except Exception:
            return ""


async def is_already_liked(button: Any) -> bool:
    values: list[str] = []
    for getter in (button.get_attribute,):
        for attr in ("aria-label", "title"):
            try:
                value = await getter(attr)
                if value:
                    values.append(value)
            except Exception:
                pass
    try:
        text = await button.inner_text(timeout=500)
        if text:
            values.append(text)
    except Exception:
        pass

    normalized = normalize_text(" ".join(values))
    return any(token in normalized for token in ALREADY_LIKED_TOKENS)


async def find_like_button(article: Any) -> Any | None:
    for selector in LIKE_BUTTON_SELECTORS:
        locator = article.locator(selector)
        count = min(await locator.count(), 8)
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if not await candidate.is_visible(timeout=250):
                    continue
                if await is_already_liked(candidate):
                    return "already-liked"
                return candidate
            except Exception:
                continue
    return None


async def inspect_group_search_result(
    page: Any,
    account_config: Any,
    options: RuntimeOptions,
    target: Target,
    query: str,
    poster_name: str,
    max_scrolls: int,
    debug_time_signals: bool,
) -> InspectResult:
    await page.goto(group_search_url(target.url, query), wait_until="domcontentloaded", timeout=45000)
    await ensure_logged_in(page, account_config, target.url, options)
    await page.wait_for_timeout(1500)

    query_normalized = normalize_text(query)
    today = datetime.now()

    for scroll_index in range(max_scrolls + 1):
        articles = page.locator("div[role='article'], div[data-pagelet*='FeedUnit']")
        count = min(await articles.count(), 20)
        for index in range(count):
            article = articles.nth(index)
            try:
                if not await article.is_visible(timeout=250):
                    continue
            except Exception:
                continue

            identity = await collect_article_identity(article)
            text = str(identity.get("text") or await get_locator_text(article))
            normalized = normalize_text(text)
            if query_normalized not in normalized:
                continue

            poster_candidates = [str(value) for value in identity.get("posterCandidates", [])]
            time_signals = [str(value) for value in identity.get("timeSignals", [])]
            if debug_time_signals:
                print(f"[DEBUG] Poster candidates for {target.name}:")
                for candidate in poster_candidates[:8]:
                    print(f"  - {candidate}")
                print(f"[DEBUG] Time signals for {target.name}:")
                for signal in time_signals[:12]:
                    print(f"  - {signal}")

            found_poster = choose_poster_name(poster_candidates, poster_name)
            posted_time = choose_posted_time(time_signals, today)
            is_target_poster = is_same_person(found_poster, poster_name)
            is_today = looks_like_today_from_dom_signals(text, time_signals, today)
            detail = f"poster='{found_poster or 'unknown'}', posted_time='{posted_time or 'unknown'}'"
            return InspectResult(target, "found", found_poster, posted_time, is_target_poster, is_today, detail)

        if scroll_index < max_scrolls:
            await page.mouse.wheel(0, 1600)
            await page.wait_for_timeout(1200)

    return InspectResult(target, "not_found", "", "", False, False, "No search result article containing query was found.")


def runtime_options(headless: bool = False) -> RuntimeOptions:
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
    target_files = args.targets or DEFAULT_TARGETS
    targets = load_targets(target_files)
    if not targets:
        print("[ERROR] No enabled targets found.")
        return 1

    account_config = load_account_config(resolve_from_base(args.account_config), require_status=False)
    profile_dir = resolve_from_base(account_config.profile_dir)
    options = runtime_options(headless=args.headless)
    context = await build_context(profile_dir=profile_dir, account_config=account_config, options=options)
    page = await context.new_page()

    results: list[InspectResult] = []
    try:
        print(f"[INFO] Account: {account_config.account_id} / {account_config.display_name}")
        print(f"[INFO] Search query: {args.search_query}")
        print(f"[INFO] Expected poster: {args.poster_name}")
        print(f"[INFO] Targets: {len(targets)}")

        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
        await ensure_logged_in(page, account_config, "https://www.facebook.com/", options)

        for position, target in enumerate(targets, start=1):
            print(f"[INFO] ({position}/{len(targets)}) {target.name}")
            try:
                result = await inspect_group_search_result(
                    page=page,
                    account_config=account_config,
                    options=options,
                    target=target,
                    query=args.search_query,
                    poster_name=args.poster_name,
                    max_scrolls=args.max_scrolls,
                    debug_time_signals=args.debug_time_signals,
                )
            except Exception as exc:
                result = InspectResult(target, "error", "", "", False, False, str(exc).strip() or exc.__class__.__name__)
            results.append(result)
            print(
                f"[RESULT] {result.status}: {target.name} | "
                f"is_vinh_sang={result.is_target_poster} | is_today={result.is_today} | {result.detail}"
            )
    finally:
        try:
            await page.close()
        except Exception:
            pass
        await close_context(context)

    found = [item for item in results if item.status == "found"]
    confirmed = [item for item in found if item.is_target_poster and item.is_today]
    failed = [item for item in results if item.status != "found"]

    print("\n===== Bump Inspect Summary =====")
    print(f"found={len(found)}, confirmed_vinh_sang_today={len(confirmed)}, not_found_or_error={len(failed)}, total={len(results)}")
    if found:
        print("Found articles:")
        for item in found:
            print(f"  - {item.target.name}")
            print(f"    poster={item.poster_name or 'unknown'}")
            print(f"    posted_time={item.posted_time or 'unknown'}")
            print(f"    is_vinh_sang={item.is_target_poster}")
            print(f"    is_today={item.is_today}")
    if failed:
        print("Failed / not found:")
        for item in failed:
            print(f"  - {item.target.name}")
            print(f"    url={item.target.url}")
            print(f"    status={item.status}")
            print(f"    detail={item.detail}")
    print("=============================")

    return 0 if not failed else 1


def main() -> int:
    configure_stdout_for_windows()
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())

"""Zalo auto reply: tu dong tra loi 1 tin nhan co dinh khi co nguoi la nhan tin.

Chay nen lien tuc: mo chat.zalo.me bang profile rieng (dang nhap QR 1 lan),
poll danh sach hoi thoai moi POLL_SECONDS giay, doc tin nhan den cuoi cung,
chi gui REPLY_TEXT khi tin chua keyword trong Zalo/keywords.txt (ghi nhan trong
logs/zalo_replied.json de khong gui lai; thread khong khop keyword thi bo qua).

Setup lan dau:  Zalo\\run_zalo_auto_reply.bat --setup   (quet QR, dump DOM de chot selector)
Chay thuong:    Zalo\\run_zalo_auto_reply.bat
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

SOCIAL_DIR = Path(__file__).resolve().parents[1]
if str(SOCIAL_DIR) not in sys.path:
    sys.path.insert(0, str(SOCIAL_DIR))

try:
    from social_scheduler import (  # noqa: E402
        AccountConfig,
        RuntimeOptions,
        build_context,
        close_all_pages,
        close_context,
        configure_stdout_for_windows,
    )
except ModuleNotFoundError as exc:
    if exc.name == "playwright":
        print("[ERROR] Missing Python package: playwright")
        print(f"[INFO] Run with project venv: {SOCIAL_DIR}\\.venv\\Scripts\\python.exe {Path(__file__).resolve()}")
        sys.exit(1)
    raise

ZALO_HOME = "https://chat.zalo.me/"
LOG_DIR = SOCIAL_DIR / "logs"
STATE_FILE = LOG_DIR / "zalo_replied.json"
IGNORE_FILE = LOG_DIR / "zalo_ignored.json"  # thread user da tu tra loi tay -> tool khong bao gio nhac vao
DOM_DUMP_FILE = LOG_DIR / "zalo_dom_dump.json"
ZALO_DIR = Path(__file__).resolve().parent
KEYWORDS_FILE = ZALO_DIR / "keywords.txt"

REPLY_TEXT = (
    "[Tin nhắn tự động] Bạn đăng kí vãng lai thì có thể gửi "
    "<số lượng slot + Giới tính + Tên của các bạn>. "
    "Tụi mình sẽ trả lời chốt slot nhanh nhất hoặc bạn có thể vào group zalo "
    "để vote chốt slot cho tiện nha. https://zalo.me/g/r0xfhglsikxroxsiqfov"
)

ACTIVE_START_HOUR = 9           # chi quet/reply trong khung 9h-20h hang ngay
ACTIVE_END_HOUR = 20
POLL_SECONDS = 30               # nghi giua 2 luot quet
MAX_THREADS_PER_CYCLE = 10      # gioi han so hoi thoai reply moi luot (tranh burst bi danh spam)
SEND_DELAY_SECONDS = 8          # nghi giua 2 tin nhan lien tiep
LOGIN_WAIT_MINUTES = 10         # cho toi da 10p de quet QR khi chua dang nhap
CRASH_BACKOFF_SECONDS = 30      # session loi -> khoi dong lai sau 30s
MAX_SCAN_FAILURES = 5           # quet loi lien tiep qua muc nay -> nem exception de supervisor restart browser

# --- Selectors (da chot tu DOM that 2026-10-12; rieng rule unread badge chua co mau that) ---
LOGGED_IN_MARKERS = ["#contact-search-input"]
COMPOSER_SELECTOR = "#richInput"
# Bo loc 'Tin nhan tu nguoi la' trong menu 'Phan loai' -> danh sach chi con thread nguoi la,
# chip 'Nguoi la' hien tren filter bar voi class mc-selected.
STRANGER_MENU_ITEM = "Tin nhắn từ người lạ"
STRANGER_CHIP_TEXT = "Người lạ"
# Thread trong danh sach: div.msg-item[anim-data-id]; group/cong dong co tien to 'g' (bo qua
# cho chac), thread da block co icon .fa-Block_24_Line (bo qua). Chi lay thread co tin chua
# doc + tin cuoi khong phai cua minh (snippet khong bat dau 'Ban:').
SCAN_JS = """() => {
    const items = [];
    document.querySelectorAll("div.msg-item").forEach(el => {
        const id = el.getAttribute("anim-data-id") || "";
        if (!id || id.startsWith("g")) return;
        if (el.querySelector(".fa-Block_24_Line")) return;
        // Tin chua doc: badge cham (khong text) trong .conv-action__unread-v2
        // hoac preview to dam voi class .z-conv-message.--unread (DOM that 2026-10-12).
        let unread = !!el.querySelector(".conv-action__unread-v2, .z-conv-message.--unread");
        if (!unread) {
            el.querySelectorAll(".z-noti-badge-container").forEach(b => {
                if ((b.textContent || "").trim()) unread = true;
            });
        }
        if (!unread && /unread/i.test(el.className || "")) unread = true;
        if (!unread) return;
        const snippetEl = el.querySelector(".z-conv-message__preview-message");
        const snippet = (snippetEl ? snippetEl.textContent : "").trim();
        if (/^Bạn:/i.test(snippet)) return;
        const nameEl = el.querySelector(".conv-item-title__name .truncate, .conv-item-title__name");
        items.push({id, name: (nameEl ? nameEl.textContent : "").trim().slice(0, 60),
                    snippet: snippet.slice(0, 60)});
    });
    return items;
}"""

# Doc cac tin nhan gan nhat trong khung chat dang mo, kem co mine (tin cua minh).
# Tin cua minh nhan biet qua element class 'me' / '--me' trong khoi chat-item
# (gio gui, trang thai Da nhan) - dung de phat hien user da tu tra loi hoi thoai.
READ_RECENT_JS = """() => {
    const texts = [];
    document.querySelectorAll(".text-message__container").forEach(el => {
        const root = el.closest("[class*='chat-item']") || el.parentElement;
        let mine = false;
        if (root) {
            mine = !!root.querySelector("[class~='me'], [class*='--me']");
        }
        let timeText = "";
        if (root) {
            const tEl = root.querySelector("[class~='card-send-time']");
            if (tEl) timeText = (tEl.textContent || "").trim();
        }
        const t = (el.textContent || "").trim();
        if (t) texts.push({mine, text: t, timeText});
    });
    // 10 tin gan nhat, moi nhat dung dau, giu ca tin cua minh de phat hien user da tu tra loi.
    // timeText: tin hom nay chi co dang "HH:MM"; tin ngay cu kem them ngay thang.
    return texts.slice(-10).reverse();
}"""

# Tin gui that bai (Zalo tu choi: nguoi nhan chan / thread request chua san sang / anti-spam):
# Zalo van xoa o nhap nen KHONG the chi nhin composer - phai quet thong bao loi trong thread.
CHECK_SEND_FAIL_JS = """() => {
    const items = document.querySelectorAll("[class*='chat-item'], [class*='system-message'], [class*='error-message']");
    const tail = Array.from(items).slice(-4);
    for (const el of tail) {
        const t = (el.textContent || "").toLowerCase();
        if (t.includes("không thể nhận tin nhắn") || t.includes("gửi lại") || t.includes("không gửi được")) {
            return true;
        }
    }
    return false;
}"""


def normalize_text(text: str) -> str:
    """Bo dau tieng Viet + lowercase de so khop keyword ben vung ('vang lai' ~ 'vãng lại').
    Luu y: 'đ' khong bi NFD tach nen phai replace thu cong thanh 'd'."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d")


def load_keywords() -> list[str]:
    """Doc Zalo/keywords.txt (moi dong 1 keyword, dong # la comment). File rong -> reply tat ca."""
    try:
        lines = KEYWORDS_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    return [normalize_text(line.strip()) for line in lines if line.strip() and not line.strip().startswith("#")]


def matches_keyword(message: str, keywords: list[str]) -> bool:
    """So khop theo ranh gioi tu (word boundary): tranh 'san' an nhau vao 'Sang', 'gia' vao 'gia dinh'."""
    normalized = normalize_text(message)
    return any(re.search(r"\b" + re.escape(kw) + r"\b", normalized) for kw in keywords)
# ---------------------------------------------------------------------------------------------

DUMP_JS = """() => {
    const out = {editables: [], strangerTexts: [], convItems: [], searchInputs: []};
    const clip = (s, n) => (s || "").replace(/\\s+/g, " ").trim().slice(0, n || 120);
    const attrs = (el) => {
        const a = {};
        for (const name of el.getAttributeNames()) {
            if (name.startsWith("data-") || ["id", "class", "role", "aria-label"].includes(name)) {
                a[name] = clip(el.getAttribute(name), 200);
            }
        }
        return a;
    };
    document.querySelectorAll("[contenteditable='true'], input[type='text'], input:not([type])").forEach(el => {
        out.editables.push({tag: el.tagName, attrs: attrs(el)});
    });
    document.querySelectorAll("span, div, p").forEach(el => {
        const t = clip(el.textContent, 60).toLowerCase();
        if (t.includes("người lạ") || t.includes("nguoi la") || t.includes("tin nhắn từ")) {
            out.strangerTexts.push({tag: el.tagName, attrs: attrs(el),
                parentAttrs: el.parentElement ? attrs(el.parentElement) : {}});
        }
    });
    document.querySelectorAll("div[id], div[class], li").forEach(el => {
        const idcls = ((el.id || "") + " " + (el.className && el.className.baseVal !== undefined ? "" : el.className || "")).toString().toLowerCase();
        if (/conv|chat-item|msg-item|friend|thread|tab/.test(idcls)) {
            const r = el.getBoundingClientRect();
            if (r.width > 100 && r.height > 20 && r.height < 200 && r.x < 450) {
                out.convItems.push({tag: el.tagName, attrs: attrs(el), text: clip(el.textContent, 60)});
            }
        }
    });
    out.convItems = out.convItems.slice(0, 40);
    out.strangerTexts = out.strangerTexts.slice(0, 20);
    return out;
}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tu dong tra loi tin nhan nguoi la tren Zalo web.")
    parser.add_argument("--setup", action="store_true",
                        help="Mo browser de quet QR dang nhap, sau do dump DOM ve logs/zalo_dom_dump.json roi thoat.")
    parser.add_argument("--headless", action="store_true", help="Chay browser an (khong khuyen nghi cho lan dau).")
    parser.add_argument("--dry-run", action="store_true", help="Quet va in ra hoi thoai se reply, khong gui that.")
    parser.add_argument("--profile", default="main", help="Ten profile Zalo (data/profiles/zalo_<ten>). Mac dinh: main.")
    parser.add_argument("--all-hours", action="store_true", help="Bo qua khung gio 9h-20h (dung de test ngoai gio).")
    return parser.parse_args()


def in_active_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR


def _load_json_dict(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json_dict(path: Path, data: dict[str, str]) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] Khong ghi duoc {path.name}: {exc}")


def load_state() -> dict[str, str]:
    return _load_json_dict(STATE_FILE)


def load_ignored() -> dict[str, str]:
    return _load_json_dict(IGNORE_FILE)


def save_state(state: dict[str, str]) -> None:
    _save_json_dict(STATE_FILE, state)


def save_ignored(ignored: dict[str, str]) -> None:
    _save_json_dict(IGNORE_FILE, ignored)


def profile_dir_for(name: str) -> Path:
    return SOCIAL_DIR / "data" / "profiles" / f"zalo_{name}"


def make_account_config(profile_dir: Path) -> AccountConfig:
    return AccountConfig(
        account_id=f"zalo_{profile_dir.name.removeprefix('zalo_')}",
        display_name=f"Zalo {profile_dir.name.removeprefix('zalo_')}",
        status="",
        profile_dir=str(profile_dir),
        browser_channel="chrome",
        chrome_profile_directory="",
        facebook_login="",
        facebook_password="",
        source_path="",
    )


async def is_logged_in(page: Any) -> bool:
    for selector in LOGGED_IN_MARKERS:
        try:
            if await page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


async def ensure_logged_in(page: Any) -> bool:
    """Cho toi khi dang nhap xong (lan dau hien QR de quet)."""
    deadline = time.monotonic() + LOGIN_WAIT_MINUTES * 60
    announced = False
    while time.monotonic() < deadline:
        if await is_logged_in(page):
            return True
        if not announced:
            print(f"[INFO] Chua dang nhap Zalo. Mo cua so browser va QUET QR (cho toi da {LOGIN_WAIT_MINUTES}p)...")
            announced = True
        await page.wait_for_timeout(5000)
    return False


async def ensure_stranger_filter(page: Any) -> None:
    """Bat bo loc 'Tin nhan tu nguoi la' (chip 'Nguoi la' tren filter bar) neu chua bat.
    Menu item dang checkbox: bam la toggle, nen tuyet doi khong bam khi da active."""
    try:
        chip = page.locator(".tab-main .filter-preview-v2.mc-selected", has_text=STRANGER_CHIP_TEXT)
        if await chip.count() > 0:
            return
    except Exception:
        pass
    try:
        await page.get_by_text("Phân loại", exact=False).first.click(timeout=3000)
        await page.wait_for_timeout(1200)
        item = page.locator(".zmenu-item", has_text=STRANGER_MENU_ITEM).last
        cls = await item.get_attribute("class", timeout=1500) or ""
        if "active" not in cls:
            await item.click(timeout=3000)
            await page.wait_for_timeout(1500)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
    except Exception as exc:
        print(f"[WARN] Khong bat duoc bo loc Nguoi la: {str(exc)[:150]}")


async def scan_strangers(page: Any) -> list[dict[str, str]]:
    """Tra ve cac hoi thoai 1-1 co tin chua doc trong tab hien tai."""
    try:
        return await page.evaluate(SCAN_JS)
    except Exception:
        return []


MARK_UNREAD_TEXT = "Đánh dấu chưa đọc"


async def mark_thread_unread(page: Any, thread_id: str) -> bool:
    """Danh dau lai chua doc sau khi doc/reply (mo thread la Zalo tu danh da doc,
    nguoi dung se khong con thay hoi thoai tren dien thoai).
    Thread vua bi reply co the bi Zalo day khoi view loc Nguoi la -> fallback sang tab Tat ca."""
    async def _try_mark() -> bool:
        item = page.locator(f"div.msg-item[anim-data-id='{thread_id}']").first
        await item.click(button="right", timeout=3000)
        await page.wait_for_timeout(1000)
        menu_item = page.locator(".zmenu-item", has_text=MARK_UNREAD_TEXT).last
        await menu_item.click(timeout=2500)
        await page.wait_for_timeout(800)
        return True

    try:
        return await _try_mark()
    except Exception:
        try:
            await page.keyboard.press("Escape")
            tab = page.locator(".tab-main .tab-item", has_text="Tất cả").last
            await tab.click(timeout=2500)
            await page.wait_for_timeout(1500)
            return await _try_mark()
        except Exception as exc:
            print(f"[WARN] Khong danh dau unread lai duoc ({thread_id}): {str(exc)[:120]}")
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return False


async def open_thread(page: Any, thread_id: str) -> bool:
    item = page.locator(f"div.msg-item[anim-data-id='{thread_id}']").first
    try:
        await item.click(timeout=3000)
    except Exception:
        return False
    await page.wait_for_timeout(2500)
    return True


async def read_recent_messages(page: Any, limit: int = 10) -> list[dict[str, Any]]:
    """Tra ve toi da `limit` tin nhan gan nhat (moi nhat truoc), moi item {mine, text}.
    Giu ca tin cua minh de nhan biet user da tu tra loi hoi thoai (ke ca tren dien thoai)."""
    try:
        result = await page.evaluate(READ_RECENT_JS)
    except Exception:
        return []
    if not isinstance(result, list):
        return []
    out = []
    for m in result[:limit]:
        text = str(m.get("text", "")).strip()
        if not text:
            continue
        time_text = str(m.get("timeText", "")).strip()
        # Tin hom nay hien dung "HH:MM"; kem ngay thang (hoac khong doc duoc gio) -> khong tinh hom nay.
        today = bool(re.fullmatch(r"\d{1,2}:\d{2}", time_text))
        out.append({"mine": bool(m.get("mine")), "text": text, "today": today})
    return out


async def send_in_open_thread(page: Any) -> bool:
    editor = page.locator(COMPOSER_SELECTOR).last
    try:
        if not await editor.is_visible(timeout=1500):
            return False
        await editor.click(timeout=2000)
        await page.keyboard.type(REPLY_TEXT, delay=40)
        await page.wait_for_timeout(500)
        await page.keyboard.press("Enter")
    except Exception:
        return False
    await page.wait_for_timeout(1500)
    # Zalo xoa o nhap ca khi gui that bai ("Khong the nhan tin nhan tu ban") ->
    # phai kiem tra ca thong bao loi trong thread, khong chi nhin composer.
    try:
        if await page.evaluate(CHECK_SEND_FAIL_JS):
            return False
    except Exception:
        pass
    try:
        return (await editor.inner_text()).strip() == ""
    except Exception:
        return True


async def scan_and_reply(page: Any, state: dict[str, str], ignored: dict[str, str],
                         dry_run: bool, keywords: list[str], skip_snippets: dict[str, str]) -> int:
    """Quet 1 luot. Tra ve so hoi thoai da reply (hoac se reply neu dry_run).
    3 nhom hoi thoai: (1) tool da reply -> state, khong mo lai; (2) user da tu tra loi ->
    ignore list, khong mo lai; (3) chua ai tra loi + chua co keyword -> theo doi snippet,
    co tin moi thi mo ra cham keyword lai."""
    await ensure_stranger_filter(page)
    threads = await scan_strangers(page)
    sent = 0
    for thread in threads:
        if sent >= MAX_THREADS_PER_CYCLE:
            break
        key = thread["id"]
        if key in state or key in ignored:
            continue
        snippet = thread.get("snippet", "")
        if skip_snippets.get(key) == snippet:
            # Da skip o luot truoc va chua co tin moi (unread hien tai la do chinh tool danh dau lai).
            continue
        label = f"{thread.get('name', '')!r} ({key})"
        print(f"[INFO] Nguoi la moi: {label} - {thread.get('snippet', '')!r}"
              + (" (dry-run, khong gui)" if dry_run else ""))
        if dry_run:
            sent += 1
            continue
        if not await open_thread(page, key):
            print(f"[WARN] Khong mo duoc hoi thoai: {label}")
            continue
        recent = await read_recent_messages(page)
        if not recent:
            # Thread moi co the render cham -> doc lai 1 lan nua truoc khi ket luan.
            await page.wait_for_timeout(2000)
            recent = await read_recent_messages(page)
        if not recent:
            # Doc trang (race load/DOM doi): danh dau unread lai nhung KHONG ghi skip_snippets,
            # de luot quet sau mo lai va doc lai tu dau - tranh bo sot vinh vien 1 nguoi.
            print(f"[WARN] {label}: khong doc duoc noi dung tin nhan, se thu lai luot sau.")
            await mark_thread_unread(page, key)
            continue
        if any(m["mine"] and m["today"] for m in recent):
            # Chu account da tu tra loi TRONG HOM NAY -> nhuong hoi thoai: dua vao ignore list
            # (luu file), khong danh dau unread lai - thread bien mat khoi danh sach unread.
            # Chi tinh tin cua user trong hom nay: user rep tu hom truoc thi hom nay khach
            # nhan keyword van duoc tool tra loi (case Truong Quang Vinh).
            print(f"[SKIP] {label}: user da tu tra loi hom nay, dua vao ignore list.")
            ignored[key] = datetime.now().isoformat(timespec="seconds")
            save_ignored(ignored)
            continue
        recent_msgs = [m["text"] for m in recent if not m["mine"]][:5]
        matched_msg = next((m for m in recent_msgs if matches_keyword(m, keywords)), None)
        if keywords and matched_msg is None:
            preview = recent_msgs[0][:60] if recent_msgs else ""
            print(f"[SKIP] {label}: {len(recent_msgs)} tin gan nhat khong chua keyword ({preview!r})")
            skip_snippets[key] = snippet
            await mark_thread_unread(page, key)
            continue
        last_msg = matched_msg or (recent_msgs[0] if recent_msgs else "")
        delivered = await send_in_open_thread(page)
        if not delivered:
            # Lan dau that bai co the do thread request chua san sang -> cho roi gui lai 1 lan.
            print(f"[WARN] Gui lan 1 that bai cho {label}, thu lai sau 4s...")
            await page.wait_for_timeout(4000)
            delivered = await send_in_open_thread(page)
        if delivered:
            state[key] = datetime.now().isoformat(timespec="seconds")
            save_state(state)
            sent += 1
            print(f"[RESULT] Da reply: {label} (tin khop: {last_msg[:60]!r})")
            await mark_thread_unread(page, key)
            await page.wait_for_timeout(SEND_DELAY_SECONDS * 1000)
        else:
            # KHONG ghi state: de ho nhan tin lai thi tool thu lai; nguoi dung tu tra loi tay duoc.
            print(f"[ERROR] Zalo tu choi tin gui toi {label} sau 2 lan thu - can kiem tra tay "
                  f"(co the nguoi nhan chan tin nguoi la hoac Zalo dang chan link trong tin).")
            await mark_thread_unread(page, key)
    return sent


async def run_session(args: argparse.Namespace) -> None:
    options = RuntimeOptions(
        headless=args.headless,
        dry_run=False,
        debug_composer=False,
        pause_for_debugger=False,
        performance_mode=True,
        concurrency=1,
    )
    profile_dir = profile_dir_for(args.profile)
    profile_dir.mkdir(parents=True, exist_ok=True)
    context = await build_context(profile_dir=profile_dir, account_config=make_account_config(profile_dir), options=options)
    page = await close_all_pages(context)
    try:
        await page.goto(ZALO_HOME, wait_until="domcontentloaded", timeout=60000)
        if not await ensure_logged_in(page):
            raise RuntimeError("Het thoi gian cho dang nhap QR")

        state = load_state()
        # Ignore chi co hieu luc TRONG NGAY (rule: chi tinh user da rep hom nay).
        # Entry cua ngay cu bi loai bo de hom sau khach nhan keyword van duoc tool tra loi.
        today_str = datetime.now().strftime("%Y-%m-%d")
        ignored = {k: v for k, v in load_ignored().items() if v.startswith(today_str)}
        save_ignored(ignored)
        if ignored:
            print(f"[INFO] Ignore list (user da tu tra loi hom nay): {len(ignored)} hoi thoai.")
        keywords = load_keywords()
        if keywords:
            print(f"[INFO] Loc theo {len(keywords)} keyword trong {KEYWORDS_FILE.name} "
                  f"(sua file roi chay lai tool de ap dung).")
        else:
            print(f"[WARN] {KEYWORDS_FILE.name} rong/khong co -> reply TAT CA tin nguoi la.")
        print(f"[INFO] Da dang nhap. Bat dau vong lap quet moi {POLL_SECONDS}s "
              f"(da reply truoc day: {len(state)} hoi thoai).")
        scan_failures = 0
        skip_snippets: dict[str, str] = {}
        idle_announced = False
        while True:
            if not args.all_hours and not in_active_window():
                if not idle_announced:
                    print(f"[INFO] Ngoai khung gio {ACTIVE_START_HOUR}h-{ACTIVE_END_HOUR}h, tam nghi toi {ACTIVE_START_HOUR}h...")
                    idle_announced = True
                await page.wait_for_timeout(60 * 1000)
                continue
            if idle_announced:
                print(f"[INFO] Vao khung gio hoat dong ({ACTIVE_START_HOUR}h-{ACTIVE_END_HOUR}h), tiep tuc quet.")
                idle_announced = False
            try:
                sent = await scan_and_reply(page, state, ignored, dry_run=args.dry_run,
                                            keywords=keywords, skip_snippets=skip_snippets)
                scan_failures = 0
                if sent:
                    print(f"[INFO] Luot nay reply {sent} hoi thoai.")
            except Exception as exc:
                scan_failures += 1
                print(f"[WARN] Luot quet loi ({scan_failures}/{MAX_SCAN_FAILURES}): {str(exc)[:200]}")
                if scan_failures >= MAX_SCAN_FAILURES:
                    raise
            await page.wait_for_timeout(POLL_SECONDS * 1000)
    finally:
        try:
            await page.close()
        except Exception:
            pass
        await close_context(context)


async def setup_mode(args: argparse.Namespace) -> None:
    """Dang nhap QR lan dau + dump DOM de chot selector."""
    options = RuntimeOptions(
        headless=False,
        dry_run=False,
        debug_composer=False,
        pause_for_debugger=False,
        performance_mode=True,
        concurrency=1,
    )
    profile_dir = profile_dir_for(args.profile)
    profile_dir.mkdir(parents=True, exist_ok=True)
    context = await build_context(profile_dir=profile_dir, account_config=make_account_config(profile_dir), options=options)
    page = await close_all_pages(context)
    try:
        await page.goto(ZALO_HOME, wait_until="domcontentloaded", timeout=60000)
        if not await ensure_logged_in(page):
            print("[ERROR] Chua dang nhap duoc. Chay lai --setup va quet QR.")
            return
        print("[INFO] Da dang nhap. Cho trang on dinh 5s roi dump DOM...")
        await page.wait_for_timeout(5000)
        dump = await page.evaluate(DUMP_JS)
        DOM_DUMP_FILE.write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
        shot = LOG_DIR / "zalo_setup.png"
        await page.screenshot(path=str(shot), full_page=False)
        print(f"[OK] DOM dump: {DOM_DUMP_FILE}")
        print(f"[OK] Screenshot: {shot}")
        print("[OK] Setup xong. Tu gio co the chay thuong: Zalo\\run_zalo_auto_reply.bat")
    finally:
        try:
            await page.close()
        except Exception:
            pass
        await close_context(context)


def main() -> int:
    configure_stdout_for_windows()
    args = parse_args()
    if args.setup:
        return asyncio.run(setup_mode(args))

    async def supervise() -> None:
        while True:
            try:
                await run_session(args)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[ERROR] Session loi: {str(exc)[:300]}. Khoi dong lai sau {CRASH_BACKOFF_SECONDS}s...")
                await asyncio.sleep(CRASH_BACKOFF_SECONDS)

    try:
        asyncio.run(supervise())
    except KeyboardInterrupt:
        print("\n[INFO] Dung boi nguoi dung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import streamlit as st
import re
import csv
import io
import requests
import urllib.parse
from datetime import datetime, timezone
from typing import Tuple, List, Optional, Dict
from zoneinfo import ZoneInfo
import unicodedata
import pandas as pd

# ==============================
# 基本設定
# ==============================
st.set_page_config(page_title="タイムスタンプCSV出力", layout="centered")

st.title("タイムスタンプ出力ツール")
st.write("タイムスタンプCSV生成")

DATE_TITLE_SEPARATOR = " "
TZ_NAME = "Asia/Tokyo"

YT_API_BASE = "https://www.googleapis.com/youtube/v3"

GLOBAL_API_KEY = st.secrets.get("YT_API_KEY", "")

COMMENT_ORDER_LABELS: Dict[str, str] = {
    "関連度順（おすすめコメント優先）": "relevance",
    "新しい順（投稿時刻が新しい順）": "time",
}

# ==============================
# 共通ユーティリティ
# ==============================
def resolve_api_key() -> str:
    """resolve_api_key の責務を実行する。"""
    shared_key = st.session_state.get("shared_api_key", "") or ""
    if not shared_key and GLOBAL_API_KEY:
        st.session_state["shared_api_key"] = GLOBAL_API_KEY
        shared_key = GLOBAL_API_KEY
    return (shared_key or "").strip()


def yt_get_json(path: str, params: Dict, timeout: int = 10) -> Optional[dict]:
    """yt_get_json の責務を実行する。"""
    try:
        r = requests.get(f"{YT_API_BASE}/{path.lstrip('/')}", params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def yt_get_json_verbose(path: str, params: Dict, timeout: int = 10) -> Tuple[Optional[dict], Optional[str]]:
    """yt_get_json_verbose の責務を実行する。"""
    try:
        r = requests.get(f"{YT_API_BASE}/{path.lstrip('/')}", params=params, timeout=timeout)
        if r.status_code != 200:
            reason = ""
            try:
                j = r.json()
                err = j.get("error", {}) or {}
                msg = err.get("message") or ""
                errs = err.get("errors") or []
                if errs and isinstance(errs, list):
                    reason = (errs[0] or {}).get("reason") or ""
            except Exception:
                msg = r.text[:300] if r.text else ""
            return None, explain_youtube_api_error(r.status_code, msg, reason)
        return r.json(), None
    except Exception as e:
        return None, explain_youtube_api_exception(e)


def explain_youtube_api_error(status_code: int, message: str, reason: str = "") -> str:
    """explain_youtube_api_error の責務を実行する。"""
    msg = (message or "").strip()
    rsn = (reason or "").strip()
    quota_reasons = {
        "quotaExceeded",
        "dailyLimitExceeded",
        "dailyLimitExceededUnreg",
        "rateLimitExceeded",
        "userRateLimitExceeded",
    }
    auth_reasons = {
        "keyInvalid",
        "accessNotConfigured",
        "forbidden",
        "insufficientPermissions",
    }

    if status_code in (429,) or rsn in quota_reasons:
        return f"APIクオータ上限の可能性があります（HTTP {status_code}: {msg or rsn or '詳細不明'}）"
    if status_code == 403 and any(k in msg.lower() for k in ["quota", "rate", "limit"]):
        return f"APIクオータ上限の可能性があります（HTTP {status_code}: {msg}）"
    if status_code in (401, 403) and rsn in auth_reasons:
        return f"APIキーまたは権限設定の問題の可能性があります（HTTP {status_code}: {msg or rsn}）"
    return f"YouTube APIエラーです（HTTP {status_code}: {msg or rsn or '詳細不明'}）"


def explain_youtube_api_exception(exc: Exception) -> str:
    """explain_youtube_api_exception の責務を実行する。"""
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if any(k in lowered for k in ["name or service not known", "temporary failure in name resolution", "nodename nor servname", "connection", "timeout", "timed out", "ssl"]):
        return f"ネットワーク接続の問題の可能性があります（{text}）"
    return f"通信中にエラーが発生しました（{text}）"


def to_csv(rows: List[List[str]]) -> str:
    """to_csv の責務を実行する。"""
    buf = io.StringIO()
    csv.writer(buf, quoting=csv.QUOTE_ALL).writerows(rows)
    return buf.getvalue()


def sanitize_download_filename(video_title: str, default_name: str = "youtube_song_list") -> str:
    """sanitize_download_filename の責務を実行する。"""
    download_name = re.sub(r'[\\/:*?"<>|\x00-\x1F]', "_", video_title or "").strip().strip(".") or default_name
    return download_name[:100]


def save_csv_to_session(rows: List[List[str]], file_name: str) -> None:
    """save_csv_to_session の責務を実行する。"""
    csv_content = to_csv(rows)
    st.session_state["ts_csv_bytes"] = csv_content.encode("utf-8-sig")
    st.session_state["ts_csv_name"] = file_name


def make_excel_hyperlink(url_: str, label: str) -> str:
    """make_excel_hyperlink の責務を実行する。"""
    safe = (label or "").replace('"', '""')
    return f'=HYPERLINK("{url_}","{safe}")'


def classify_content_label(
    has_timestamps: bool,
    video_url: str = "",
    video_title: str = "",
    duration_seconds: Optional[int] = None,
) -> str:
    """classify_content_label の責務を実行する。"""
    if has_timestamps:
        return "歌枠"

    if duration_seconds is not None and duration_seconds <= 61:
        return "ショート"

    url_lower = (video_url or "").lower()
    if "/shorts/" in url_lower:
        return "ショート"

    title_raw = video_title or ""
    title_lower = title_raw.lower()
    if "#shorts" in title_lower or "shorts" in title_lower or "ショート" in title_raw:
        return "ショート"

    return "歌ってみた"


def is_valid_youtube_url(u: str) -> bool:
    """is_valid_youtube_url の責務を実行する。"""
    return bool(re.match(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$", u or ""))


def normalize_text(s: str) -> str:
    """normalize_text の責務を実行する。"""
    s = (s or "").replace("／", "/")
    s = s.replace("　", " ").strip()
    return re.sub(r"\s+", " ", s)


def extract_video_id(u: str) -> Optional[str]:
    """extract_video_id の責務を実行する。"""
    if not u:
        return None
    try:
        pr = urllib.parse.urlparse(u)
        host = (pr.netloc or "").lower()
        path = pr.path or ""
        qs = urllib.parse.parse_qs(pr.query or "")
        if "youtu.be" in host:
            seg = path.strip("/").split("/")
            return seg[0] if seg and seg[0] else None
        if "youtube.com" in host:
            if "v" in qs and qs["v"]:
                return qs["v"][0]
            if path.startswith("/shorts/"):
                after = path.split("/shorts/", 1)[1]
                return after.split("/")[0].split("?")[0]
        return None
    except Exception:
        return None


def extract_playlist_id(u: str) -> Optional[str]:
    """extract_playlist_id の責務を実行する。"""
    if not u:
        return None
    try:
        pr = urllib.parse.urlparse(u)
        qs = urllib.parse.parse_qs(pr.query or "")
        vals = qs.get("list") or []
        return vals[0] if vals and vals[0] else None
    except Exception:
        return None


def normalize_manual_date_input(raw: str, tz_name: str) -> Optional[str]:
    """normalize_manual_date_input の責務を実行する。"""
    s = (raw or "").strip()
    if not s:
        return None

    s = unicodedata.normalize("NFKC", s)
    s = s.replace("年", "/").replace("月", "/").replace("日", "")
    s = re.sub(r"[.\-]", "/", s)
    s = re.sub(r"\s+", "/", s)
    s = s.strip("/")

    if re.fullmatch(r"\d{8}", s):
        y, m, d = int(s[0:4]), int(s[4:6]), int(s[6:8])
    else:
        parts = s.split("/")
        if len(parts) == 3:
            try:
                y, m, d = map(int, parts)
            except ValueError:
                return None
        elif len(parts) == 2:
            today = datetime.now(ZoneInfo(tz_name)).date()
            y = today.year
            try:
                m, d = map(int, parts)
            except ValueError:
                return None
        else:
            return None

    if y < 100:
        y += 2000

    try:
        dt = datetime(y, m, d)
    except ValueError:
        return None

    return dt.strftime("%Y%m%d")


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_video_title_from_oembed(watch_url: str) -> str:
    """fetch_video_title_from_oembed の責務を実行する。"""
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": watch_url, "format": "json"},
            timeout=6
        )
        if r.status_code == 200:
            title = (r.json().get("title") or "").strip()
            return title if title else "YouTube動画"
    except Exception:
        pass
    return "YouTube動画"


def iso_utc_to_tz_epoch_and_yyyymmdd(iso_str: str, tz_name: str) -> Tuple[Optional[int], Optional[str]]:
    """iso_utc_to_tz_epoch_and_yyyymmdd の責務を実行する。"""
    if not iso_str:
        return None, None
    try:
        s = iso_str
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt_utc = datetime.fromisoformat(s)
        dt_local = dt_utc.astimezone(ZoneInfo(tz_name))
        return int(dt_local.timestamp()), dt_local.strftime("%Y%m%d")
    except Exception:
        return None, None


def iso_utc_to_tz_yyyymmdd(iso_str: str, tz_name: str) -> Optional[str]:
    """iso_utc_to_tz_yyyymmdd の責務を実行する。"""
    _, ymd = iso_utc_to_tz_epoch_and_yyyymmdd(iso_str, tz_name)
    return ymd


def resolve_display_date(video_id: str, manual_yyyymmdd: str, api_key: str, tz_name: str) -> Tuple[Optional[str], Optional[str]]:
    """resolve_display_date の責務を実行する。"""
    if manual_yyyymmdd and re.fullmatch(r"\d{8}", manual_yyyymmdd):
        return manual_yyyymmdd, "manual"

    if api_key:
        date_info = fetch_best_display_date_and_sources(video_id, api_key, tz_name)
        return date_info.get("chosen_yyyymmdd"), date_info.get("source")

    return None, None


def build_display_name(video_title: str, date_yyyymmdd: Optional[str]) -> str:
    """build_display_name の責務を実行する。"""
    return f"{date_yyyymmdd}{DATE_TITLE_SEPARATOR}{video_title}" if date_yyyymmdd else video_title


# ==============================
# タブ1：タイムスタンプCSVジェネレーター用関数
# ==============================
TIMESTAMP_START_RE = re.compile(r"^\s*(?:[-*•▶▷\u25CF\u25A0\u25B6\u25B7\u30FB]\s*)*(\d{1,2}:)?(\d{1,2}):(\d{2})\b")


def _strip_leading_glyphs(line: str) -> str:
    """_strip_leading_glyphs の責務を実行する。"""
    return re.sub(r"^\s*(?:[-*•▶▷\u25CF\u25A0\u25B6\u25B7\u30FB]\s*)+", "", line or "")


def parse_line(line: str, flip: bool) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """parse_line の責務を実行する。"""
    m = re.match(r"^(\d{1,2}:)?(\d{1,2}):(\d{2})", _strip_leading_glyphs(line))
    if not m:
        return (None, None, None)
    time_str = m.group(0)
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 3:
        seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
    else:
        seconds = parts[0] * 60 + parts[1]
    info = _strip_leading_glyphs(line)[len(time_str):].strip()

    msep = re.search(r"\s(-|—|–|―|－|/|／|by|BY)\s", info)
    if msep:
        left  = normalize_text(info[:msep.start()].strip())
        right = normalize_text(info[msep.end():].strip())
        if not flip:
            artist, song = right or "N/A", left or "N/A"
        else:
            artist, song = left or "N/A", right or "N/A"
        return (seconds, artist, song)

    return (seconds, "N/A", normalize_text(info) or "N/A")


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_best_display_date_and_sources(video_id: str, api_key: str, tz_name: str) -> Dict[str, Optional[str]]:
    """fetch_best_display_date_and_sources の責務を実行する。"""
    result: Dict[str, Optional[str]] = {"chosen_yyyymmdd": None, "source": None}
    if not api_key:
        return result

    data = yt_get_json(
        "videos",
        {"part": "snippet,liveStreamingDetails", "id": video_id, "key": api_key},
        timeout=10
    )
    if not data:
        return result
    items = (data or {}).get("items", [])
    if not items:
        return result

    item = items[0]
    snippet = item.get("snippet", {}) or {}
    live = item.get("liveStreamingDetails", {}) or {}

    publishedAt = snippet.get("publishedAt")
    actualStartTime = live.get("actualStartTime")
    scheduledStartTime = live.get("scheduledStartTime")

    publishedAt_local = iso_utc_to_tz_yyyymmdd(publishedAt, tz_name) if publishedAt else None
    actualStartTime_local = iso_utc_to_tz_yyyymmdd(actualStartTime, tz_name) if actualStartTime else None
    scheduledStartTime_local = iso_utc_to_tz_yyyymmdd(scheduledStartTime, tz_name) if scheduledStartTime else None

    if actualStartTime_local:
        result["chosen_yyyymmdd"] = actualStartTime_local
        result["source"] = "actualStartTime"
    elif scheduledStartTime_local:
        result["chosen_yyyymmdd"] = scheduledStartTime_local
        result["source"] = "scheduledStartTime"
    elif publishedAt_local:
        result["chosen_yyyymmdd"] = publishedAt_local
        result["source"] = "publishedAt"

    return result


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_video_channel_id(video_id: str, api_key: str) -> Optional[str]:
    """fetch_video_channel_id の責務を実行する。"""
    if not api_key or not video_id:
        return None
    data = yt_get_json(
        "videos",
        {"part": "snippet", "id": video_id, "key": api_key},
        timeout=10
    )
    if not data or not data.get("items"):
        return None
    snip = data["items"][0].get("snippet", {}) or {}
    return snip.get("channelId")


def _count_timestamp_lines(text: str) -> int:
    """_count_timestamp_lines の責務を実行する。"""
    n = 0
    for raw in (text or "").splitlines():
        s = normalize_text(raw)
        if not s:
            continue
        if TIMESTAMP_START_RE.match(s):
            n += 1
    return n


def _extract_timestamp_lines(text: str, flip: bool) -> str:
    """_extract_timestamp_lines の責務を実行する。"""
    out = []
    for raw in (text or "").splitlines():
        s = normalize_text(raw)
        if not s:
            continue
        sec, _, _ = parse_line(s, flip)
        if sec is not None:
            out.append(_strip_leading_glyphs(raw).strip())
    return "\n".join(out).strip()


def _split_lines_for_bulk_editor(text: str) -> List[str]:
    """_split_lines_for_bulk_editor の責務を実行する。"""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return lines if lines else [""]


@st.cache_data(show_spinner=False, ttl=300)
def fetch_timestamp_comment_candidates(
    video_id: str,
    api_key: str,
    order: str = "relevance",
    search_terms: str = "",
    max_pages: int = 3,
) -> Tuple[List[dict], Optional[str]]:
    """fetch_timestamp_comment_candidates の責務を実行する。"""
    if not api_key:
        return [], "APIキーが必要です。"
    if not video_id:
        return [], "videoId が空です。"

    owner_channel_id = fetch_video_channel_id(video_id, api_key)

    candidates: List[dict] = []
    page_token = None
    pages = 0

    while pages < max_pages:
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": 100,
            "order": order,
            "textFormat": "plainText",
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token
        if (search_terms or "").strip():
            params["searchTerms"] = (search_terms or "").strip()

        data, err = yt_get_json_verbose("commentThreads", params, timeout=10)
        if err:
            return [], f"commentThreads.list 失敗: {err}"
        if not data:
            break

        items = data.get("items", []) or []
        for it in items:
            sn = it.get("snippet", {}) or {}
            tlc = sn.get("topLevelComment", {}) or {}
            tlc_sn = (tlc.get("snippet", {}) or {})

            text = (tlc_sn.get("textDisplay") or "").strip()
            if not text:
                continue

            like_count = int(tlc_sn.get("likeCount") or 0)
            author_ch_obj = tlc_sn.get("authorChannelId") or {}
            author_channel_id = author_ch_obj.get("value") if isinstance(author_ch_obj, dict) else None

            ts_lines = _count_timestamp_lines(text)
            if ts_lines <= 0:
                continue

            is_owner = bool(owner_channel_id and author_channel_id and owner_channel_id == author_channel_id)

            score = ts_lines * 10
            if is_owner:
                score += 60
            score += min(like_count, 500) / 10.0

            candidates.append({
                "score": score,
                "ts_lines": ts_lines,
                "likeCount": like_count,
                "is_owner": is_owner,
                "authorChannelId": author_channel_id or "",
                "text": text,
                "commentId": tlc.get("id", ""),
                "publishedAt": tlc_sn.get("publishedAt", ""),
            })

        page_token = data.get("nextPageToken")
        pages += 1
        if not page_token:
            break

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates, None


def generate_rows(
    u: str,
    timestamps_text: str,
    tz_name: str,
    api_key: str,
    manual_yyyymmdd: str,
    flip: bool
) -> Tuple[List[List[str]], List[dict], List[str], str]:
    """generate_rows の責務を実行する。"""
    vid = extract_video_id(u)
    if not vid:
        raise ValueError("URLからビデオIDを抽出できませんでした。")
    base_watch = f"https://www.youtube.com/watch?v={vid}"

    video_title = fetch_video_title_from_oembed(base_watch)

    date_yyyymmdd, date_source = resolve_display_date(vid, manual_yyyymmdd, api_key, tz_name)
    display_name = build_display_name(video_title, date_yyyymmdd)

    rows: List[List[str]] = [["アーティスト名", "楽曲名", "", "YouTubeリンク"]]
    parsed_preview: List[dict] = []
    invalid_lines: List[str] = []

    for raw in (timestamps_text or "").splitlines():
        line = normalize_text(raw)
        if not line:
            continue
        sec, artist, song = parse_line(line, flip)
        if sec is None:
            invalid_lines.append(raw)
            continue

        jump = f"{base_watch}&t={sec}s"
        hyperlink = make_excel_hyperlink(jump, display_name)
        content_label = classify_content_label(
            has_timestamps=True,
            video_url=base_watch,
            video_title=video_title,
        )
        rows.append([artist, song, content_label, hyperlink])
        parsed_preview.append({
            "time_seconds": sec,
            "artist": artist,
            "song": song,
            "display_name": display_name,
            "date_source": date_source,
            "hyperlink_formula": hyperlink,
        })

    if len(rows) == 1:
        raise ValueError("有効なタイムスタンプ付きの楽曲データが見つかりませんでした。")

    return rows, parsed_preview, invalid_lines, video_title


def parse_unique_video_urls(raw_text: str) -> List[str]:
    """parse_unique_video_urls の責務を実行する。"""
    urls: List[str] = []
    seen = set()
    for raw in (raw_text or "").splitlines():
        line = (raw or "").strip()
        if not line:
            continue
        vid = extract_video_id(line)
        if not vid:
            continue
        watch = f"https://www.youtube.com/watch?v={vid}"
        if watch in seen:
            continue
        seen.add(watch)
        urls.append(watch)
    return urls


def build_multi_video_rows(
    items: Dict[str, dict],
    ordered_video_ids: List[str],
    tz_name: str,
    api_key: str,
    manual_yyyymmdd: str,
    flip: bool,
) -> Tuple[List[List[str]], List[str]]:
    """build_multi_video_rows の責務を実行する。"""
    rows: List[List[str]] = [["アーティスト名", "楽曲名", "", "YouTubeリンク"]]
    warnings: List[str] = []
    duration_by_video_id: Dict[str, int] = {}

    if api_key and ordered_video_ids:
        metas = fetch_video_meta(ordered_video_ids, api_key)
        duration_by_video_id = {
            m.get("videoId"): int(m.get("seconds") or 0)
            for m in metas
            if m.get("videoId")
        }

    for vid in ordered_video_ids:
        it = items.get(vid) or {}
        video_url = (it.get("url") or "").strip()
        ts_text = (it.get("applied_text") or "").strip()
        if not video_url:
            warnings.append(f"{vid}: 動画URLが空のためスキップ")
            continue

        if not ts_text:
            title = (it.get("title") or "").strip() or fetch_video_title_from_oembed(video_url)
            date_yyyymmdd, _ = resolve_display_date(vid, manual_yyyymmdd, api_key, tz_name)

            link = f"https://www.youtube.com/watch?v={vid}"
            label = build_display_name(title, date_yyyymmdd)
            hyperlink = make_excel_hyperlink(link, label)
            artist, song = split_artist_song_from_title(title)
            content_label = classify_content_label(
                has_timestamps=False,
                video_url=video_url,
                video_title=title,
                duration_seconds=duration_by_video_id.get(vid),
            )
            rows.append([artist, song, content_label, hyperlink])
            warnings.append(f"{vid}: タイムスタンプ未入力のため、動画タイトルから1行生成")
            continue

        try:
            single_rows, _, invalid, _ = generate_rows(
                video_url, ts_text, tz_name, api_key, manual_yyyymmdd, flip
            )
            for r in single_rows[1:]:
                rows.append([r[0], r[1], r[2], r[3]])
            if invalid:
                warnings.append(f"{vid}: 未解析行 {len(invalid)} 件")
        except Exception as e:
            warnings.append(f"{vid}: 生成失敗（{e}）")

    if len(rows) == 1:
        raise ValueError("出力可能な行がありません。各動画でタイムスタンプテキストを確認してください。")

    return rows, warnings


def build_multi_video_preview(
    items: Dict[str, dict],
    ordered_video_ids: List[str],
    tz_name: str,
    api_key: str,
    manual_yyyymmdd: str,
    flip: bool,
) -> Tuple[List[dict], List[str], List[str]]:
    """build_multi_video_preview の責務を実行する。"""
    preview_rows: List[dict] = []
    invalid_lines: List[str] = []
    warnings: List[str] = []

    for vid in ordered_video_ids:
        it = items.get(vid) or {}
        video_url = (it.get("url") or "").strip()
        ts_text = (it.get("applied_text") or "").strip()
        if not video_url:
            warnings.append(f"{vid}: 動画URLが空のためスキップ")
            continue

        if not ts_text:
            title = (it.get("title") or "").strip() or fetch_video_title_from_oembed(video_url)
            date_yyyymmdd, date_source = resolve_display_date(vid, manual_yyyymmdd, api_key, tz_name)
            display_name = build_display_name(title, date_yyyymmdd)
            artist, song = split_artist_song_from_title(title)
            preview_rows.append({
                "video_id": vid,
                "video_url": video_url,
                "time_seconds": None,
                "artist": artist,
                "song": song,
                "display_name": display_name,
                "date_source": date_source or "",
            })
            warnings.append(f"{vid}: タイムスタンプ未入力のため、動画タイトルから1行プレビュー生成")
            continue

        try:
            _, parsed_preview, invalid, _ = generate_rows(
                video_url, ts_text, tz_name, api_key, manual_yyyymmdd, flip
            )
            for p in parsed_preview:
                preview_rows.append({
                    "video_id": vid,
                    "video_url": video_url,
                    "time_seconds": p.get("time_seconds"),
                    "artist": p.get("artist"),
                    "song": p.get("song"),
                    "display_name": p.get("display_name"),
                    "date_source": p.get("date_source"),
                })
            if invalid:
                invalid_lines.extend([f"[{vid}] {line}" for line in invalid])
                warnings.append(f"{vid}: 未解析行 {len(invalid)} 件")
        except Exception as e:
            warnings.append(f"{vid}: 解析失敗（{e}）")

    if not preview_rows:
        raise ValueError("プレビュー可能な行がありません。各動画のタイムスタンプテキストを確認してください。")

    return preview_rows, invalid_lines, warnings


def apply_row_swap_flags(preview_rows: List[dict], swap_flags: List[bool]) -> List[dict]:
    """apply_row_swap_flags の責務を実行する。"""
    adjusted_rows: List[dict] = []
    for i, row in enumerate(preview_rows):
        copied = dict(row)
        if i < len(swap_flags) and swap_flags[i]:
            copied["artist"], copied["song"] = copied.get("song", "N/A"), copied.get("artist", "N/A")
        adjusted_rows.append(copied)
    return adjusted_rows


def apply_row_swap_flags_to_csv_rows(rows: List[List[str]], swap_flags: List[bool]) -> List[List[str]]:
    """apply_row_swap_flags_to_csv_rows の責務を実行する。"""
    if not rows:
        return rows
    adjusted_rows: List[List[str]] = [rows[0]]
    for i, row in enumerate(rows[1:]):
        copied = list(row)
        if i < len(swap_flags) and swap_flags[i] and len(copied) >= 2:
            copied[0], copied[1] = copied[1], copied[0]
        adjusted_rows.append(copied)
    return adjusted_rows


def extract_url_and_label_from_hyperlink_formula(formula: str) -> Tuple[str, str]:
    """extract_url_and_label_from_hyperlink_formula の責務を実行する。"""
    m = re.match(r'^=HYPERLINK\("([^"]+)"\s*,\s*"([^"]*)"\)$', (formula or "").strip(), flags=re.IGNORECASE)
    if not m:
        return "", ""
    return m.group(1), m.group(2)


def build_csv_result_preview_rows(rows: List[List[str]]) -> List[dict]:
    """build_csv_result_preview_rows の責務を実行する。"""
    out: List[dict] = []
    for row in (rows or [])[1:]:
        if len(row) < 4:
            continue
        url, label = extract_url_and_label_from_hyperlink_formula(row[3])
        out.append({
            "アーティスト名": row[0],
            "楽曲名": row[1],
            "区分": row[2],
            "リンク表示名": label,
            "YouTubeリンク": url,
        })
    return out


# ==============================
# tab1: コールバック（重要：widget key を安全に更新する）
# ==============================
def _get_ts_api_key() -> str:
    """_get_ts_api_key の責務を実行する。"""
    return resolve_api_key()


def _get_manual_yyyymmdd() -> str:
    """_get_manual_yyyymmdd の責務を実行する。"""
    raw = (st.session_state.get("ts_manual_date_raw", "") or "").strip()
    if not raw:
        return ""
    normalized = normalize_manual_date_input(raw, TZ_NAME)
    return normalized or ""


def _clear_ts_preview_state(clear_csv: bool = False) -> None:
    """_clear_ts_preview_state の責務を実行する。"""
    for k in ["ts_preview_df", "ts_preview_invalid", "ts_preview_title", "ts_last_rows", "ts_row_swap_flags"]:
        st.session_state.pop(k, None)

    if clear_csv:
        st.session_state.pop("ts_csv_bytes", None)
        st.session_state.pop("ts_csv_name", None)


def _set_preview_from_text(url: str, ts_text: str) -> None:
    """_set_preview_from_text の責務を実行する。"""
    flip = st.session_state.get("flip_ts", False)
    api_key = _get_ts_api_key()
    manual_date = _get_manual_yyyymmdd()

    _clear_ts_preview_state()

    rows, preview, invalid, video_title = generate_rows(
        url, ts_text, TZ_NAME, api_key, manual_date, flip
    )
    st.session_state["ts_preview_df"] = preview
    st.session_state["ts_row_swap_flags"] = [False] * len(preview)
    st.session_state["ts_preview_invalid"] = invalid
    st.session_state["ts_preview_title"] = video_title
    st.session_state["ts_auto_msg"] = f"プレビュー生成：解析 {len(preview)} 件 / 未解析 {len(invalid)} 件"
    st.session_state.pop("ts_auto_err", None)


def _apply_comment_text(comment_text: str, do_preview: bool) -> None:
    """_apply_comment_text の責務を実行する。"""
    flip = st.session_state.get("flip_ts", False)
    ts_text = comment_text or ""

    if st.session_state.get("ts_auto_only_ts_lines", True):
        extracted = _extract_timestamp_lines(ts_text, flip)
        if extracted:
            ts_text = extracted

    # callback 内で widget key を更新する（ここが肝）
    st.session_state["timestamps_input_ts"] = ts_text

    if do_preview:
        url = (st.session_state.get("ts_url", "") or "").strip()
        try:
            _set_preview_from_text(url, ts_text)
        except Exception as e:
            st.session_state["ts_auto_err"] = f"プレビュー生成に失敗しました：{e}"
            st.session_state.pop("ts_auto_msg", None)


def cb_fetch_candidates(do_autoselect_preview: bool) -> None:
    """cb_fetch_candidates の責務を実行する。"""
    url = (st.session_state.get("ts_url", "") or "").strip()
    if not url or not is_valid_youtube_url(url):
        st.session_state["ts_auto_err"] = "URLが空、または無効です。"
        return

    api_key = _get_ts_api_key()
    if not api_key:
        st.session_state["ts_auto_err"] = "コメント自動取得はAPIキー必須です。"
        return

    vid = extract_video_id(url)
    if not vid:
        st.session_state["ts_auto_err"] = "URLからビデオIDを抽出できませんでした。"
        return

    order = st.session_state.get("ts_auto_order", "relevance")
    terms = st.session_state.get("ts_auto_search_terms", "")
    pages = int(st.session_state.get("ts_auto_pages", 3))

    cands, err = fetch_timestamp_comment_candidates(
        video_id=vid,
        api_key=api_key,
        order=order,
        search_terms=terms,
        max_pages=pages,
    )
    if err:
        st.session_state["ts_auto_err"] = err
        st.session_state["ts_auto_candidates"] = []
        return

    st.session_state["ts_auto_candidates"] = cands
    st.session_state["ts_auto_msg"] = f"コメント候補取得：{len(cands)} 件"
    st.session_state.pop("ts_auto_err", None)

    if do_autoselect_preview and cands:
        _apply_comment_text(cands[0]["text"], do_preview=True)


def cb_apply_candidate(index: int, do_preview: bool) -> None:
    """cb_apply_candidate の責務を実行する。"""
    cands = st.session_state.get("ts_auto_candidates", []) or []
    if not cands or index < 0 or index >= len(cands):
        st.session_state["ts_auto_err"] = "候補がありません（先に「コメント候補を取得」してください）。"
        return
    _apply_comment_text(cands[index]["text"], do_preview=do_preview)


def cb_fetch_multi_video_candidates() -> None:
    """cb_fetch_multi_video_candidates の責務を実行する。"""
    raw = st.session_state.get("ts_multi_urls", "") or ""
    urls = parse_unique_video_urls(raw)
    st.session_state["ts_multi_url_list"] = urls
    st.session_state["ts_multi_order"] = [extract_video_id(u) for u in urls if extract_video_id(u)]

    if not urls:
        st.session_state["ts_multi_err"] = "有効なYouTube URLが見つかりませんでした。"
        st.session_state["ts_multi_items"] = {}
        return

    api_key = _get_ts_api_key()
    if not api_key:
        st.session_state["ts_multi_err"] = "複数動画のコメント自動取得にはAPIキーが必要です。"
        st.session_state["ts_multi_items"] = {}
        return

    order = st.session_state.get("ts_auto_order", "relevance")
    terms = st.session_state.get("ts_auto_search_terms", "")
    pages = int(st.session_state.get("ts_auto_pages", 3))

    items: Dict[str, dict] = {}
    fail_count = 0
    for u in urls:
        vid = extract_video_id(u)
        if not vid:
            fail_count += 1
            continue
        video_title = fetch_video_title_from_oembed(u)
        cands, err = fetch_timestamp_comment_candidates(
            video_id=vid,
            api_key=api_key,
            order=order,
            search_terms=terms,
            max_pages=pages,
        )
        if err:
            fail_count += 1
            items[vid] = {
                "url": u,
                "title": video_title,
                "candidates": [],
                "applied_text": "",
                "error": err,
            }
            continue

        default_text = cands[0]["text"] if cands else ""
        if st.session_state.get("ts_auto_only_ts_lines", True):
            extracted = _extract_timestamp_lines(default_text, st.session_state.get("flip_ts", False))
            if extracted:
                default_text = extracted

        items[vid] = {
            "url": u,
            "title": video_title,
            "candidates": cands,
            "applied_text": default_text,
            "error": "",
        }

    st.session_state["ts_multi_items"] = items
    st.session_state["ts_multi_msg"] = f"{len(items)}動画の候補取得完了（失敗 {fail_count}）"
    st.session_state.pop("ts_multi_err", None)


def cb_apply_multi_candidate(video_id: str, index: int) -> None:
    """cb_apply_multi_candidate の責務を実行する。"""
    items = st.session_state.get("ts_multi_items", {}) or {}
    it = items.get(video_id)
    if not it:
        return

    cands = it.get("candidates", []) or []
    if index < 0 or index >= len(cands):
        return

    picked_text = cands[index].get("text", "")
    if st.session_state.get("ts_auto_only_ts_lines", True):
        extracted = _extract_timestamp_lines(picked_text, st.session_state.get("flip_ts", False))
        if extracted:
            picked_text = extracted

    it["applied_text"] = picked_text
    items[video_id] = it
    st.session_state["ts_multi_items"] = items
    st.session_state[f"ts_multi_text_{video_id}"] = picked_text


def cb_refresh_multi_video_candidates() -> None:
    """cb_refresh_multi_video_candidates の責務を実行する。"""
    items = st.session_state.get("ts_multi_items", {}) or {}
    ordered_ids = st.session_state.get("ts_multi_order", []) or []
    if not items or not ordered_ids:
        st.session_state["ts_multi_err"] = "更新対象の動画がありません。"
        return

    api_key = _get_ts_api_key()
    if not api_key:
        st.session_state["ts_multi_err"] = "コメント更新にはAPIキーが必要です。"
        return

    order = st.session_state.get("ts_auto_order", "relevance")
    terms = st.session_state.get("ts_auto_search_terms", "")
    pages = int(st.session_state.get("ts_auto_pages", 3))

    refreshed_count = 0
    fail_count = 0
    for vid in ordered_ids:
        it = items.get(vid)
        if not it:
            continue

        url = (it.get("url") or f"https://www.youtube.com/watch?v={vid}").strip()
        video_id = extract_video_id(url) or vid
        cands, err = fetch_timestamp_comment_candidates(
            video_id=video_id,
            api_key=api_key,
            order=order,
            search_terms=terms,
            max_pages=pages,
        )

        if err:
            fail_count += 1
            it["error"] = err
            it["candidates"] = []
        else:
            refreshed_count += 1
            it["error"] = ""
            it["candidates"] = cands
            it["title"] = fetch_video_title_from_oembed(url)

        items[vid] = it

    st.session_state["ts_multi_items"] = items
    st.session_state["ts_multi_msg"] = f"コメント候補を再取得しました（成功 {refreshed_count} / 失敗 {fail_count}）"
    st.session_state.pop("ts_multi_err", None)


def cb_fetch_latest_multi_video_candidates() -> None:
    """cb_fetch_latest_multi_video_candidates の責務を実行する。"""
    api_key = _get_ts_api_key()
    if not api_key:
        st.session_state["ts_multi_latest_err"] = "最新動画取得にはAPIキーが必要です。"
        st.session_state["ts_multi_latest_candidates"] = []
        return

    channel_input = (st.session_state.get("ts_multi_channel_input", "") or "").strip()
    if not channel_input:
        st.session_state["ts_multi_latest_err"] = "チャンネルURLまたはチャンネルIDを入力してください。"
        st.session_state["ts_multi_latest_candidates"] = []
        return

    channel_id = resolve_channel_id_from_input(channel_input, api_key)
    if not channel_id:
        st.session_state["ts_multi_latest_err"] = "チャンネルIDを特定できませんでした。URLまたはIDを確認してください。"
        st.session_state["ts_multi_latest_candidates"] = []
        return

    latest_n = int(st.session_state.get("ts_multi_latest_n", 10))
    shorts_only = bool(st.session_state.get("ts_multi_shorts_only", False))
    fetch_n = min(max(latest_n * 4, latest_n), 200) if shorts_only else latest_n

    video_ids, latest_err = list_latest_video_ids_mixed_verbose(channel_id, api_key, fetch_n)
    if latest_err:
        st.session_state["ts_multi_latest_err"] = f"最新動画の取得に失敗しました。{latest_err}"
        st.session_state["ts_multi_latest_candidates"] = []
        return
    if not video_ids:
        st.session_state["ts_multi_latest_err"] = "最新動画を取得できませんでした。"
        st.session_state["ts_multi_latest_candidates"] = []
        return

    if shorts_only:
        metas = fetch_video_meta(video_ids, api_key)
        short_ids = [m.get("videoId") for m in metas if (m.get("seconds") or 0) <= 61 and m.get("videoId")]
        allowed = set(short_ids)
        video_ids = [vid for vid in video_ids if vid in allowed][:latest_n]
        if not video_ids:
            st.session_state["ts_multi_latest_err"] = "ショート動画（61秒以下）を取得できませんでした。"
            st.session_state["ts_multi_latest_candidates"] = []
            return
    else:
        video_ids = video_ids[:latest_n]

    details = fetch_titles_and_best_dates_bulk(video_ids, api_key, TZ_NAME)
    candidates = []
    for vid in video_ids:
        meta = details.get(vid, {})
        title = meta.get("title") or f"動画 {vid}"
        ymd = meta.get("yyyymmdd") or ""
        candidates.append(
            {
                "videoId": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": title,
                "yyyymmdd": ymd,
            }
        )

    st.session_state["ts_multi_latest_candidates"] = candidates
    st.session_state["ts_multi_latest_selected_ids"] = [c["videoId"] for c in candidates]
    suffix = "（ショートのみ）" if shorts_only else ""
    st.session_state["ts_multi_latest_msg"] = f"最新動画 {len(candidates)} 件を取得しました。{suffix}"
    st.session_state.pop("ts_multi_latest_err", None)


def cb_on_target_mode_change() -> None:
    """cb_on_target_mode_change の責務を実行する。"""
    st.session_state.pop("ts_multi_latest_err", None)


def cb_apply_latest_selection() -> None:
    """cb_apply_latest_selection の責務を実行する。"""
    target_mode = st.session_state.get("ts_target_mode")
    candidates = st.session_state.get("ts_multi_latest_candidates", []) or []
    id_to_url = {c.get("videoId"): c.get("url", "") for c in candidates}
    id_to_title = {c.get("videoId"): c.get("title", "") for c in candidates}

    if target_mode == "単体":
        selected_id = (st.session_state.get("ts_single_latest_selected_id", "") or "").strip()
        selected_url = id_to_url.get(selected_id, "").strip()
        if not selected_url:
            st.session_state["ts_multi_latest_err"] = "対象動画を1件選択してください。"
            return

        st.session_state["ts_url"] = selected_url
        st.session_state["ts_multi_latest_msg"] = "選択動画を反映しました。"
        st.session_state.pop("ts_multi_latest_err", None)
        return

    selected_ids = st.session_state.get("ts_multi_latest_selected_ids", []) or []
    selected_urls = [id_to_url.get(vid, "").strip() for vid in selected_ids if id_to_url.get(vid, "").strip()]
    if not selected_urls:
        st.session_state["ts_multi_latest_err"] = "対象動画を1件以上選択してください。"
        return

    st.session_state["ts_multi_urls"] = "\n".join(selected_urls)
    st.session_state["ts_multi_order"] = selected_ids
    items = st.session_state.get("ts_multi_items", {}) or {}
    for vid in selected_ids:
        if vid not in items:
            items[vid] = {
                "url": id_to_url.get(vid, ""),
                "title": id_to_title.get(vid, ""),
                "candidates": [],
                "applied_text": "",
                "error": "",
            }
        else:
            items[vid]["url"] = id_to_url.get(vid, items[vid].get("url", ""))
            items[vid]["title"] = id_to_title.get(vid, items[vid].get("title", ""))
    st.session_state["ts_multi_items"] = items
    st.session_state["ts_multi_latest_msg"] = f"{len(selected_urls)} 件のURLを反映しました。"
    st.session_state.pop("ts_multi_latest_err", None)


def cb_fetch_comment_candidates_by_mode() -> None:
    """cb_fetch_comment_candidates_by_mode の責務を実行する。"""
    if st.session_state.get("ts_target_mode") == "単体":
        cb_fetch_candidates(do_autoselect_preview=False)
    else:
        cb_fetch_multi_video_candidates()


# ==============================
# タブ2：Shorts → CSV 用関数
# ==============================
@st.cache_data(show_spinner=False, ttl=600)
def resolve_channel_id_from_input(channel_input: str, api_key: str) -> Optional[str]:
    """resolve_channel_id_from_input の責務を実行する。"""
    text = (channel_input or "").strip()
    if not text:
        return None

    if re.fullmatch(r"U[\w-]+", text):
        return text

    if not api_key:
        return None

    try:
        if text.startswith("http://") or text.startswith("https://"):
            pr = urllib.parse.urlparse(text)
            path = pr.path or ""
        else:
            path = text

        m = re.search(r"/channel/(U[\w-]+)", path)
        if m:
            return m.group(1)

        handle = ""
        m = re.search(r"/@([^/?#]+)", path)
        if m:
            handle = m.group(1)
        elif text.startswith("@") and len(text) > 1:
            handle = text[1:]

        if handle:
            data = yt_get_json(
                "channels",
                {"part": "id", "forHandle": f"@{handle}", "key": api_key},
                timeout=10
            )
            if data and data.get("items"):
                return data["items"][0].get("id")
            data2 = yt_get_json(
                "channels",
                {"part": "id", "forHandle": handle, "key": api_key},
                timeout=10
            )
            if data2 and data2.get("items"):
                return data2["items"][0].get("id")
            return None

        m = re.search(r"/user/([^/?#]+)", path)
        if m:
            username = m.group(1)
            data = yt_get_json(
                "channels",
                {"part": "id", "forUsername": username, "key": api_key},
                timeout=10
            )
            if data and data.get("items"):
                return data["items"][0].get("id")
            return None

        candidate = [p for p in path.split("/") if p][-1] if path else ""
        if candidate:
            data = yt_get_json(
                "search",
                {"part": "snippet", "type": "channel", "q": candidate, "maxResults": 5, "key": api_key},
                timeout=10
            )
            if data:
                for it in data.get("items", []):
                    ch_id = it.get("id", {}).get("channelId")
                    if ch_id:
                        return ch_id
        return None

    except Exception:
        return None


def iso8601_to_seconds(iso: str) -> int:
    """iso8601_to_seconds の責務を実行する。"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    h = int(m.group(1) or 0) if m else 0
    m_ = int(m.group(2) or 0) if m else 0
    s = int(m.group(3) or 0) if m else 0
    return h*3600 + m_*60 + s


def fetch_video_meta(video_ids: List[str], api_key: str):
    """fetch_video_meta の責務を実行する。"""
    out = []
    for i in range(0, len(video_ids), 50):
        chunk = ",".join(video_ids[i:i+50])
        data = yt_get_json(
            "videos",
            {"part": "snippet,contentDetails", "id": chunk, "key": api_key},
            timeout=10
        )
        if not data:
            continue
        for it in data.get("items", []):
            vid = it.get("id")
            snip = it.get("snippet", {}) or {}
            cdet = it.get("contentDetails", {}) or {}
            title = (snip.get("title") or "").strip()
            dur = iso8601_to_seconds(cdet.get("duration"))
            ymd = iso_utc_to_tz_yyyymmdd(snip.get("publishedAt", ""), TZ_NAME)
            out.append({"videoId": vid, "title": title, "seconds": dur, "yyyymmdd": ymd})
    return out


def clean_for_parse(s: str) -> str:
    """clean_for_parse の責務を実行する。"""
    s = (s or "").replace("／", "/")
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"#\S+", " ", s)
    s = re.sub(r"[【\[][^】\]]*[】\]]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_artist_song_from_title(title: str) -> Tuple[str, str]:
    """split_artist_song_from_title の責務を実行する。"""
    t = clean_for_parse(title)

    q = re.search(r'[「『“"](.+?)[」』”"]', t)
    if q:
        song = q.group(1).strip()
        artist = (t[:q.start()] + t[q.end():]).strip()
        artist = re.sub(r"^(?:-|—|–|―|－|/|／|by\s+)+", "", artist, flags=re.IGNORECASE)
        artist = re.sub(r"(?:\s+by|[-—–―－/／])$", "", artist, flags=re.IGNORECASE)
        artist = re.sub(r"\s+", " ", artist).strip()
        return artist if artist else "N/A", song if song else "N/A"

    m = re.search(r"\s(-|—|–|―|－|/|／|by|BY)\s", t)
    if m:
        left = t[:m.start()].strip()
        right = t[m.end():].strip()
        alpha_left = len(re.findall(r"[A-Za-z]", left))
        alpha_right = len(re.findall(r"[A-Za-z]", right))
        artist, song = (left, right) if alpha_left > alpha_right else (right, left)
        return artist or "N/A", song or "N/A"

    if "/" in t:
        if t.count("/") == 1 and not t.startswith("/") and not t.endswith("/"):
            left, right = [part.strip() for part in t.split("/", 1)]
            if left and right:
                alpha_left = len(re.findall(r"[A-Za-z]", left))
                alpha_right = len(re.findall(r"[A-Za-z]", right))
                artist, song = (left, right) if alpha_left > alpha_right else (right, left)
                return artist or "N/A", song or "N/A"

    return "N/A", t or "N/A"


@st.cache_data(show_spinner=False, ttl=600)
def list_latest_video_ids_mixed_verbose(channel_id: str, api_key: str, limit: int) -> Tuple[List[str], Optional[str]]:
    """list_latest_video_ids_mixed_verbose の責務を実行する。"""
    ids: List[str] = []
    token = None
    seen = set()

    while len(ids) < limit:
        params = {
            "part": "id",
            "channelId": channel_id,
            "type": "video",
            "order": "date",
            "maxResults": 50,
            "key": api_key,
        }
        if token:
            params["pageToken"] = token

        data, err = yt_get_json_verbose("search", params, timeout=10)
        if err:
            return [], err
        if not data:
            break

        for it in data.get("items", []):
            vid = it.get("id", {}).get("videoId")
            if vid and vid not in seen:
                seen.add(vid)
                ids.append(vid)
                if len(ids) >= limit:
                    break

        token = data.get("nextPageToken")
        if not token:
            break

    return ids[:limit], None


@st.cache_data(show_spinner=False, ttl=600)
def fetch_titles_and_best_dates_bulk(video_ids: List[str], api_key: str, tz_name: str) -> Dict[str, Dict[str, str]]:
    """fetch_titles_and_best_dates_bulk の責務を実行する。"""
    out: Dict[str, Dict[str, str]] = {}

    for i in range(0, len(video_ids), 50):
        chunk = ",".join(video_ids[i:i+50])
        data = yt_get_json(
            "videos",
            {"part": "snippet,liveStreamingDetails", "id": chunk, "key": api_key},
            timeout=10,
        )
        if not data:
            continue

        for it in data.get("items", []):
            vid = it.get("id")
            snip = it.get("snippet", {}) or {}
            live = it.get("liveStreamingDetails", {}) or {}

            title = (snip.get("title") or "").strip()
            published_at = snip.get("publishedAt")
            actual_start_time = live.get("actualStartTime")
            scheduled_start_time = live.get("scheduledStartTime")

            a_epoch, a_ymd = iso_utc_to_tz_epoch_and_yyyymmdd(actual_start_time, tz_name) if actual_start_time else (None, None)
            s_epoch, s_ymd = iso_utc_to_tz_epoch_and_yyyymmdd(scheduled_start_time, tz_name) if scheduled_start_time else (None, None)
            p_epoch, p_ymd = iso_utc_to_tz_epoch_and_yyyymmdd(published_at, tz_name) if published_at else (None, None)

            if a_epoch and a_ymd:
                epoch = a_epoch
                ymd = a_ymd
                src = "actualStartTime"
            elif s_epoch and s_ymd:
                epoch = s_epoch
                ymd = s_ymd
                src = "scheduledStartTime"
            elif p_epoch and p_ymd:
                epoch = p_epoch
                ymd = p_ymd
                src = "publishedAt"
            else:
                epoch = 0
                ymd = ""
                src = ""

            out[vid] = {
                "title": title,
                "yyyymmdd": ymd,
                "date_source": src,
                "sort_epoch": str(epoch),
            }

    return out


# ==============================
# 共有APIキー入力
# ==============================
if "shared_api_key" not in st.session_state:
    st.session_state["shared_api_key"] = GLOBAL_API_KEY or ""

st.text_input(
    "YouTube APIキー（必須）",
    key="shared_api_key",
    type="password",
    placeholder="YouTube Data API v3",
    help="設定すると全タブで共通利用します。",
)

# ==============================
# メインレイアウト
# ==============================

# ---------------- メイン ----------------
st.subheader("タイムスタンプCSVジェネレーター")

api_key_ts = resolve_api_key()
is_api_key_ready = bool(api_key_ts)
flow_steps = [
    "1) 対象動画を指定（単体 / 複数）",
    "2) タイムスタンプ入力（手動/コメント取得）",
    "3) プレビュー確認",
    "4) CSV生成・ダウンロード",
]
st.info("\n".join(flow_steps))
if not is_api_key_ready:
    st.warning("YouTube APIキーを入力すると操作できます。")

target_mode = st.radio(
    "1. 対象動画の指定方法",
    ["単体", "複数"],
    horizontal=True,
    key="ts_target_mode",
    on_change=cb_on_target_mode_change,
)
if target_mode == "複数":
    st.text_input(
        "チャンネルURLまたはチャンネルID（UC... / @handle / URL）",
        placeholder="https://www.youtube.com/@example または UCxxxxxxxxxxxxxxxxxxxxxx",
        key="ts_multi_channel_input",
    )
    st.slider(
        "取得する最新動画件数",
        min_value=3,
        max_value=50,
        value=10,
        step=1,
        key="ts_multi_latest_n",
    )
    st.toggle(
        "ショート動画のみ（61秒以下）",
        value=False,
        key="ts_multi_shorts_only",
    )
    st.button(
        "1-A. 最新動画を取得",
        key="ts_multi_fetch_latest",
        on_click=cb_fetch_latest_multi_video_candidates,
        disabled=not is_api_key_ready,
    )

if st.session_state.get("ts_multi_latest_err"):
    st.error(st.session_state["ts_multi_latest_err"])
if st.session_state.get("ts_multi_latest_msg"):
    st.success(st.session_state["ts_multi_latest_msg"])

latest_candidates = st.session_state.get("ts_multi_latest_candidates", []) or []
if target_mode == "複数" and latest_candidates:
    label_to_id: Dict[str, str] = {}
    options = []
    for c in latest_candidates:
        title = (c.get("title") or "").strip()
        ymd = c.get("yyyymmdd") or "----"
        vid = c.get("videoId") or ""
        url_ = c.get("url") or ""
        label = f"{ymd} | {title} | {url_}"
        options.append(label)
        label_to_id[label] = vid

    if target_mode == "単体":
        selected_id = (st.session_state.get("ts_single_latest_selected_id", "") or "").strip()
        default_index = 0
        if selected_id:
            for i, label in enumerate(options):
                if label_to_id.get(label) == selected_id:
                    default_index = i
                    break

        picked_label = st.selectbox(
            "1-B. 対象動画を選択",
            options,
            index=default_index,
            key="ts_single_latest_selected_label",
        )
        st.session_state["ts_single_latest_selected_id"] = label_to_id.get(picked_label, "")
    else:
        current_ids = st.session_state.get("ts_multi_latest_selected_ids", []) or []
        default_labels = [label for label in options if label_to_id.get(label) in current_ids]

        picked_labels = st.multiselect(
            "1-B. 対象動画を選択（複数可）",
            options,
            default=default_labels,
            key="ts_multi_latest_selected_labels",
        )
        st.session_state["ts_multi_latest_selected_ids"] = [label_to_id[l] for l in picked_labels if l in label_to_id]

    st.button(
        "1-C. 選択を反映",
        key="ts_apply_latest_selection",
        on_click=cb_apply_latest_selection,
        disabled=not is_api_key_ready,
    )

if target_mode == "単体":
    st.text_input(
        "動画URL（単体）",
        key="ts_url",
        placeholder="https://www.youtube.com/watch?v=...",
        disabled=not is_api_key_ready,
    )
    url = (st.session_state.get("ts_url", "") or "").strip()
else:
    multi_urls = st.session_state.get("ts_multi_urls", "") or ""
    st.text_area("選択中の動画URL（複数）", value=multi_urls, height=120, disabled=True)
    parsed_urls = parse_unique_video_urls(multi_urls)
    st.caption(f"有効URL: {len(parsed_urls)} 件（重複は自動除外）")
    url = ""

st.markdown("### 2. タイムスタンプを用意")
st.caption("手動入力かコメント自動取得のどちらかを選び、最後に入力欄の内容を確認します。")
if target_mode == "複数":
    st.caption("複数モードでは、タイムスタンプが空の動画は『ショートCSV互換（動画タイトルから1行生成）』として出力されます。")
if "ts_input_mode" not in st.session_state:
    st.session_state["ts_input_mode"] = "自動（コメントから取得）"
input_mode = st.radio(
    "入力方法",
    ["自動（コメントから取得）", "手動（貼り付け）"],
    horizontal=True,
    key="ts_input_mode",
)

manual_date_raw_ts: str = ""
manual_date_ts: str = ""
manual_date_raw_ts = st.text_input(
    "公開日を手動指定（任意・入力時は最優先）",
    placeholder="例: 2025/11/19, 11/19, 3月20日",
    key="ts_manual_date_raw",
    disabled=not is_api_key_ready,
)

if input_mode == "自動（コメントから取得）":
    st.markdown("#### コメントから候補を取り込む")
    st.markdown(
        "\n".join([
            "**操作手順（自動取得）**",
            "- 2-a. 取得条件を設定",
            "- 2-b. コメント候補を取得",
            "- 2-c. 候補を選んで入力欄に反映",
            "- 2-d. 入力欄を直接編集して微調整",
        ])
    )
    st.caption("反映後は入力欄を直接編集できます。")

    if not api_key_ts:
        st.warning("コメント自動取得にはAPIキーが必要です。")
    else:
        col_a1, col_a2 = st.columns([2, 2])
        with col_a1:
            current_order = st.session_state.get("ts_auto_order", "relevance")
            label_by_value = {v: k for k, v in COMMENT_ORDER_LABELS.items()}
            default_label = label_by_value.get(current_order, "関連度順（おすすめコメント優先）")
            selected_label = st.selectbox(
                "コメント取得順",
                list(COMMENT_ORDER_LABELS.keys()),
                index=list(COMMENT_ORDER_LABELS.keys()).index(default_label),
            )
            st.session_state["ts_auto_order"] = COMMENT_ORDER_LABELS[selected_label]
            st.caption("関連度順: 評価が高い/動画に関連が強いコメントを優先。新しい順: 直近に投稿されたコメントを優先。")
        with col_a2:
            st.text_input("検索語（任意）", value="", key="ts_auto_search_terms")

        col_a3, col_a4 = st.columns([2, 2])
        with col_a3:
            st.slider("探索ページ数", min_value=1, max_value=10, value=3, step=1, key="ts_auto_pages")
        with col_a4:
            st.checkbox("タイムスタンプ行のみ抽出", value=True, key="ts_auto_only_ts_lines")

        st.button("2-b. コメント候補を取得", key="ts_fetch_comments_common", on_click=cb_fetch_comment_candidates_by_mode, disabled=not is_api_key_ready)

        if target_mode == "単体":
            if st.session_state.get("ts_auto_err"):
                st.error(st.session_state["ts_auto_err"])
            if st.session_state.get("ts_auto_msg"):
                st.success(st.session_state["ts_auto_msg"])

            cands = st.session_state.get("ts_auto_candidates", []) or []
            if cands:
                labels = []
                shown = cands[:30]
                for i, c in enumerate(shown, start=1):
                    head = (c["text"].splitlines()[0] if c["text"] else "").strip()
                    head = head[:60] + ("…" if len(head) > 60 else "")
                    owner_tag = "本人" if c.get("is_owner") else "外部"
                    labels.append(f"[{i}] ts行={c.get('ts_lines')} / 👍{c.get('likeCount')} / {owner_tag} / {head}")

                picked = st.selectbox("2-c. 反映する候補", labels, key="ts_auto_pick")
                picked_idx = labels.index(picked)

                st.button(
                    "2-c. この候補を入力欄へ反映",
                    key="ts_auto_apply",
                    on_click=cb_apply_candidate,
                    kwargs={"index": picked_idx, "do_preview": False},
                    disabled=not is_api_key_ready,
                )

                with st.expander("選択中コメント（全文）"):
                    st.text(shown[picked_idx]["text"])
        else:
            if st.session_state.get("ts_multi_err"):
                st.error(st.session_state["ts_multi_err"])
            if st.session_state.get("ts_multi_msg"):
                st.success(st.session_state["ts_multi_msg"])

            items = st.session_state.get("ts_multi_items", {}) or {}
            ordered_ids = st.session_state.get("ts_multi_order", []) or []
            for vid in ordered_ids:
                it = items.get(vid) or {}
                vurl = it.get("url") or f"https://www.youtube.com/watch?v={vid}"
                vtitle = (it.get("title") or "").strip() or f"動画 {vid}"
                with st.expander(vtitle):
                    st.caption(vurl)
                    if it.get("error"):
                        st.warning(it["error"])
                        continue

                    cands = it.get("candidates", []) or []
                    if cands:
                        labels = []
                        for i, c in enumerate(cands[:20], start=1):
                            head = (c.get("text", "").splitlines()[0] if c.get("text") else "").strip()
                            head = head[:60] + ("…" if len(head) > 60 else "")
                            labels.append(f"[{i}] ts行={c.get('ts_lines')} / 👍{c.get('likeCount')} / {head}")
                        picked = st.selectbox(f"候補（{vtitle}）", labels, key=f"ts_multi_pick_{vid}")
                        picked_idx = labels.index(picked)
                        st.button(
                            f"この候補を採用（{vtitle}）",
                            key=f"ts_multi_apply_{vid}",
                            on_click=cb_apply_multi_candidate,
                            kwargs={"video_id": vid, "index": picked_idx},
                            disabled=not is_api_key_ready,
                        )

                    edited = st.text_area(
                        f"採用テキスト（{vtitle}）",
                        value=it.get("applied_text", ""),
                        height=140,
                        key=f"ts_multi_text_{vid}",
                        disabled=not is_api_key_ready,
                    )
                    it["applied_text"] = edited
                    items[vid] = it
            st.session_state["ts_multi_items"] = items

            summary_rows = []
            for vid in ordered_ids:
                it = items.get(vid) or {}
                applied_text = (it.get("applied_text") or "").strip()
                lines = _split_lines_for_bulk_editor(applied_text)
                for row_index, line in enumerate(lines, start=1):
                    summary_rows.append({
                        "video_id": vid,
                        "動画タイトル": (it.get("title") or "").strip() or f"動画 {vid}",
                        "URL": it.get("url") or f"https://www.youtube.com/watch?v={vid}",
                        "行番号": row_index,
                        "反映行テキスト": line,
                        "反映行数": _count_timestamp_lines(applied_text),
                    })

            if summary_rows:
                st.markdown("##### 2-e. 反映結果の確認・一括編集")
                st.caption("2-b/2-cで取り込んだ内容を一覧で確認し、そのまま編集できます。")
                st.button(
                    "2-e. コメント候補を更新（再取得）",
                    key="ts_multi_refresh_candidates",
                    on_click=cb_refresh_multi_video_candidates,
                    disabled=not is_api_key_ready,
                )
                edited_summary = st.data_editor(
                    pd.DataFrame(summary_rows),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "video_id": st.column_config.TextColumn("video_id", width="small"),
                        "動画タイトル": st.column_config.TextColumn("動画タイトル", width="large"),
                        "URL": st.column_config.LinkColumn("URL", width="large"),
                        "行番号": st.column_config.NumberColumn("行", width="small"),
                        "反映行テキスト": st.column_config.TextColumn("反映行テキスト", width="large"),
                        "反映行数": st.column_config.NumberColumn("反映行数", width="small"),
                    },
                    disabled=["video_id", "動画タイトル", "URL", "行番号", "反映行数"],
                    key="ts_multi_applied_editor",
                )

                if "video_id" in edited_summary and "反映行テキスト" in edited_summary:
                    updated_lines: Dict[str, List[Tuple[int, str]]] = {}
                    for _, row in edited_summary.iterrows():
                        vid = str(row.get("video_id") or "").strip()
                        if not vid or vid not in items:
                            continue
                        row_index = int(row.get("行番号") or 0)
                        edited_line = str(row.get("反映行テキスト") or "")
                        updated_lines.setdefault(vid, []).append((row_index, edited_line))

                    for vid in ordered_ids:
                        if vid not in items:
                            continue
                        pairs = sorted(updated_lines.get(vid, []), key=lambda x: x[0])
                        merged_text = "\n".join([line for _, line in pairs if line.strip()]).strip()
                        items[vid]["applied_text"] = merged_text
                        st.session_state[f"ts_multi_text_{vid}"] = merged_text
                    st.session_state["ts_multi_items"] = items

if target_mode == "単体":
    timestamps_input_ts = st.text_area(
        "2-d. タイムスタンプ付き楽曲リスト（最終確認・直接編集）",
        placeholder="例：\n0:35 曲名A / アーティスト名A\n6:23 曲名B - アーティスト名B\n1:10:05 曲名C by アーティスト名C",
        height=220,
        key="timestamps_input_ts",
        disabled=not is_api_key_ready,
    )
else:
    timestamps_input_ts = ""

if is_api_key_ready and manual_date_raw_ts:
    normalized = normalize_manual_date_input(manual_date_raw_ts, TZ_NAME)
    if normalized:
        manual_date_ts = normalized
        st.caption(f"解釈された公開日: {manual_date_ts}")
    else:
        st.error("日付の解釈に失敗しました。例: 2025/11/19, 11/19, 3月20日")

st.markdown("### 3. プレビュー確認")
# ここでは「CSV出力前に解析結果を確認・微調整する」ためのプレビューを作る。
st.caption("修正したい場合は『2. タイムスタンプを用意』に戻って編集し、再度プレビューを更新してください。")

col_p1, col_p2 = st.columns([1, 1])
with col_p1:
    st.toggle("左右反転", value=False, key="flip_ts")
with col_p2:
    preview_clicked = st.button("3. プレビューを更新", key="preview_ts", disabled=not is_api_key_ready)

if preview_clicked:
    # 新しいプレビューを作る前に、前回のプレビュー/CSVを初期化して状態を揃える。
    _clear_ts_preview_state(clear_csv=True)

    if target_mode == "単体":
        timestamps_text = st.session_state.get("timestamps_input_ts", "")
        flip = st.session_state.get("flip_ts", False)
        if not url or not timestamps_text:
            st.error("URLと楽曲リストを入力してください。")
        elif not is_valid_youtube_url(url):
            st.error("有効なYouTube URLを入力してください。")
        else:
            try:
                rows, preview, invalid, video_title = generate_rows(
                    url, timestamps_text, TZ_NAME, api_key_ts, manual_date_ts, flip
                )
                st.session_state["ts_preview_df"] = preview
                st.session_state["ts_row_swap_flags"] = [False] * len(preview)
                st.session_state["ts_preview_invalid"] = invalid
                st.session_state["ts_preview_title"] = video_title
                st.session_state["ts_last_rows"] = rows
                st.success(f"解析成功：{len(preview)}件（未解析 {len(invalid)}件）")
            except Exception as e:
                st.error(f"エラー: {e}")
    else:
        try:
            preview_rows, invalid_lines, warnings = build_multi_video_preview(
                items=st.session_state.get("ts_multi_items", {}) or {},
                ordered_video_ids=st.session_state.get("ts_multi_order", []) or [],
                tz_name=TZ_NAME,
                api_key=api_key_ts,
                manual_yyyymmdd=manual_date_ts,
                flip=st.session_state.get("flip_ts", False),
            )
            st.session_state["ts_preview_df"] = preview_rows
            st.session_state["ts_row_swap_flags"] = [False] * len(preview_rows)
            st.session_state["ts_preview_invalid"] = invalid_lines
            st.session_state["ts_preview_title"] = f"複数動画プレビュー（{len(preview_rows)}行）"
            st.success(f"解析成功：{len(preview_rows)}件（未解析 {len(invalid_lines)}件）")
            for w in warnings:
                st.caption(f"- {w}")
        except Exception as e:
            st.error(f"エラー: {e}")

st.markdown("### 4. CSV出力")
# ここではプレビュー済みデータを CSV に変換し、ダウンロードボタンへ受け渡す。

csv_clicked = st.button("4. CSV生成", key="csv_ts_common", disabled=not is_api_key_ready)
if csv_clicked:
    if target_mode == "単体":
        timestamps_text = st.session_state.get("timestamps_input_ts", "")
        flip = st.session_state.get("flip_ts", False)
        if not url or not timestamps_text:
            st.error("URLと楽曲リストを入力してください。")
        elif not is_valid_youtube_url(url):
            st.error("有効なYouTube URLを入力してください。")
        else:
            try:
                rows, _, invalid, video_title = generate_rows(
                    url, timestamps_text, TZ_NAME, api_key_ts, manual_date_ts, flip
                )
                swap_flags = st.session_state.get("ts_row_swap_flags", []) or []
                rows = apply_row_swap_flags_to_csv_rows(rows, swap_flags)
                download_name = f"{sanitize_download_filename(video_title)}.csv"
                save_csv_to_session(rows, download_name)
                st.session_state["ts_last_rows"] = rows
                st.success("CSVを生成しました。下のボタンからダウンロードできます。")
                if invalid:
                    st.info(f"未解析行：{len(invalid)}件。")
            except Exception as e:
                st.error(f"エラー: {e}")
    else:
        try:
            rows, warnings = build_multi_video_rows(
                items=st.session_state.get("ts_multi_items", {}) or {},
                ordered_video_ids=st.session_state.get("ts_multi_order", []) or [],
                tz_name=TZ_NAME,
                api_key=api_key_ts,
                manual_yyyymmdd=manual_date_ts,
                flip=st.session_state.get("flip_ts", False),
            )
            swap_flags = st.session_state.get("ts_row_swap_flags", []) or []
            rows = apply_row_swap_flags_to_csv_rows(rows, swap_flags)
            save_csv_to_session(rows, "timestamp_multi_videos.csv")
            st.session_state["ts_last_rows"] = rows
            st.success(f"CSVを生成しました。出力行数: {len(rows)-1}")
            for w in warnings:
                st.caption(f"- {w}")
        except Exception as e:
            st.error(f"エラー: {e}")

if st.session_state.get("ts_csv_bytes") and st.session_state.get("ts_csv_name"):
    st.download_button(
        label="CSVをダウンロード",
        data=st.session_state["ts_csv_bytes"],
        file_name=st.session_state["ts_csv_name"],
        mime="text/csv",
    )

    csv_preview_rows = build_csv_result_preview_rows(st.session_state.get("ts_last_rows", []) or [])
    if csv_preview_rows:
        st.markdown("#### 4-A. CSV出力内容の確認")
        st.caption("ショート動画の行に加えて、歌枠のタイムスタンプ付きリンクもここで確認できます。")
        st.dataframe(pd.DataFrame(csv_preview_rows), use_container_width=True, hide_index=True)

if "ts_preview_df" in st.session_state:
    st.subheader("プレビュー")
    preview_rows = st.session_state.get("ts_preview_df", []) or []
    swap_flags = st.session_state.get("ts_row_swap_flags", []) or []
    if len(swap_flags) != len(preview_rows):
        swap_flags = [False] * len(preview_rows)
        st.session_state["ts_row_swap_flags"] = swap_flags

    preview_with_ui = apply_row_swap_flags(preview_rows, swap_flags)
    preview_table_rows = []
    for idx, row in enumerate(preview_with_ui):
        preview_table_rows.append({
            "入替": bool(idx < len(swap_flags) and swap_flags[idx]),
            "artist": row.get("artist", ""),
            "song": row.get("song", ""),
            "video_id": row.get("video_id", ""),
            "video_url": row.get("video_url", ""),
            "time_seconds": row.get("time_seconds"),
            "display_name": row.get("display_name", ""),
            "date_source": row.get("date_source", ""),
            "hyperlink_formula": row.get("hyperlink_formula", ""),
        })

    edited_preview_df = st.data_editor(
        pd.DataFrame(preview_table_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "入替": st.column_config.CheckboxColumn("入替", help="ONでアーティスト名と楽曲名を入れ替え"),
            "time_seconds": st.column_config.NumberColumn("秒数", width="small"),
            "artist": st.column_config.TextColumn("アーティスト名", width="medium"),
            "song": st.column_config.TextColumn("楽曲名", width="large"),
            "display_name": st.column_config.TextColumn("リンク表示名", width="large"),
            "date_source": st.column_config.TextColumn("日付ソース", width="small"),
            "hyperlink_formula": st.column_config.TextColumn("Excel用リンク式", width="large"),
            "video_id": st.column_config.TextColumn("video_id", width="small"),
            "video_url": st.column_config.LinkColumn("video_url", width="large"),
        },
        disabled=[
            "time_seconds",
            "artist",
            "song",
            "display_name",
            "date_source",
            "hyperlink_formula",
            "video_id",
            "video_url",
        ],
        key="ts_preview_editor",
    )

    new_swap_flags = edited_preview_df["入替"].fillna(False).astype(bool).tolist() if "入替" in edited_preview_df else []
    if new_swap_flags != swap_flags:
        st.session_state["ts_row_swap_flags"] = new_swap_flags
        st.rerun()

    st.caption(f"動画タイトル：{st.session_state.get('ts_preview_title', '')}")

    invalid_lines = st.session_state.get("ts_preview_invalid", [])
    if invalid_lines:
        with st.expander("未解析行の一覧"):
            st.code("\n".join(invalid_lines))

with st.expander("👀 サンプル入力のヒント"):
    st.markdown("- URL例: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`")
    st.markdown("- 行書式: `MM:SS` または `HH:MM:SS` + 半角スペース + タイトル（区切り ` - `, ` / `, ` by ` など）")

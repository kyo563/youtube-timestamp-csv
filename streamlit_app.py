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
st.write("タイムスタンプCSV生成とショート動画CSV生成、最新動画一覧CSV生成")

DATE_TITLE_SEPARATOR = " "
TZ_NAME = "Asia/Tokyo"

YT_API_BASE = "https://www.googleapis.com/youtube/v3"

GLOBAL_API_KEY = st.secrets.get("YT_API_KEY", "")

# ==============================
# 共通ユーティリティ
# ==============================
def resolve_api_key() -> str:
    shared_key = st.session_state.get("shared_api_key", "") or ""
    if not shared_key and GLOBAL_API_KEY:
        st.session_state["shared_api_key"] = GLOBAL_API_KEY
        shared_key = GLOBAL_API_KEY
    return (shared_key or "").strip()


def yt_get_json(path: str, params: Dict, timeout: int = 10) -> Optional[dict]:
    """
    YouTube Data API v3 GET（失敗時 None）
    """
    try:
        r = requests.get(f"{YT_API_BASE}/{path.lstrip('/')}", params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def yt_get_json_verbose(path: str, params: Dict, timeout: int = 10) -> Tuple[Optional[dict], Optional[str]]:
    """
    YouTube Data API v3 GET（失敗時 (None, error_message)）
    """
    try:
        r = requests.get(f"{YT_API_BASE}/{path.lstrip('/')}", params=params, timeout=timeout)
        if r.status_code != 200:
            try:
                j = r.json()
                msg = (j.get("error", {}) or {}).get("message") or ""
            except Exception:
                msg = r.text[:300] if r.text else ""
            return None, f"{r.status_code} {msg}".strip()
        return r.json(), None
    except Exception as e:
        return None, str(e)


def to_csv(rows: List[List[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, quoting=csv.QUOTE_ALL).writerows(rows)
    return buf.getvalue()


def make_excel_hyperlink(url_: str, label: str) -> str:
    safe = (label or "").replace('"', '""')
    return f'=HYPERLINK("{url_}","{safe}")'


def is_valid_youtube_url(u: str) -> bool:
    return bool(re.match(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$", u or ""))


def normalize_text(s: str) -> str:
    s = (s or "").replace("／", "/")
    s = s.replace("　", " ").strip()
    return re.sub(r"\s+", " ", s)


def extract_video_id(u: str) -> Optional[str]:
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
    """
    ISO8601(UTC) -> (epoch_seconds, yyyymmdd in tz)
    """
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
    _, ymd = iso_utc_to_tz_epoch_and_yyyymmdd(iso_str, tz_name)
    return ymd


# ==============================
# タブ1：タイムスタンプCSVジェネレーター用関数
# ==============================
TIMESTAMP_START_RE = re.compile(r"^\s*(?:[-*•▶▷\u25CF\u25A0\u25B6\u25B7\u30FB]\s*)*(\d{1,2}:)?(\d{1,2}):(\d{2})\b")


def _strip_leading_glyphs(line: str) -> str:
    return re.sub(r"^\s*(?:[-*•▶▷\u25CF\u25A0\u25B6\u25B7\u30FB]\s*)+", "", line or "")


def parse_line(line: str, flip: bool) -> Tuple[Optional[int], Optional[str], Optional[str]]:
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
    n = 0
    for raw in (text or "").splitlines():
        s = normalize_text(raw)
        if not s:
            continue
        if TIMESTAMP_START_RE.match(s):
            n += 1
    return n


def _extract_timestamp_lines(text: str, flip: bool) -> str:
    out = []
    for raw in (text or "").splitlines():
        s = normalize_text(raw)
        if not s:
            continue
        sec, _, _ = parse_line(s, flip)
        if sec is not None:
            out.append(_strip_leading_glyphs(raw).strip())
    return "\n".join(out).strip()


@st.cache_data(show_spinner=False, ttl=300)
def fetch_timestamp_comment_candidates(
    video_id: str,
    api_key: str,
    order: str = "relevance",
    search_terms: str = "",
    max_pages: int = 3,
) -> Tuple[List[dict], Optional[str]]:
    """
    コメントからタイムスタンプ候補を収集してスコア順で返す。
    返り値: (candidates, error_message)
    """
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
    vid = extract_video_id(u)
    if not vid:
        raise ValueError("URLからビデオIDを抽出できませんでした。")
    base_watch = f"https://www.youtube.com/watch?v={vid}"

    video_title = fetch_video_title_from_oembed(base_watch)

    date_info: Dict[str, Optional[str]] = {"chosen_yyyymmdd": None, "source": None}
    if api_key:
        date_info = fetch_best_display_date_and_sources(vid, api_key, tz_name)

    date_yyyymmdd: Optional[str] = date_info.get("chosen_yyyymmdd")
    date_source: Optional[str] = date_info.get("source")

    if not date_yyyymmdd and manual_yyyymmdd and re.fullmatch(r"\d{8}", manual_yyyymmdd):
        date_yyyymmdd = manual_yyyymmdd
        date_source = "manual"

    display_name = f"{date_yyyymmdd}{DATE_TITLE_SEPARATOR}{video_title}" if date_yyyymmdd else video_title

    rows: List[List[str]] = [["アーティスト名", "楽曲名", "YouTubeリンク"]]
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
        rows.append([artist, song, hyperlink])
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


# ==============================
# tab1: コールバック（重要：widget key を安全に更新する）
# ==============================
def _get_ts_api_key() -> str:
    return resolve_api_key()


def _get_manual_yyyymmdd() -> str:
    raw = (st.session_state.get("ts_manual_date_raw", "") or "").strip()
    if not raw:
        return ""
    normalized = normalize_manual_date_input(raw, TZ_NAME)
    return normalized or ""


def _set_preview_from_text(url: str, ts_text: str) -> None:
    flip = st.session_state.get("flip_ts", False)
    api_key = _get_ts_api_key()
    manual_date = _get_manual_yyyymmdd()

    rows, preview, invalid, video_title = generate_rows(
        url, ts_text, TZ_NAME, api_key, manual_date, flip
    )
    st.session_state["ts_preview_df"] = preview
    st.session_state["ts_preview_invalid"] = invalid
    st.session_state["ts_preview_title"] = video_title
    st.session_state["ts_auto_msg"] = f"プレビュー生成：解析 {len(preview)} 件 / 未解析 {len(invalid)} 件"
    st.session_state.pop("ts_auto_err", None)


def _apply_comment_text(comment_text: str, do_preview: bool) -> None:
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
    cands = st.session_state.get("ts_auto_candidates", []) or []
    if not cands or index < 0 or index >= len(cands):
        st.session_state["ts_auto_err"] = "候補がありません（先に「コメント候補を取得」してください）。"
        return
    _apply_comment_text(cands[index]["text"], do_preview=do_preview)


# ==============================
# タブ2：Shorts → CSV 用関数
# ==============================
@st.cache_data(show_spinner=False, ttl=600)
def resolve_channel_id_from_url(url: str, api_key: str) -> Optional[str]:
    if not url or not api_key:
        return None

    try:
        pr = urllib.parse.urlparse(url)
        path = pr.path or ""

        m = re.search(r"/channel/(UC[\w-]+)", path)
        if m:
            return m.group(1)

        m = re.search(r"/@([^/?#]+)", path)
        if m:
            handle = m.group(1)
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


def list_channel_videos(channel_id: str, api_key: str, limit: int = 50) -> List[str]:
    ids: List[str] = []
    token = None
    while len(ids) < limit:
        params = {
            "part": "id",
            "type": "video",
            "channelId": channel_id,
            "maxResults": 50,
            "order": "date",
            "key": api_key,
        }
        if token:
            params["pageToken"] = token
        data = yt_get_json("search", params, timeout=10)
        if not data:
            break
        for it in data.get("items", []):
            vid = it.get("id", {}).get("videoId")
            if vid:
                ids.append(vid)
        token = data.get("nextPageToken")
        if not token:
            break
    return ids[:limit]


def iso8601_to_seconds(iso: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    h = int(m.group(1) or 0) if m else 0
    m_ = int(m.group(2) or 0) if m else 0
    s = int(m.group(3) or 0) if m else 0
    return h*3600 + m_*60 + s


def fetch_video_meta(video_ids: List[str], api_key: str):
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
    s = (s or "").replace("／", "/")
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"#\S+", " ", s)
    s = re.sub(r"[【\[][^】\]]*[】\]]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_artist_song_from_title(title: str) -> Tuple[str, str]:
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


def scrape_shorts_ids_from_web(url: str, limit: int = 50) -> List[str]:
    try:
        pr = urllib.parse.urlparse(url)
        base = f"{pr.scheme}://{pr.netloc}"
        m = re.search(r"/@[^/?#]+", pr.path)
        if m:
            target = base + m.group(0) + "/shorts"
        else:
            target = base + pr.path.rstrip("/") + "/shorts"
        html_ = requests.get(target, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).text
        vids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html_)
        seen = set()
        uniq = []
        for v in vids:
            if v not in seen:
                seen.add(v)
                uniq.append(v)
            if len(uniq) >= limit:
                break
        return uniq
    except Exception:
        return []

# ==============================
# タブ3：最新動画一覧 → CSV（改）
# ==============================
@st.cache_data(show_spinner=False, ttl=600)
def list_playlist_video_ids(playlist_id: str, api_key: str, limit: int) -> List[str]:
    ids: List[str] = []
    token = None
    seen = set()

    while len(ids) < limit:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": api_key,
        }
        if token:
            params["pageToken"] = token

        data = yt_get_json("playlistItems", params, timeout=10)
        if not data:
            break

        for it in data.get("items", []):
            vid = (it.get("contentDetails", {}) or {}).get("videoId")
            if vid and vid not in seen:
                seen.add(vid)
                ids.append(vid)
                if len(ids) >= limit:
                    break

        token = data.get("nextPageToken")
        if not token:
            break

    return ids[:limit]


@st.cache_data(show_spinner=False, ttl=600)
def list_latest_video_ids_mixed(channel_id: str, api_key: str, limit: int) -> List[str]:
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

        data = yt_get_json("search", params, timeout=10)
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

    return ids[:limit]


@st.cache_data(show_spinner=False, ttl=600)
def fetch_titles_and_best_dates_bulk(video_ids: List[str], api_key: str, tz_name: str) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}

    for i in range(0, len(video_ids), 50):
        chunk = ",".join(video_ids[i:i+50])
        data = yt_get_json(
            "videos",
            {"part": "snippet,liveStreamingDetails", "id": chunk, "key": api_key},
            timeout=10
        )
        if not data:
            continue

        for it in data.get("items", []):
            vid = it.get("id")
            snip = it.get("snippet", {}) or {}
            live = it.get("liveStreamingDetails", {}) or {}

            title = (snip.get("title") or "").strip()

            publishedAt = snip.get("publishedAt")
            actualStartTime = live.get("actualStartTime")
            scheduledStartTime = live.get("scheduledStartTime")

            a_epoch, a_ymd = iso_utc_to_tz_epoch_and_yyyymmdd(actualStartTime, tz_name) if actualStartTime else (None, None)
            s_epoch, s_ymd = iso_utc_to_tz_epoch_and_yyyymmdd(scheduledStartTime, tz_name) if scheduledStartTime else (None, None)
            p_epoch, p_ymd = iso_utc_to_tz_epoch_and_yyyymmdd(publishedAt, tz_name) if publishedAt else (None, None)

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
    "YouTube APIキー（任意）",
    key="shared_api_key",
    type="password",
    placeholder="PASS",
    help="設定すると全タブで共通利用します。",
)

# ==============================
# タブレイアウト
# ==============================
tab1, tab2, tab3 = st.tabs(["⏱ タイムスタンプCSV", "🎬 Shorts→CSV", "🆕 最新動画→CSV"])

# ---------------- タブ1 ----------------
with tab1:
    st.subheader("タイムスタンプCSVジェネレーター")
    st.write("入力→確認→出力の順で進める、4ステップ構成です。")

    api_key_ts = resolve_api_key()
    flow_steps = [
        "1) URL入力",
        "2) タイムスタンプ入力（手動/コメント取得）",
        "3) プレビュー確認",
        "4) CSV生成・ダウンロード",
    ]
    st.info("\n".join(flow_steps))

    url = st.text_input(
        "1. YouTube動画のURL",
        placeholder="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        key="ts_url",
    )

    st.markdown("### 2. タイムスタンプを用意")
    input_mode = st.radio(
        "入力方法",
        ["手動（貼り付け）", "自動（コメントから取得）"],
        horizontal=True,
        key="ts_input_mode",
    )

    manual_date_raw_ts: str = ""
    manual_date_ts: str = ""
    if not api_key_ts:
        manual_date_raw_ts = st.text_input(
            "公開日を手動指定（API未設定時のみ）",
            placeholder="例: 2025/11/19, 11/19, 3月20日",
            key="ts_manual_date_raw",
        )

    timestamps_input_ts = st.text_area(
        "タイムスタンプ付き楽曲リスト",
        placeholder="例：\n0:35 曲名A / アーティスト名A\n6:23 曲名B - アーティスト名B\n1:10:05 曲名C by アーティスト名C",
        height=220,
        key="timestamps_input_ts",
    )

    if not api_key_ts and manual_date_raw_ts:
        normalized = normalize_manual_date_input(manual_date_raw_ts, TZ_NAME)
        if normalized:
            manual_date_ts = normalized
            st.caption(f"解釈された公開日: {manual_date_ts}")
        else:
            st.error("日付の解釈に失敗しました。例: 2025/11/19, 11/19, 3月20日")

    if input_mode == "自動（コメントから取得）":
        st.markdown("#### コメントから候補を取り込む")
        st.caption("この手順で入力欄に反映されます。反映後は入力欄を直接編集して調整できます。")

        if not api_key_ts:
            st.warning("コメント自動取得にはAPIキーが必要です。")
        else:
            col_a1, col_a2 = st.columns([2, 2])
            with col_a1:
                st.selectbox("コメント取得順", ["relevance", "time"], index=0, key="ts_auto_order")
            with col_a2:
                st.text_input("検索語（任意）", value="", key="ts_auto_search_terms")

            col_a3, col_a4 = st.columns([2, 2])
            with col_a3:
                st.slider("探索ページ数", min_value=1, max_value=10, value=3, step=1, key="ts_auto_pages")
            with col_a4:
                st.checkbox("タイムスタンプ行のみ抽出", value=True, key="ts_auto_only_ts_lines")

            st.button(
                "2-A. コメント候補を取得",
                key="ts_auto_fetch",
                on_click=cb_fetch_candidates,
                kwargs={"do_autoselect_preview": False},
            )

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

                picked = st.selectbox("2-B. 反映する候補", labels, key="ts_auto_pick")
                picked_idx = labels.index(picked)

                st.button(
                    "2-C. この候補を入力欄へ反映",
                    key="ts_auto_apply",
                    on_click=cb_apply_candidate,
                    kwargs={"index": picked_idx, "do_preview": False},
                )

                with st.expander("選択中コメント（全文）"):
                    st.text(shown[picked_idx]["text"])

    st.markdown("### 3. プレビュー確認")
    st.caption("修正したい場合は『2. タイムスタンプを用意』に戻って編集し、再度プレビューを更新してください。")

    col_p1, col_p2 = st.columns([1, 1])
    with col_p1:
        st.toggle("左右反転", value=False, key="flip_ts")
    with col_p2:
        preview_clicked = st.button("3. プレビューを更新", key="preview_ts")

    if preview_clicked:
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
                st.session_state["ts_preview_invalid"] = invalid
                st.session_state["ts_preview_title"] = video_title
                st.session_state["ts_last_rows"] = rows
                st.success(f"解析成功：{len(preview)}件（未解析 {len(invalid)}件）")
            except Exception as e:
                st.error(f"エラー: {e}")

    st.markdown("### 4. CSV出力")
    st.caption("出力前に修正したい場合は、ステップ2に戻って編集 → ステップ3で再確認してください。")

    csv_clicked = st.button("4. CSVを生成してダウンロードを有効化", key="csv_ts")
    if csv_clicked:
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
                csv_content = to_csv(rows)
                download_name = re.sub(r'[\\/:*?"<>|\x00-\x1F]', "_", video_title or "").strip().strip(".") or "youtube_song_list"
                if len(download_name) > 100:
                    download_name = download_name[:100]
                download_name += ".csv"

                st.session_state["ts_csv_bytes"] = csv_content.encode("utf-8-sig")
                st.session_state["ts_csv_name"] = download_name
                st.success("CSVを生成しました。下のボタンからダウンロードできます。")
                if invalid:
                    st.info(f"未解析行：{len(invalid)}件。")
            except Exception as e:
                st.error(f"エラー: {e}")

    if st.session_state.get("ts_csv_bytes") and st.session_state.get("ts_csv_name"):
        st.download_button(
            label="CSVをダウンロード",
            data=st.session_state["ts_csv_bytes"],
            file_name=st.session_state["ts_csv_name"],
            mime="text/csv",
        )

    if "ts_preview_df" in st.session_state:
        st.subheader("プレビュー")
        df = pd.DataFrame(st.session_state["ts_preview_df"])
        st.dataframe(
            df,
            use_container_width=True,
            column_config={
                "time_seconds": st.column_config.NumberColumn("秒数", width="small"),
                "artist": st.column_config.TextColumn("アーティスト名", width="medium"),
                "song": st.column_config.TextColumn("楽曲名", width="large"),
                "display_name": st.column_config.TextColumn("リンク表示名", width="large"),
                "date_source": st.column_config.TextColumn("日付ソース", width="small"),
                "hyperlink_formula": st.column_config.TextColumn("Excel用リンク式", width="large"),
            },
        )

        st.caption(f"動画タイトル：{st.session_state.get('ts_preview_title', '')}")

        invalid_lines = st.session_state.get("ts_preview_invalid", [])
        if invalid_lines:
            with st.expander("未解析行の一覧"):
                st.code("\n".join(invalid_lines))

    with st.expander("👀 サンプル入力のヒント"):
        st.markdown("- URL例: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`")
        st.markdown("- 行書式: `MM:SS` または `HH:MM:SS` + 半角スペース + タイトル（区切り ` - `, ` / `, ` by ` など）")

# ---------------- タブ2 ----------------
with tab2:
    st.subheader("ショート → CSV")
    st.write(
        "チャンネルURLからショート動画を取得し、タイトルから **楽曲名/アーティスト名** を推定して "
        "CSV（アーティスト名, 楽曲名, ショート動画）を生成します。"
    )

    channel_url = st.text_input(
        "チャンネルのURL（/channel/UC… または /@handle）",
        placeholder="https://www.youtube.com/@Google",
        key="shorts_channel_url",
    )
    max_items = st.slider(
        "取得件数（上限）",
        min_value=5,
        max_value=200,
        value=50,
        step=5,
        key="shorts_max_items",
    )

    api_key_shorts = resolve_api_key()

    run = st.button("実行（ショート取得→推定→CSV生成）", key="shorts_run")

    if run:
        if not channel_url:
            st.error("チャンネルURLを入力してください。")
        else:
            try:
                video_ids: List[str] = []
                titles: Dict[str, str] = {}
                ymd_map: Dict[str, Optional[str]] = {}

                if api_key_shorts:
                    ch_id = resolve_channel_id_from_url(channel_url, api_key_shorts)
                    if not ch_id:
                        st.error("チャンネルIDを特定できませんでした（URLを確認するか、@handle 形式の場合はAPIキーが必要です）。")
                        st.stop()
                    st.info(f"チャンネルIDを特定しました：{ch_id}")

                    ids = list_channel_videos(ch_id, api_key_shorts, limit=max_items * 2)
                    metas = fetch_video_meta(ids, api_key_shorts)

                    shorts = [m for m in metas if m["seconds"] <= 61]
                    shorts = shorts[:max_items]
                    video_ids = [m["videoId"] for m in shorts]
                    titles = {m["videoId"]: m["title"] for m in shorts}
                    ymd_map = {m["videoId"]: m["yyyymmdd"] for m in shorts}
                else:
                    st.warning("APIキー未設定のため、Webページからの簡易抽出で試行します（公開日は取得できません）。")
                    video_ids = scrape_shorts_ids_from_web(channel_url, limit=max_items)
                    for vid in video_ids:
                        try:
                            j = requests.get(
                                "https://www.youtube.com/oembed",
                                params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                                timeout=6
                            ).json()
                            titles[vid] = (j.get("title") or "").strip()
                        except Exception:
                            titles[vid] = ""
                        ymd_map[vid] = None

                if not video_ids:
                    st.error("ショート動画が見つかりませんでした。")
                    st.stop()

                rows = [["アーティスト名", "楽曲名", "ショート動画"]]
                preview = []
                for vid in video_ids:
                    title = titles.get(vid, "") or "ショート動画"
                    artist, song = split_artist_song_from_title(title)
                    link = f"https://www.youtube.com/shorts/{vid}"

                    ymd = ymd_map.get(vid)
                    label = f"{ymd}{DATE_TITLE_SEPARATOR}{title}" if ymd else title
                    hyperlink = make_excel_hyperlink(link, label)

                    rows.append([artist, song, hyperlink])
                    preview.append({
                        "videoId": vid,
                        "yyyymmdd": ymd or "",
                        "title": title,
                        "artist": artist,
                        "song": song,
                        "shorts_url": link
                    })

                st.success(f"取得・推定完了：{len(preview)} 件")
                st.dataframe(pd.DataFrame(preview), use_container_width=True)

                csv_text = to_csv(rows)
                st.download_button(
                    label="CSVをダウンロード",
                    data=csv_text.encode("utf-8-sig"),
                    file_name="shorts_songs.csv",
                    mime="text/csv"
                )

            except Exception as e:
                st.error(f"エラー: {e}")

# ---------------- タブ3 ----------------
with tab3:
    st.subheader("投稿動画情報取得")
    st.write(
        "チャンネルの最新n件、またはプレイリスト内の動画について動画情報をCSV出力します。"
    )

    latest_mode = st.radio(
        "取得対象",
        ["チャンネルの最新動画", "プレイリスト"],
        horizontal=True,
        key="latest_mode",
    )

    if latest_mode == "チャンネルの最新動画":
        latest_channel_url = st.text_input(
            "チャンネルのURL（/channel/UC… または /@handle）",
            placeholder="https://www.youtube.com/@Google",
            key="latest_channel_url",
        )
        latest_playlist_url = ""
    else:
        latest_playlist_url = st.text_input(
            "プレイリストのURL（watch?v=…&list=… でも可）",
            placeholder="https://www.youtube.com/playlist?list=PLxxxxxxxx",
            key="latest_playlist_url",
        )
        latest_channel_url = ""

    latest_n = st.slider(
        "取得件数（n）",
        min_value=5,
        max_value=500,
        value=50,
        step=5,
        key="latest_n",
    )

    sort_choice = st.radio(
        "並び順",
        ["公開日で降順（最新が上）", "プレイリスト登録順を保持（ソートしない）"],
        index=0,
        help="プレイリスト利用時に登録順を尊重したい場合は『プレイリスト登録順』を選択してください。",
        key="latest_sort_choice",
    )

    api_key_latest = resolve_api_key()

    run_latest = st.button("実行（最新動画取得→CSV生成）", key="latest_run")

    if run_latest:
        if not api_key_latest:
            st.error("このタブはAPIキーが必須です。")
            st.stop()

        keep_playlist_order = latest_mode == "プレイリスト" and sort_choice == "プレイリスト登録順を保持（ソートしない）"

        if latest_mode == "チャンネルの最新動画":
            if not latest_channel_url:
                st.error("チャンネルURLを入力してください。")
                st.stop()

            ch_id = resolve_channel_id_from_url(latest_channel_url, api_key_latest)
            if not ch_id:
                st.error("チャンネルIDを特定できませんでした（/channel/UC… 形式か、@handle の綴りを確認してください）。")
                st.stop()

            st.info(f"チャンネルID：{ch_id}")

            video_ids = list_latest_video_ids_mixed(ch_id, api_key_latest, latest_n)
            if not video_ids:
                st.error("最新動画IDを取得できませんでした（APIキー/クォータ/権限を確認してください）。")
                st.stop()
        else:
            if not latest_playlist_url:
                st.error("プレイリストURLを入力してください。")
                st.stop()

            playlist_id = extract_playlist_id(latest_playlist_url)
            if not playlist_id:
                st.error("URLから playlistId を抽出できませんでした。list=XXXX を含むURLを指定してください。")
                st.stop()

            st.info(f"プレイリストID：{playlist_id}")

            video_ids = list_playlist_video_ids(playlist_id, api_key_latest, latest_n)
            if not video_ids:
                st.error("プレイリスト内の動画IDを取得できませんでした（APIキー/クォータ/権限、URLを確認してください）。")
                st.stop()

        details_map = fetch_titles_and_best_dates_bulk(video_ids, api_key_latest, TZ_NAME)

        records = []
        skipped = 0
        for vid in video_ids:
            d = details_map.get(vid, {})
            title = d.get("title", "") or ""
            ymd = d.get("yyyymmdd", "") or ""
            src = d.get("date_source", "") or ""
            epoch = int(d.get("sort_epoch", "0") or "0")
            if not title and not ymd and not src and not epoch:
                skipped += 1
                continue
            url2 = f"https://www.youtube.com/watch?v={vid}"
            records.append({
                "sort_epoch": epoch,
                "yyyymmdd": ymd,
                "title": title,
                "url": url2,
                "date_source": src,
            })

        if not keep_playlist_order:
            records.sort(key=lambda x: x["sort_epoch"], reverse=True)

        rows = [["動画タイトル", "動画URL", "公開日(yyyymmdd)"]]
        for r in records:
            rows.append([r["title"], r["url"], r["yyyymmdd"]])

        if skipped:
            st.warning(f"{skipped} 件の動画は詳細を取得できなかったためスキップしました（非公開/削除などの可能性があります）。")

        st.success(f"取得完了：{len(records)} 件")
        st.dataframe(pd.DataFrame(records).drop(columns=["sort_epoch"]), use_container_width=True)

        csv_text = to_csv(rows)
        st.download_button(
            label="CSVをダウンロード",
            data=csv_text.encode("utf-8-sig"),
            file_name="latest_videos.csv",
            mime="text/csv",
        )

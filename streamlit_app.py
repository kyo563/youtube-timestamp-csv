import streamlit as st
import re
import csv
import io
import requests
import urllib.parse
from datetime import datetime
from typing import Tuple, List, Optional, Dict
from zoneinfo import ZoneInfo
import unicodedata
import pandas as pd

# ==============================
# 基本設定
# ==============================
st.set_page_config(page_title="タイムスタンプCSV出力", layout="centered")

st.title("YouTube CSVツール")
st.write("タイムスタンプCSV生成とショート動画CSV生成")

# 表示名の区切り（例: 20250101 My Video Title）
DATE_TITLE_SEPARATOR = " "
# タイムゾーンは固定
TZ_NAME = "Asia/Tokyo"

# 共通APIキー（Secrets優先）
GLOBAL_API_KEY = st.secrets.get("YT_API_KEY", "")

# ==============================
# 共通ユーティリティ
# ==============================
def resolve_api_key(
    default_key: str,
    input_state_key: str,
    expander_label: str,
    input_label: str = "YT_API_KEY",
) -> str:
    """
    Secrets に設定された APIキーを優先し、無い場合だけパスワード入力欄を表示して取得します。
    """
    api_key = default_key
    if not api_key:
        with st.expander(expander_label):
            api_key = st.text_input(input_label, type="password", key=input_state_key)
    return api_key or ""

def to_csv(rows: List[List[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, quoting=csv.QUOTE_ALL).writerows(rows)
    return buf.getvalue()

def make_excel_hyperlink(url_: str, label: str) -> str:
    """Excel用 HYPERLINK 関数文字列."""
    safe = (label or "").replace('"', '""')
    return f'=HYPERLINK("{url_}","{safe}")'

def is_valid_youtube_url(u: str) -> bool:
    return bool(re.match(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$", u or ""))

def normalize_text(s: str) -> str:
    """全角→半角など軽微な正規化と空白整形です。※伸ばし棒「ー」は変換しません。"""
    s = (s or "").replace("／", "/")   # 全角スラッシュのみ半角へ
    s = s.replace("　", " ").strip()  # 全角スペース→半角
    return re.sub(r"\s+", " ", s)     # 連続空白を1つに

def extract_video_id(u: str) -> Optional[str]:
    """URLからVideo IDを抽出（watch?v= / youtu.be / shorts/ に対応）です。"""
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

def normalize_manual_date_input(raw: str, tz_name: str) -> Optional[str]:
    """
    手動入力された日付文字列を yyyymmdd に正規化して返します。
    """
    s = (raw or "").strip()
    if not s:
        return None

    # 全角→半角（数字・スラッシュなど）
    s = unicodedata.normalize("NFKC", s)

    # 日本語の年/月/日を / に統一
    s = s.replace("年", "/").replace("月", "/").replace("日", "")

    # ., - と空白を / に統一
    s = re.sub(r"[.\-]", "/", s)
    s = re.sub(r"\s+", "/", s)
    s = s.strip("/")

    # パターン1: すでに8桁数字（yyyymmdd）
    if re.fullmatch(r"\d{8}", s):
        y, m, d = int(s[0:4]), int(s[4:6]), int(s[6:8])
    else:
        parts = s.split("/")
        if len(parts) == 3:
            # 2025/3/20 など
            try:
                y, m, d = map(int, parts)
            except ValueError:
                return None
        elif len(parts) == 2:
            # 11/19, 3/20 など → 年は現在年
            today = datetime.now(ZoneInfo(tz_name)).date()
            y = today.year
            try:
                m, d = map(int, parts)
            except ValueError:
                return None
        else:
            return None

    # 2桁年が来た場合は 2000年代として扱う
    if y < 100:
        y += 2000

    try:
        dt = datetime(y, m, d)
    except ValueError:
        # 存在しない日付なら None
        return None

    return dt.strftime("%Y%m%d")

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_video_title_from_oembed(watch_url: str) -> str:
    """oEmbedで動画タイトルを取得（APIキー不要）。失敗時は既定名です。"""
    try:
        r = requests.get("https://www.youtube.com/oembed", params={"url": watch_url, "format": "json"}, timeout=6)
        if r.status_code == 200:
            title = (r.json().get("title") or "").strip()
            return title if title else "YouTube動画"
    except Exception:
        pass
    return "YouTube動画"

def iso_utc_to_tz_yyyymmdd(iso_str: str, tz_name: str) -> Optional[str]:
    """
    ISO8601(UTC, 'Z' または 'Z+小数') を tz_name へ変換し yyyymmdd を返します。
    YouTube publishedAt / actualStartTime / scheduledStartTime 共通利用。
    """
    if not iso_str:
        return None
    try:
        s = iso_str
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt_utc = datetime.fromisoformat(s)  # 小数秒付きも対応
        dt_local = dt_utc.astimezone(ZoneInfo(tz_name))
        return dt_local.strftime("%Y%m%d")
    except Exception:
        return None

# ==============================
# タブ1：タイムスタンプCSVジェネレーター用関数
# ==============================
def parse_line(line: str, flip: bool) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    先頭のタイムスタンプを読み取り、(seconds, artist, song) を返します。
    """
    m = re.match(r"^(\d{1,2}:)?(\d{1,2}):(\d{2})", line)
    if not m:
        return (None, None, None)
    time_str = m.group(0)
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 3:
        seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
    else:
        seconds = parts[0] * 60 + parts[1]
    info = line[len(time_str):].strip()

    # 区切り（ーは除外）。対象: -, —, –, ―, －, /, ／, by, BY
    msep = re.search(r"\s(-|—|–|―|－|/|／|by|BY)\s", info)
    if msep:
        left  = normalize_text(info[:msep.start()].strip())
        right = normalize_text(info[msep.end():].strip())
        if not flip:
            # デフォルト：右→左（右=アーティスト、左=曲名）
            artist, song = right or "N/A", left or "N/A"
        else:
            # 反転：左→右（左=アーティスト、右=曲名）
            artist, song = left or "N/A", right or "N/A"
        return (seconds, artist, song)

    # 区切りがない場合：全文を曲名扱い
    return (seconds, "N/A", normalize_text(info) or "N/A")

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_best_display_date_and_sources(video_id: str, api_key: str, tz_name: str) -> Dict[str, Optional[str]]:
    """
    videos?part=snippet,liveStreamingDetails を取得。
    優先順位: actualStartTime → scheduledStartTime → publishedAt。
    """
    result: Dict[str, Optional[str]] = {
        "chosen_yyyymmdd": None,
        "source": None,
    }
    if not api_key:
        return result
    try:
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {"part": "snippet,liveStreamingDetails", "id": video_id, "key": api_key}
        r = requests.get(url, params=params, timeout=6)
        if r.status_code != 200:
            return result
        items = (r.json() or {}).get("items", [])
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
    except Exception:
        return result

def generate_rows(
    u: str,
    timestamps_text: str,
    tz_name: str,
    api_key: str,
    manual_yyyymmdd: str,
    flip: bool
) -> Tuple[List[List[str]], List[dict], List[str], str]:
    """入力テキストを解析し、CSV行・プレビュー行・未解析行・動画タイトルを返します。"""
    vid = extract_video_id(u)
    if not vid:
        raise ValueError("URLからビデオIDを抽出できませんでした。")
    base_watch = f"https://www.youtube.com/watch?v={vid}"

    # タイトル（oEmbed）
    video_title = fetch_video_title_from_oembed(base_watch)

    # 日付（ライブ/プレミア優先 + ローカルTZ変換）
    date_info: Dict[str, Optional[str]] = {"chosen_yyyymmdd": None, "source": None}
    if api_key:
        date_info = fetch_best_display_date_and_sources(vid, api_key, tz_name)

    date_yyyymmdd: Optional[str] = date_info.get("chosen_yyyymmdd")
    date_source: Optional[str] = date_info.get("source")

    # APIで取得不可・未設定時は手動日付
    if not date_yyyymmdd and manual_yyyymmdd and re.fullmatch(r"\d{8}", manual_yyyymmdd):
        date_yyyymmdd = manual_yyyymmdd
        date_source = "manual"

    display_name = f"{date_yyyymmdd}{DATE_TITLE_SEPARATOR}{video_title}" if date_yyyymmdd else video_title

    # ヘッダは3列固定
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
# タブ2：Shorts → CSV 用関数
# ==============================
def extract_channel_id_from_url(url: str, api_key: str) -> Optional[str]:
    """
    /channel/UCxxxx → そのまま返す。
    /@handle や /c/xxxx → search.list(type=channel) で解決（APIキー必須）。
    """
    try:
        pr = urllib.parse.urlparse(url)
        path = pr.path or ""
        # /channel/UCxxxx
        m = re.search(r"/channel/(UC[\w-]+)", path)
        if m:
            return m.group(1)
        # /@handle
        m = re.search(r"/@([^/?#]+)", path)
        if m and api_key:
            handle = m.group(1)
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={"part": "snippet", "type": "channel", "q": handle, "maxResults": 5, "key": api_key},
                timeout=8,
            ).json()
            for it in resp.get("items", []):
                ch_id = it.get("id", {}).get("channelId")
                if ch_id:
                    return ch_id
        # /c/ や /user/ のケースも検索で対応（API前提）
        if api_key:
            candidate = [p for p in path.split("/") if p][-1]
            if candidate:
                resp = requests.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={"part": "snippet", "type": "channel", "q": candidate, "maxResults": 5, "key": api_key},
                    timeout=8,
                ).json()
                for it in resp.get("items", []):
                    ch_id = it.get("id", {}).get("channelId")
                    if ch_id:
                        return ch_id
        return None
    except Exception:
        return None

def list_channel_videos(channel_id: str, api_key: str, limit: int = 50) -> List[str]:
    """
    search.list でチャンネル内動画の videoId を新着順で取得します（APIキー必須）。
    """
    ids: List[str] = []
    token = None
    while len(ids) < limit:
        params = {
            "part": "id", "type": "video", "channelId": channel_id,
            "maxResults": 50, "order": "date", "key": api_key
        }
        if token:
            params["pageToken"] = token
        data = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=8).json()
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
    """
    videos.list で title / duration / publishedAt を取得します。
    返却: [{'videoId', 'title', 'seconds', 'yyyymmdd'}, ...]
    """
    out = []
    for i in range(0, len(video_ids), 50):
        chunk = ",".join(video_ids[i:i+50])
        data = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet,contentDetails", "id": chunk, "key": api_key},
            timeout=8,
        ).json()
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
    # 伸ばし棒「ー」は一切触らない。ハッシュタグやURLは除去。
    s = (s or "").replace("／", "/")
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"#\S+", " ", s)
    s = re.sub(r"[【\[][^】\]]*[】\]]", " ", s)  # 【】や[]のメタ表記を除去
    s = re.sub(r"\s+", " ", s).strip()
    return s

def split_artist_song_from_title(title: str) -> Tuple[str, str]:
    """
    タイトルから (artist, song) を推定して返します。
    """
    t = clean_for_parse(title)

    # 1) 引用内が曲名パターン
    q = re.search(r'[「『“"](.+?)[」』”"]', t)
    if q:
        song = q.group(1).strip()
        artist = (t[:q.start()] + t[q.end():]).strip(" -/byBY")
        artist = re.sub(r"\s+", " ", artist).strip()
        return artist if artist else "N/A", song if song else "N/A"

    # 2) 前後に空白のある明示区切り（ーは区切り扱いしない）
    m = re.search(r"\s(-|—|–|―|－|/|／|by|BY)\s", t)
    if m:
        left = t[:m.start()].strip()
        right = t[m.end():].strip()
        # 英字多い方をアーティスト（簡易ヒューリスティック）
        alpha_left = len(re.findall(r"[A-Za-z]", left))
        alpha_right = len(re.findall(r"[A-Za-z]", right))
        artist, song = (left, right) if alpha_left > alpha_right else (right, left)
        return artist or "N/A", song or "N/A"

    # 3) 空白無しの "/" 区切り（例: "曲名/アーティスト"）
    if "/" in t:
        if t.count("/") == 1 and not t.startswith("/") and not t.endswith("/"):
            left, right = [part.strip() for part in t.split("/", 1)]
            if left and right:
                alpha_left = len(re.findall(r"[A-Za-z]", left))
                alpha_right = len(re.findall(r"[A-Za-z]", right))
                artist, song = (left, right) if alpha_left > alpha_right else (right, left)
                return artist or "N/A", song or "N/A"

    # 4) 汎用フォールバック（全部曲名扱い）
    return "N/A", t or "N/A"

# --------- 非公式フォールバック（APIキー無し時の簡易抽出） ----------
def scrape_shorts_ids_from_web(url: str, limit: int = 50) -> List[str]:
    """
    /@handle/shorts などのHTMLから "videoId":"XXXX" を拾うベストエフォート。
    """
    try:
        pr = urllib.parse.urlparse(url)
        base = f"{pr.scheme}://{pr.netloc}"
        m = re.search(r"/@[^/?#]+", pr.path)
        if m:
            target = base + m.group(0) + "/shorts"
        else:
            target = base + pr.path.rstrip("/") + "/shorts"
        html = requests.get(target, timeout=8, headers={"User-Agent": "Mozilla/5.0"}).text
        vids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
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
# タブレイアウト
# ==============================
tab1, tab2 = st.tabs(["⏱ タイムスタンプCSV", "🎬 Shorts→CSV"])

# ---------------- タブ1：タイムスタンプCSVジェネレーター ----------------
with tab1:
    st.subheader("タイムスタンプCSVジェネレーター")
    st.write("YouTube動画のURLとタイムスタンプリストからCSVを生成します。")

    url = st.text_input(
        "1. YouTube動画のURL",
        placeholder="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        key="ts_url",
    )

    api_key_ts = resolve_api_key(
        default_key=GLOBAL_API_KEY,
        input_state_key="ts_api_key",
        expander_label="YouTube APIキー（任意。未設定でも手動で公開日を指定できます）",
    )

    manual_date_raw_ts: str = ""
    manual_date_ts: str = ""

    if not api_key_ts:
        manual_date_raw_ts = st.text_input(
            "公開日を手動指定（API未設定時に利用／任意）",
            placeholder="例: 2025/11/19, 11/19, 3月20日 など",
            key="ts_manual_date_raw",
        )

    timestamps_input_ts = st.text_area(
        "2. 楽曲リスト（タイムスタンプ付き）",
        placeholder="例：\n0:35 曲名A / アーティスト名A\n6:23 曲名B - アーティスト名B\n1:10:05 曲名C by アーティスト名C",
        height=220,
        key="timestamps_input_ts",
    )

    # 手動日付入力の正規化
    if not api_key_ts and manual_date_raw_ts:
        normalized = normalize_manual_date_input(manual_date_raw_ts, TZ_NAME)
        if normalized:
            manual_date_ts = normalized
            st.caption(f"解釈された公開日: {manual_date_ts}")
        else:
            manual_date_ts = ""
            st.error("日付として解釈できませんでした。例: 2025/11/19, 11/19, 3月20日 などの形式で入力してください。")

    c1, c2 = st.columns(2)
    with c1:
        st.toggle("左右反転", value=False, key="flip_ts")
        preview_clicked = st.button("🔍 プレビュー表示", key="preview_ts")
    with c2:
        csv_clicked = st.button("📥 CSVファイルを生成", key="csv_ts")

    # プレビュー生成
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
                st.success(f"解析成功：{len(preview)}件。未解析：{len(invalid)}件。下部にプレビューを表示しました。")
            except Exception as e:
                st.error(f"エラー: {e}")

    # CSV生成
    if csv_clicked:
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
                csv_content = to_csv(rows)

                # ファイル名サニタイズ（共通関数にしてもOKですがここだけなのでインライン）
                download_name = re.sub(r'[\\/:*?"<>|\x00-\x1F]', "_", video_title or "").strip().strip(".") or "youtube_song_list"
                if len(download_name) > 100:
                    download_name = download_name[:100]
                download_name += ".csv"

                st.success("CSVファイルを生成しました。下のボタンからダウンロードできます。")
                st.download_button(
                    label="CSVをダウンロード",
                    data=csv_content.encode("utf-8-sig"),
                    file_name=download_name,
                    mime="text/csv"
                )
                if invalid:
                    st.info(f"未解析行：{len(invalid)}件。入力の書式を確認してください。")
            except Exception as e:
                st.error(f"エラー: {e}")

    # プレビュー表示
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

# ---------------- タブ2：Shorts → CSV（曲名・アーティスト推定） ----------------
with tab2:
    st.subheader("ショート → CSV")
    st.write(
        "チャンネルURLからショート動画を取得し、タイトルから **楽曲名/アーティスト名** を推定して "
        "CSV（アーティスト名, 楽曲名, ショート動画）を生成します。3列目は**公開日(yyyymmdd)+元動画タイトル（リンク付き）**です。"
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

    api_key_shorts = resolve_api_key(
        default_key=GLOBAL_API_KEY,
        input_state_key="shorts_api_key",
        expander_label="YouTube APIキー（推奨。未設定時は簡易スクレイピングで試行、公開日は取得できません）",
    )

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
                    ch_id = extract_channel_id_from_url(channel_url, api_key_shorts)
                    if not ch_id:
                        st.error("チャンネルIDを特定できませんでした（URLを確認するか、@handle 形式の場合はAPIキーが必要です）。")
                        st.stop()
                    st.info(f"チャンネルIDを特定しました：{ch_id}")
                    ids = list_channel_videos(ch_id, api_key_shorts, limit=max_items * 2)
                    metas = fetch_video_meta(ids, api_key_shorts)
                    # 「60秒以下」をショートとみなす
                    shorts = [m for m in metas if m["seconds"] <= 61]
                    shorts = shorts[:max_items]
                    video_ids = [m["videoId"] for m in shorts]
                    titles = {m["videoId"]: m["title"] for m in shorts}
                    ymd_map = {m["videoId"]: m["yyyymmdd"] for m in shorts}
                else:
                    st.warning("APIキー未設定のため、Webページからの簡易抽出で試行します（公開日は取得できません）。")
                    video_ids = scrape_shorts_ids_from_web(channel_url, limit=max_items)
                    # タイトルは oEmbed で補完（公開日は取得不可）
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
                        ymd_map[vid] = None  # 日付は無し

                if not video_ids:
                    st.error("ショート動画が見つかりませんでした。URLや権限、取得件数を見直してください。")
                    st.stop()

                # 推定＆CSV作成（3列：アーティスト名, 楽曲名, ショート動画）
                rows = [["アーティスト名", "楽曲名", "ショート動画"]]
                preview = []
                for vid in video_ids:
                    title = titles.get(vid, "") or "ショート動画"
                    artist, song = split_artist_song_from_title(title)
                    link = f"https://www.youtube.com/shorts/{vid}"

                    ymd = ymd_map.get(vid)
                    if ymd:
                        label = f"{ymd}{DATE_TITLE_SEPARATOR}{title}"
                    else:
                        label = title  # 日付が無い場合はタイトルのみ

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

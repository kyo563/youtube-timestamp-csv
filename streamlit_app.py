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
st.set_page_config(page_title="タイムスタンプCSVジェネレーター", layout="centered")

st.title("タイムスタンプCSVジェネレーター")
st.write(
    "YouTube動画のURLとタイムスタンプリストからCSVを生成します。"
)

# 表示名の区切り（例: 20250101 My Video Title）
DATE_TITLE_SEPARATOR = " "
# タイムゾーンは固定
TZ_NAME = "Asia/Tokyo"

# ==============================
# 入力UI
# ==============================
url = st.text_input("1. YouTube動画のURL", placeholder="https://www.youtube.com/watch?v=xxxxxxxxxxx")

# APIキー（Secrets優先、未設定なら任意入力）
API_KEY = st.secrets.get("YT_API_KEY", "")
if not API_KEY:
    with st.expander("YouTube APIキー（任意。未設定でも手動で公開日を指定できます）"):
        API_KEY = st.text_input("YT_API_KEY", type="password")

# API未使用時の手動公開日（柔軟入力 → yyyymmdd に正規化）
manual_date_raw: str = ""
manual_date: str = ""

if not API_KEY:
    manual_date_raw = st.text_input(
        "公開日を手動指定（API未設定時に利用／任意）",
        placeholder="例: 2025/11/19, 11/19, 3月20日 など"
    )

# タイムスタンプ入力（必ず session_state と同期）
timestamps_input = st.text_area(
    "2. 楽曲リスト（タイムスタンプ付き）",
    placeholder="例：\n0:35 曲名A / アーティスト名A\n6:23 曲名B - アーティスト名B\n1:10:05 曲名C by アーティスト名C",
    height=220,
    key="timestamps_input",
)

# ==============================
# ユーティリティ：一般
# ==============================
def is_valid_youtube_url(u: str) -> bool:
    return bool(re.match(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$", u or ""))

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

def normalize_text(s: str) -> str:
    """全角→半角など軽微な正規化と空白整形です。※伸ばし棒「ー」は変換しません。"""
    s = (s or "").replace("／", "/")   # 全角スラッシュのみ半角へ
    s = s.replace("　", " ").strip()  # 全角スペース→半角
    return re.sub(r"\s+", " ", s)     # 連続空白を1つに

def normalize_manual_date_input(raw: str, tz_name: str) -> Optional[str]:
    """
    手動入力された日付文字列を yyyymmdd に正規化して返します。

    サポート例:
      - "20250101"
      - "2025/01/01", "2025-1-1", "2025.1.1"
      - "2025年1月1日"
      - "11/19", "11-19", "11 19", "11月19日"  → {今年}1119
      - "3/20", "3月20日", "０３月０５日"      → {今年}0320 / {今年}0305

    年が省略されている場合は tz_name の現在年を補完します。
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

def parse_line(line: str, flip: bool) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    先頭のタイムスタンプを読み取り、(seconds, artist, song) を返します。
    解析不可なら (None, None, None) を返します。

    仕様:
      - 引用補助・自動推定は一切なし。
      - 区切り記号（- — – ― － / ／ by BY）で左右に分割。
      - デフォルト（flip=False）は 右=アーティスト / 左=曲名。
        flip=True のとき左右反転（左=アーティスト / 右=曲名）。
      - 区切りが無い行は全文を曲名扱い（アーティスト "N/A"）
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

# ==============================
# 日付：ライブ/プレミア優先 + ローカルTZ変換（TZ_NAMEで固定）
# ==============================
def _iso_utc_to_tz_yyyymmdd(iso_str: str, tz_name: str) -> Optional[str]:
    """ISO8601(UTC, 'Z' または 'Z+小数') → tz_name へ変換し yyyymmdd を返します。"""
    if not iso_str:
        return None
    try:
        s = iso_str
        # YouTubeは "2024-01-01T00:00:00Z" or "2024-01-01T00:00:00.123Z" 形式です。
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt_utc = datetime.fromisoformat(s)  # 小数秒付きも対応
        dt_local = dt_utc.astimezone(ZoneInfo(tz_name))
        return dt_local.strftime("%Y%m%d")
    except Exception:
        return None

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_best_display_date_and_sources(video_id: str, api_key: str, tz_name: str) -> Dict[str, Optional[str]]:
    """
    videos?part=snippet,liveStreamingDetails を取得。
    優先順位: actualStartTime → scheduledStartTime → publishedAt。
    それぞれを tz_name へ変換した yyyymmdd と採用ソースを返します。
    """
    result: Dict[str, Optional[str]] = {
        "chosen_yyyymmdd": None,
        "source": None,  # "actualStartTime" | "scheduledStartTime" | "publishedAt" | "manual"
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

        publishedAt_local = _iso_utc_to_tz_yyyymmdd(publishedAt, tz_name) if publishedAt else None
        actualStartTime_local = _iso_utc_to_tz_yyyymmdd(actualStartTime, tz_name) if actualStartTime else None
        scheduledStartTime_local = _iso_utc_to_tz_yyyymmdd(scheduledStartTime, tz_name) if scheduledStartTime else None

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

# ==============================
# CSV関連ユーティリティ
# ==============================
def make_hyperlink_formula(url_: str, display_text: str) -> str:
    """Excel用 HYPERLINK 関数文字列です。"""
    safe = (display_text or "").replace('"', '""')
    return f'=HYPERLINK("{url_}","{safe}")'

def make_safe_filename(name: str, ext: str = ".csv") -> str:
    """ファイル名サニタイズ + 長さ制限です。"""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1F]', "_", name or "").strip().strip(".")
    if not name:
        name = "youtube_song_list"
    if len(name) > 100:
        name = name[:100]
    return f"{name}{ext}"

def to_csv(rows: List[List[str]]) -> str:
    out = io.StringIO()
    csv.writer(out, quoting=csv.QUOTE_ALL).writerows(rows)
    return out.getvalue()

# ==============================
# 主処理（プレビュー／CSVで共通利用）
# ==============================
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
        hyperlink = make_hyperlink_formula(jump, display_name)
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
# 手動日付入力の正規化（UI上で解釈結果を表示）
# ==============================
if not API_KEY and manual_date_raw:
    normalized = normalize_manual_date_input(manual_date_raw, TZ_NAME)
    if normalized:
        manual_date = normalized
        st.caption(f"解釈された公開日: {manual_date}")
    else:
        manual_date = ""
        st.error("日付として解釈できませんでした。例: 2025/11/19, 11/19, 3月20日 などの形式で入力してください。")

# ==============================
# ボタン群（結果は session_state に格納）
# ==============================
c1, c2 = st.columns(2)

with c1:
    # 左右反転スイッチ（デフォルトOFF）
    st.toggle("左右反転", value=False, key="flip")
    preview_clicked = st.button("🔍 プレビュー表示")

with c2:
    csv_clicked = st.button("📥 CSVファイルを生成")

# プレビュー生成
if preview_clicked:
    timestamps_text = st.session_state.get("timestamps_input", "")
    flip = st.session_state.get("flip", False)

    if not url or not timestamps_text:
        st.error("URLと楽曲リストを入力してください。")
    elif not is_valid_youtube_url(url):
        st.error("有効なYouTube URLを入力してください。")
    else:
        try:
            rows, preview, invalid, video_title = generate_rows(
                url, timestamps_text, TZ_NAME, API_KEY, manual_date, flip
            )
            st.session_state["preview_df"] = preview
            st.session_state["preview_invalid"] = invalid
            st.session_state["preview_title"] = video_title
            st.success(f"解析成功：{len(preview)}件。未解析：{len(invalid)}件。下部にプレビューを表示しました。")
        except Exception as e:
            st.error(f"エラー: {e}")

# CSV生成
if csv_clicked:
    timestamps_text = st.session_state.get("timestamps_input", "")
    flip = st.session_state.get("flip", False)
    if not url or not timestamps_text:
        st.error("URLと楽曲リストを入力してください。")
    elif not is_valid_youtube_url(url):
        st.error("有効なYouTube URLを入力してください。")
    else:
        try:
            rows, preview, invalid, video_title = generate_rows(
                url, timestamps_text, TZ_NAME, API_KEY, manual_date, flip
            )
            csv_content = to_csv(rows)
            download_name = make_safe_filename(video_title, ".csv")

            st.success("CSVファイルを生成しました。下のボタンからダウンロードできます。")
            st.download_button(
                label="CSVをダウンロード",
                data=csv_content.encode("utf-8-sig"),  # BOM付きUTF-8（Excel互換）
                file_name=download_name,
                mime="text/csv"
            )
            if invalid:
                st.info(f"未解析行：{len(invalid)}件。入力の書式を確認してください。")
        except Exception as e:
            st.error(f"エラー: {e}")

# ==============================
# プレビュー表示（カラムの外で全幅表示）
# ==============================
if "preview_df" in st.session_state:
    st.subheader("プレビュー")

    df = pd.DataFrame(st.session_state["preview_df"])

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

    st.caption(f"動画タイトル：{st.session_state.get('preview_title', '')}")

    invalid_lines = st.session_state.get("preview_invalid", [])
    if invalid_lines:
        with st.expander("未解析行の一覧"):
            st.code("\n".join(invalid_lines))

# ==============================
# ヘルプ
# ==============================
with st.expander("👀 サンプル入力のヒント"):
    st.markdown("- URL例: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`")
    st.markdown("- 行書式: `MM:SS` または `HH:MM:SS` + 半角スペース + タイトル（区切り ` - `, ` / `, ` by ` など）")

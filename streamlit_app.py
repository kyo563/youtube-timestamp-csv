import streamlit as st
import re
import csv
import io
import requests
import urllib.parse
from typing import Tuple, List, Optional

st.set_page_config(page_title="TS-DBジェネレーター", layout="centered")

st.title("楽曲データベースジェネレーター")
st.write(
    "YouTube動画のURLとタイムスタンプリストからCSVを生成します。"
    "出力は **アーティスト名 / 楽曲名 / YouTubeリンク** の3列固定で、"
    "リンク列は動画タイトル表示のハイパーリンクになります。"
    "CSVのダウンロード名も動画タイトルに自動設定します（UTF-8 BOM）。"
)

# ------------------------------
# 入力UI
# ------------------------------
url = st.text_input("1. YouTube動画のURL", placeholder="https://www.youtube.com/watch?v=xxxxxxxxxxx")
timestamps = st.text_area(
    "2. 楽曲リスト（タイムスタンプ付き）",
    placeholder="例：\n0:35 楽曲名A - アーティスト名A\n6:23 楽曲名B / アーティスト名B\n1:10:05 アーティスト名C「楽曲名C」",
    height=220,
    key="timestamps_input",
)

# ------------------------------
# ユーティリティ
# ------------------------------
def is_valid_youtube_url(u: str) -> bool:
    pattern = re.compile(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$")
    return bool(pattern.match(u or ""))

def extract_video_id(u: str) -> Optional[str]:
    """URLからVideo IDを頑健に抽出（watch?v= / youtu.be / shorts/ に対応）。"""
    if not u:
        return None
    try:
        pr = urllib.parse.urlparse(u)
        host = (pr.netloc or "").lower()
        path = pr.path or ""
        qs = urllib.parse.parse_qs(pr.query or "")

        # https://youtu.be/<id>
        if "youtu.be" in host:
            seg = path.strip("/").split("/")
            return seg[0] if seg and seg[0] else None

        # https://www.youtube.com/watch?v=<id>
        if "youtube.com" in host:
            if "v" in qs and qs["v"]:
                return qs["v"][0]
            # https://www.youtube.com/shorts/<id>
            if path.startswith("/shorts/"):
                after = path.split("/shorts/", 1)[1]
                return after.split("/")[0].split("?")[0]
        return None
    except Exception:
        return None

def normalize_text(s: str) -> str:
    """全角→半角など軽微な正規化。余計な空白を整理。"""
    s = (s or "").replace("／", "/").replace("–", "-").replace("―", "-").replace("ー", "-")
    s = s.replace("　", " ").strip()
    return re.sub(r"\s+", " ", s)

def parse_line(line: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    先頭のタイムスタンプを読み取り、(seconds, artist, song) を返します。
    解析不可なら (None, None, None) を返します。
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

    # 引用（「」/『』/“”/"）で曲名が囲まれているケース
    quote = re.search(r'[「『“"](.+?)[」』”"]', info)
    if quote:
        song = quote.group(1).strip()
        artist = (info[:quote.start()] + info[quote.end():]).strip(" -/byBy")
        artist = normalize_text(artist)
        return (seconds, artist if artist else "N/A", song if song else "N/A")

    # 区切り候補
    seps = [" - ", " — ", " / ", " by ", " BY ", "/"]
    for sep in seps:
        if sep in info:
            left, right = info.split(sep, 1)
            left, right = left.strip(), right.strip()
            # アルファベット多い方をアーティストと仮定（簡易ヒューリスティック）
            alpha_left = len(re.findall(r"[A-Za-z]", left))
            alpha_right = len(re.findall(r"[A-Za-z]", right))
            if alpha_left > alpha_right:
                artist, song = left, right
            else:
                artist, song = right, left
            return (seconds, normalize_text(artist) or "N/A", normalize_text(song) or "N/A")

    # 区切りがない場合：全文を曲名扱い
    return (seconds, "N/A", normalize_text(info) or "N/A")

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_video_title_from_oembed(watch_url: str) -> str:
    """oEmbedで動画タイトルを取得（APIキー不要）。失敗時は既定名。"""
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

def make_hyperlink_formula(url_: str, display_text: str) -> str:
    """Excel用ハイパーリンク式。ダブルクォートはエスケープ。"""
    safe_title = (display_text or "").replace('"', '""')
    return f'=HYPERLINK("{url_}","{safe_title}")'

def make_safe_filename(name: str, ext: str = ".csv") -> str:
    """ファイル名に使えない文字を置換し、長さも制限。"""
    name = re.sub(r'[\\/\:\*\?"<>\|\x00-\x1F]', "_", (nam_*]()

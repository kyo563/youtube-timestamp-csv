import streamlit as st
import re
import csv
import io
import requests
import urllib.parse
from typing import Tuple, List, Optional

st.set_page_config(page_title="タイムスタンプCSVジェネレーター", layout="centered")

st.title("🎵 タイムスタンプCSVジェネレーター")
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
    name = re.sub(r'[\\/\:\*\?"<>\|\x00-\x1F]', "_", (name or ""))
    name = name.strip().strip(".")
    if not name:
        name = "youtube_song_list"
    if len(name) > 100:
        name = name[:100]
    return f"{name}{ext}"

def to_csv(rows: List[List[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)
    writer.writerows(rows)
    return out.getvalue()

# ------------------------------
# 主処理：CSV行生成（3列固定）
# ------------------------------
def generate_rows(u: str, ts: str) -> Tuple[List[List[str]], List[dict], List[str], str]:
    vid = extract_video_id(u)
    if not vid:
        raise ValueError("URLからビデオIDを抽出できませんでした。")
    base_watch = f"https://www.youtube.com/watch?v={vid}"
    video_title = fetch_video_title_from_oembed(base_watch)

    # ヘッダは3列固定
    rows: List[List[str]] = [["アーティスト名", "楽曲名", "YouTubeリンク"]]
    parsed_preview = []
    invalid_lines = []

    for raw in (ts or "").splitlines():
        line = normalize_text(raw)
        if not line:
            continue
        sec, artist, song = parse_line(line)
        if sec is None:
            invalid_lines.append(raw)
            continue

        jump = f"{base_watch}&t={sec}s"
        hyperlink = make_hyperlink_formula(jump, video_title)
        rows.append([artist, song, hyperlink])

        parsed_preview.append({
            "time_seconds": sec,
            "artist": artist,
            "song": song,
            "hyperlink_formula": hyperlink,
        })

    if len(rows) == 1:
        raise ValueError("有効なタイムスタンプ付きの楽曲データが見つかりませんでした。")

    return rows, parsed_preview, invalid_lines, video_title

# ------------------------------
# ボタン群
# ------------------------------
c1, c2 = st.columns(2)

with c1:
    if st.button("🔍 プレビュー表示"):
        if not url or not timestamps:
            st.error("URLと楽曲リストを入力してください。")
        elif not is_valid_youtube_url(url):
            st.error("有効なYouTube URLを入力してください。")
        else:
            try:
                rows, preview, invalid, video_title = generate_rows(url, timestamps)
                st.success(f"解析成功：{len(preview)}件。未解析：{len(invalid)}件。")
                if preview:
                    import pandas as pd
                    df = pd.DataFrame(preview)
                    st.dataframe(df, use_container_width=True)
                st.caption(f"動画タイトル：{video_title}")
                if invalid:
                    with st.expander("未解析行の一覧"):
                        st.code("\n".join(invalid))
            except Exception as e:
                st.error(f"エラー: {e}")

with c2:
    if st.button("📥 CSVファイルを生成"):
        if not url or not timestamps:
            st.error("URLと楽曲リストを入力してください。")
        elif not is_valid_youtube_url(url):
            st.error("有効なYouTube URLを入力してください。")
        else:
            try:
                rows, preview, invalid, video_title = generate_rows(url, timestamps)
                csv_content = to_csv(rows)

                # ダウンロード名：動画タイトルを使用（サニタイズ済）
                download_name = make_safe_filename(video_title, ".csv")

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

# ------------------------------
# ヘルプ
# ------------------------------
with st.expander("👀 サンプル入力のヒント"):
    st.markdown("- URL例: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`")
    st.markdown("- 行書式: `MM:SS` または `HH:MM:SS` + 半角スペース + タイトル（区切り `-`, `/`, `by`, 引用「」 など）")

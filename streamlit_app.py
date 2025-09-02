import streamlit as st
import re
import csv
import io
import requests
from typing import Tuple, List, Optional

st.set_page_config(page_title="YouTubeタイムスタンプCSVジェネレーター", layout="centered")

st.title("🎵 YouTubeタイムスタンプCSVジェネレーター")
st.write("YouTube動画のURLとタイムスタンプリストからCSVを生成します。Excel向けにUTF-8 BOM付きで出力します。3列固定：アーティスト名 / 楽曲名 / YouTubeリンク（動画タイトルのハイパーリンク）です。")

url = st.text_input("1. YouTube動画のURL", placeholder="https://www.youtube.com/watch?v=xxxxxxxxxxx")
timestamps = st.text_area(
    "2. 楽曲リスト（タイムスタンプ付き）",
    placeholder="例：\n0:35 楽曲名A - アーティスト名A\n6:23 楽曲名B / アーティスト名B\n1:10:05 アーティスト名C「楽曲名C」",
    height=220
)

def is_valid_youtube_url(u: str) -> bool:
    pattern = re.compile(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$")
    return bool(pattern.match(u))

def extract_video_id(u: str) -> Optional[str]:
    # 通常/短縮/Shorts いずれにも対応
    m = re.search(r"(?:v=)([\w-]+)|(?:youtu\.be\/)([\w-]+)|(?:shorts\/)([\w-]+)", u)
    if not m:
        return None
    return m.group(1) or m.group(2) or m.group(3)

def normalize_text(s: str) -> str:
    # 軽い正規化（全角→半角など）
    s = s.replace("／", "/").replace("–", "-").replace("―", "-").replace("ー", "-")
    s = s.replace("　", " ").strip()
    return re.sub(r"\s+", " ", s)

def parse_line(line: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    1行を解析して (seconds, artist, song) を返します。
    解析できない場合は (None, None, None) を返します。
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
def fetch_video_title(base_watch_url: str) -> str:
    """
    YouTube oEmbedから動画タイトルを取得します（APIキー不要）。
    失敗時はプレースホルダを返します。
    """
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": base_watch_url, "format": "json"},
            timeout=6
        )
        if r.status_code == 200:
            data = r.json()
            title = str(data.get("title", "")).strip()
            return title if title else "YouTube動画"
        return "YouTube動画"
    except Exception:
        return "YouTube動画"

def make_hyperlink_formula(url_: str, display_text: str) -> str:
    """
    Excelでクリック可能な HYPERLINK 関数を返します。
    CSVでは =HYPERLINK("URL","表示名") の形で出力します。
    タイトル中のダブルクォートは "" にエスケープします。
    """
    safe_title = display_text.replace('"', '""')
    return f'=HYPERLINK("{url_}","{safe_title}")'

def generate_rows(u: str, ts: str):
    vid = extract_video_id(u)
    if not vid:
        raise ValueError("URLからビデオIDを抽出できませんでした。")
    base_watch = f"https://www.youtube.com/watch?v={vid}"

    video_title = fetch_video_title(base_watch)

    # ★ 3列固定のヘッダに変更（YouTubeリンク）
    rows: List[List[str]] = [["アーティスト名", "楽曲名", "YouTubeリンク"]]
    parsed_preview = []
    invalid_lines = []

    for raw in ts.splitlines():
        line = normalize_text(raw)
        if not line:
            continue
        sec, artist, song = parse_line(line)
        if sec is None:
            invalid_lines.append(raw)
            continue

        jump = f"{base_watch}&t={sec}s"  # 時間つきリンクを維持
        hyperlink = make_hyperlink_formula(jump, video_title)

        rows.append([artist, song, hyperlink])
        parsed_preview.append({
            "time_seconds": sec,
            "artist": artist,
            "song": song,
            "hyperlink_formula": hyperlink
        })

    if len(rows) == 1:
        raise ValueError("有効なタイムスタンプ付きの楽曲データが見つかりませんでした。")
    return rows, parsed_preview, invalid_lines

def to_csv(rows: List[List[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)
    writer.writerows(rows)
    return out.getvalue()

c1, c2 = st.columns(2)

with c1:
    if st.button("🔍 プレビュー表示"):
        if not url or not timestamps:
            st.error("URLと楽曲リストを入力してください。")
        elif not is_valid_youtube_url(url):
            st.error("有効なYouTube URLを入力してください。")
        else:
            try:
                rows, preview, invalid = generate_rows(url, timestamps)
                st.success(f"解析成功：{len(preview)}件。未解析：{len(invalid)}件。")
                if preview:
                    import pandas as pd
                    df = pd.DataFrame(preview)
                    st.dataframe(df, use_container_width=True)
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
                rows, preview, invalid = generate_rows(url, timestamps)
                csv_content = to_csv(rows)
                st.success("CSVファイルを生成しました。下のボタンからダウンロードできます。")
                st.download_button(
                    label="CSVをダウンロード",
                    data=csv_content.encode("utf-8-sig"),
                    file_name="youtube_song_list.csv",
                    mime="text/csv"
                )
                if invalid:
                    st.info(f"未解析行：{len(invalid)}件。入力の書式を確認してください。")
            except Exception as e:
                st.error(f"エラー: {e}")

with st.expander("👀 サンプル入力のヒント"):
    st.markdown("- URL例: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`")
    st.markdown("- 行書式: `MM:SS` または `HH:MM:SS` + 半角スペース + タイトル（区切り `-`, `/`, `by`, 引用「」 など）")

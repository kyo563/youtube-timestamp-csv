# streamlit_app.py
import streamlit as st
import re
import csv
import io
from typing import Tuple, List

st.set_page_config(page_title="YouTubeタイムスタンプCSVジェネレーター", layout="centered")

st.title("🎵 YouTubeタイムスタンプCSVジェネレーター")
st.write("YouTube動画のURLとタイムスタンプリストからCSVを生成します。Excel向けにUTF-8 BOM付きで出力します。")

url = st.text_input("1. YouTube動画のURL", placeholder="https://www.youtube.com/watch?v=xxxxxxxxxxx")
timestamps = st.text_area(
    "2. 楽曲リスト（タイムスタンプ付き）",
    placeholder="例：\n0:35 楽曲名A - アーティスト名A\n6:23 楽曲名B / アーティスト名B\n1:10:05 アーティスト名C「楽曲名C」",
    height=220
)

def is_valid_youtube_url(url: str) -> bool:
    pattern = re.compile(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$")
    return bool(pattern.match(url))

def extract_video_id(url: str) -> str:
    # 通常/短縮/Shorts いずれにも対応
    match = re.search(r"(?:v=)([\w-]+)|(?:youtu\.be\/)([\w-]+)|(?:shorts\/)([\w-]+)", url)
    if not match:
        return None
    return match.group(1) or match.group(2) or match.group(3)

def normalize_text(s: str) -> str:
    # 全角スラッシュなどを半角へ、余計な全角スペースも除去
    s = s.replace("／", "/").replace("–", "-").replace("ー", "-").replace("―", "-")
    s = s.replace("　", " ").strip()
    return re.sub(r"\s+", " ", s)

def parse_line(line: str) -> Tuple[int, str, str]:
    # 1行を解析して (seconds, artist, song) を返す
    # 解析できない場合は (None, None, None)
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

    # 「」/『』/“”/"" などで曲名が囲まれているケース
    quote = re.search(r"[「『“\"](.+?)[」『”\"]", info)
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
            # ヒューリスティック：アルファベット多い方をアーティスト
            alpha_left = len(re.findall(r"[A-Za-z]", left))
            alpha_right = len(re.findall(r"[A-Za-z]", right))
            if alpha_left > alpha_right:
                artist, song = left, right
            else:
                artist, song = right, left
            return (seconds, normalize_text(artist) or "N/A", normalize_text(song) or "N/A")

    # 区切りがない場合：全文を曲名扱い
    return (seconds, "N/A", normalize_text(info) or "N/A")

def generate_rows(url: str, timestamps: str):
    vid = extract_video_id(url)
    if not vid:
        raise ValueError("URLからビデオIDを抽出できませんでした。")
    base = f"https://www.youtube.com/watch?v={vid}"

    rows = [["アーティスト名", "楽曲名", "動画リンク"]]
    parsed_preview = []
    invalid_lines = []

    for raw in timestamps.splitlines():
        line = normalize_text(raw)
        if not line:
            continue
        sec, artist, song = parse_line(line)
        if sec is None:
            invalid_lines.append(raw)
            continue
        link = f"{base}&t={sec}s"
        rows.append([artist, song, link])
        parsed_preview.append({"time_seconds": sec, "artist": artist, "song": song, "link": link})

    if len(rows) == 1:
        raise ValueError("有効なタイムスタンプ付きの楽曲データが見つかりませんでした。")

    return rows, parsed_preview, invalid_lines

def to_csv(rows):
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

with st.expander("👀 サンプル入力を挿入"):
    st.markdown("- URL例: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`")
    st.markdown("- 行書式: `MM:SS` または `HH:MM:SS` + 半角スペース + タイトル（区切り `-`, `/`, `by`, 引用「」 など）")

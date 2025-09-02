# YouTubeタイムスタンプCSVジェネレーター

YouTube動画URLとタイムスタンプ付き楽曲リストから、**Excel対応（UTF-8 BOM）** のCSVを生成するStreamlitアプリです。

## 特徴
- 通常/短縮/Shorts いずれのYouTube URLにも対応
- 引用「」/『』/“”/`"`、区切り `-` `/` `by` などを自動判別
- 全角スラッシュや全角スペースを正規化
- 解析プレビュー + CSVダウンロード
- ダウンロードCSVはExcelで文字化けしない `UTF-8 with BOM`

## 1. ローカル実行
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

ブラウザが自動で開かない場合は、表示されたURLをコピーしてアクセスしてください。

## 2. GitHub新規リポジトリの作成手順（Web UI）
1. GitHubにログイン → 右上 **+** → **New repository**。
2. Repository name に例：`youtube-timestamp-csv`。
3. Public/Private を選択し、**Create repository**。
4. ローカルで作ったフォルダを初期化してプッシュ：
   ```bash
   cd youtube-timestamp-csv
   git init
   git add .
   git commit -m "Initial commit: Streamlit app"
   git branch -M main
   git remote add origin https://github.com/<YOUR_NAME>/youtube-timestamp-csv.git
   git push -u origin main
   ```

## 3. GitHub新規リポジトリの作成手順（GH CLI）
CLIを使う場合は [GitHub CLI](https://cli.github.com/) をインストールし、ログイン後に：
```bash
gh repo create youtube-timestamp-csv --public --source=. --remote=origin --push
```

## 4. Streamlit Community Cloudにデプロイ
1. https://share.streamlit.io/ にアクセスし、GitHub連携。
2. **New app** → リポジトリを選択 → Branch: `main`, File path: `streamlit_app.py` を指定。
3. **Deploy**。数分後にURLが発行されます。

## 入力例
URL：`https://www.youtube.com/watch?v=dQw4w9WgXcQ`
```
0:35 楽曲名A - アーティスト名A
6:23 楽曲名B / アーティスト名B
1:10:05 アーティスト名C「楽曲名C」
```

## ライセンス
MIT License

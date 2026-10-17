# 醫技國考｜175 個知識模塊

免費、離線優先的臺灣醫事檢驗師國考題庫。收錄近五年六科共 4,800 題，依 175 個知識模塊整理，提供作答歷史、錯題追蹤、個人筆記、分類知識筆記及逐題背景補充。

## 下載與安裝

請從 GitHub 頁面的 **Releases** 下載符合電腦的版本：

- `macos-arm64`：Apple Silicon Mac（M1、M2、M3、M4…）
- `macos-intel`：Intel Mac
- `windows-x64`：64 位元 Windows

解壓縮後執行「醫技國考」。程式會開啟本機網站，第一次使用時請建立至少 8 個字元的登入密碼。不需要安裝 Python，也不需要網路才能作答。

### 未簽章提示

本專案不收費，也未購買 Apple 或 Microsoft 的商業程式簽章。請只從本 repository 的 Releases 下載，並可使用 `SHA256SUMS.txt` 核對檔案。

- macOS：第一次開啟時，在 Finder 對 App 按右鍵並選擇「打開」。
- Windows：若 SmartScreen 顯示未辨識的應用程式，請先核對下載來源及 SHA-256，再自行決定是否執行。

## 資料與隱私

作答紀錄、錯題、密碼、個人筆記及筆記圖片只保存在使用者自己的電腦：

- macOS：`~/Library/Application Support/醫技國考複習系統/`
- Windows：`%LOCALAPPDATA%\醫技國考複習系統\`

程式沒有帳號系統、遙測、廣告或第三方雲端同步。請在「資料與設定」定期下載完整 ZIP 備份。

## 手機與 Tailscale

預設只有安裝電腦能存取。若要從手機使用：

1. 在電腦版「資料與設定」啟用「允許同 Wi-Fi／Tailscale 裝置連線」。
2. 關閉並重新啟動程式。
3. 手機與電腦連上同一 Wi-Fi，或登入同一個 Tailscale 私人網路。
4. 使用啟動視窗顯示的私人網址登入。

主機電腦必須保持開機，手機沒有離線副本。

## 更新

從 Releases 下載新版並取代舊應用程式即可。使用者資料存放在獨立資料夾，新版啟動前會建立備份，再更新共用題庫內容，不會覆蓋個人作答與筆記。

## 開發與驗證

需要 Python 3.12、Node.js 及 Poppler：

```bash
python -m pip install -r requirements-build.txt
python tools/build_seed.py
python tools/check_public_release.py
PYTHONPATH=app python -m unittest discover -s app/tests -q
node --check app/static/app.js
pyinstaller --noconfirm medtech-study.spec
```

對 `v*` 標籤推送後，GitHub Actions 會建立 macOS Apple Silicon、macOS Intel 與 Windows x64 的 Release ZIP。

## 授權與資料來源

- 程式碼：MIT License
- 自行整理的知識內容：CC BY-NC 4.0
- 官方試題與答案：請參閱 [NOTICE.md](NOTICE.md)

本工具為考試複習用途，並非醫療判斷或臨床決策工具。

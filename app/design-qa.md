# Design QA — 題卡知識筆記按鈕

- Source visual truth: `/tmp/codex-remote-attachments/01a12ed3-3699-7732-821f-085fe25b7e04/D2090366-E9F9-43A3-8B38-B2C6D98117AE/1-相片-1.jpg`
- Implementation: `http://localhost:8766/`，Codex in-app Browser tab 8
- Implementation screenshot: CUA browser capture from tab 8（此瀏覽器介面回傳 19,486-byte JPEG，但不提供可保留的檔案路徑）
- Source pixels: 590 × 1280；implementation capture: 319 × 467
- CSS viewport: 319 px wide；density normalization: 以內容寬度縮放，比較題卡工具列下方的分類列、按鈕及題目標題，不比較來源截圖中的 iOS 狀態列
- State: 題卡已載入、答案尚未送出；該分類已有知識筆記

**Full-view comparison evidence**

- 來源以藍框指定分類列右側空間；實作把「知識筆記」放在相同區域，沒有改變返回、題數、模式、題目、原卷及底部送出按鈕的層次。
- 319 px 寬度下沒有水平溢位，分類文字可換行，按鈕保持完整且沒有遮住標題。

**Focused region comparison evidence**

- 題卡頂部區域已在 in-app Browser 實際截圖檢查。按鈕使用既有紙本編輯風的細框、暖白底、墨藍文字，尺寸及右對齊位置符合藍框標示。
- 字體沿用現有系統字與襯線標題；分類文字的大小、粗細、行高沒有改動。

**Findings**

- 沒有 P0、P1 或 P2 視覺差異。
- P3：來源截圖的實際題目與測試題目不同，因此長分類名稱的換行位置不完全相同；按鈕所在網格及響應式行為一致，屬可接受的內容差異。

**Required fidelity surfaces**

- Fonts and typography: 通過；沿用既有字型、重量與行高。
- Spacing and layout rhythm: 通過；分類列使用 `minmax(0, 1fr) auto`，按鈕與文字間距 12 px。
- Colors and visual tokens: 通過；沿用既有 `secondary` 按鈕與墨藍色票。
- Image quality and assets: 不適用；本次新增內容沒有圖片、圖示或裝飾素材。
- Copy and content: 通過；按鈕文字為「知識筆記」，返回文字依入口顯示「返回題目」或「返回分類」。

**Primary interactions tested**

1. 題卡只在所屬分類有筆記時顯示按鈕。
2. 點擊按鈕可開啟正確分類筆記。
3. 返回題目後保留已選答案及尚未儲存的個人筆記。
4. 從筆記點擊代表題、返回筆記、再返回原題後，仍恢復原本題號與狀態。
5. 鍵盤焦點回到「知識筆記」按鈕；瀏覽器 console 無錯誤。

**Comparison history**

- Initial implementation: 沒有發現 P0/P1/P2 問題，因此未進行視覺修正循環。

**Implementation checklist**

- [x] 後端回傳該題分類是否有筆記
- [x] 手機題卡分類列右側顯示按鈕
- [x] 開啟、關閉與焦點流程
- [x] 原題狀態保存與筆記內題號往返
- [x] 319 px 寬度與 console 檢查

final result: passed

# Codex Sidebar Cleaner

修復 Codex 側邊欄的殘留索引與舊專案參照, 並提供本機 / ChatGPT 雲端封存對話的處理入口

## 開始使用

需要 Python 3.10+, 不需要安裝 pip 套件; 本機封存刪除與對話整理另需相容的 Codex CLI

- Windows: 從檔案總管雙擊 `start.cmd`
- macOS: 在終端機執行 `sh start.command`
- 預設繁體中文; 主選單按 `L` 切換語言, 也可用 `start.cmd en` / `sh start.command en` 以英文啟動

請從 Codex 外部啟動; 需要清理快取時, 依提示完全退出與重開 App, 並保留 cleaner 視窗; macOS 請結束 App, 不只關閉視窗

## 選單

| 主選單 | 用途 |
| --- | --- |
| 1. 修復側邊欄 | 檢查與掃描, 核對舊參照, 等待 App 重新同步, 再清理有刪除證據的索引 |
| 2. 清理封存對話 | 選擇本機 Codex 或 ChatGPT 雲端流程 |
| 3. 進階工具 | 已核對的修正清單, 舊專案參照, 封存清單, 本機對話整理與單獨索引清理 |
| 0. 離開 | 結束工具 |

進階選單按 `0` 返回主選單:

| 進階工具 | 用途 |
| --- | --- |
| 1. 執行已核對的修正清單 | 選擇計畫 JSON; 當地有 `logs/pending-sidebar-reference-plan.json` 時可按 Enter 使用 |
| 2. 移除舊專案參照 | 核對並移除指定的本機參照, 保留雲端專案與對話 |
| 3. 建立封存清單 | Windows 使用截圖, macOS 使用已核對的標題 JSON; 只建立請求, 不直接封存 |
| 4. 整理本機對話 | 把未分類且未封存的本機對話移到獨立區段, 保留其他分類與內容 |
| 5. 只清除已刪除的索引 | 清理已有 `conversation_deleted` 證據的本機 ChatGPT 索引 |

## 封存對話

**本機**: 輸入專案資料夾後預覽封存對話與相依項目; 輸入 `DELETE` 才備份並永久刪除; 活動中的相依對話會阻擋刪除; 內容檔案已遺失時可清除殘留資料, 但遺失內容無法備份

**雲端**: 開啟 ChatGPT 封存管理, 使用相同帳號在網頁逐筆核對與確認刪除; 網頁已刪除但 Codex 仍顯示時, 選雲端流程第 2 項, 核對精確 ID 後輸入 `CLOUD DELETED` 清理本機索引; 工具不自動刪除雲端對話, 雲端刪除結果由使用者確認

## 日誌與限制

- `logs/` 保存計畫與逐筆結果, `backups/` 保存修改前的資料; 不要公開上傳, 可能包含私人對話資訊
- 修改前會核對帳戶, 精確 ID, 來源及相依項目; 狀態改變或資料格式不符就停止
- 無法載入, 404, 同名, OCR 或清單缺席都不是刪除證據; 完成 App 同步也不代表逐筆驗證了全部雲端歷史
- 本機對話永久刪除無法在 App 還原; 中途失敗時, 先前成功的刪除仍保留, 請先核對日誌再重試
- Windows 啟動器已操作驗證; macOS 啟動器僅做 shell 語法與隔離路由檢查, 正式資料操作仍須依當地 Codex 版本確認

結束代碼 `0` 表示操作結束 (含取消), `2` 表示仍有項目待處理; 其他代碼請查看畫面與日誌

## 命令列

```powershell
python clean_codex_catalog.py --verify
python maintain_sidebar.py --help
python delete_archived_threads.py --help
```

預設資料目錄為 `CODEX_HOME` 或 `~/.codex`; 可用 `--codex-home` 與 `--output-dir` 指定其他位置; [進階用法與 JSON 格式](docs/advanced.md) 集中說明手動計畫, 雲端快照與其他 CLI

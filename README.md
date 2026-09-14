# Codex 側欄修復工具（Windows）

## 說明
這是我讓 codex 自己做出清理自己用的工具，將整個資料夾放在可寫入的位置，再雙擊 `start.cmd`。需要已安裝的 Python 3.10 或以上版本；不需要 pip 套件。

## 選單

每個保留的選單功能都先執行原本 Check / verify 的完整性、已知刪除 ID、索引數量及同步狀態檢查；檢查失敗就停止。原本的檢查、要求核對與掃描不再各佔一個選單項目，底層命令仍供整合流程及診斷使用。

1. **Clean confirmed deleted entries**：先檢查，再掃描日誌並清除有 `conversation_deleted` 證據的本機 ChatGPT 索引，包含專案內項目。
2. **Verify, scan, reconcile with cloud, then clean**：檢查 → 全日期掃描（含專案）→ 要求 App 核對 → 等待 App 核對完成 → 清理已確認刪除的索引。
3. **Organize local tasks**：先檢查，再把符合條件的 local tasks 移到「本機 Codex」section，與雲端核對清理分開執行。
4. **Exit**：離開。

整理會把未封存、未指派專案、未放入其他 section 的本機 Codex 任務移到「本機 Codex」。不存在時建立，同名 section 存在時重用；同名多個則停止。保留內容、釘選、專案、其他 section 及封存狀態。

區段整理需安裝相容的 Codex CLI（可在命令列執行 `codex`），使用其 app-server 區段介面，不直接修改資料庫。此功能不用退出桌面 App；但獨立程式的變更可能需要重開桌面才顯示。若版本不支援、資料結構不同或實際資料目錄不符，會停止並記錄錯誤。

```powershell
python organize_local_threads.py
python organize_local_threads.py --apply
python organize_local_threads.py --apply --section-name "我的本機任務"
```

第一行只預覽。可用 `--codex` 指定 Codex 執行檔，`--codex-home` 指定資料目錄。記錄存於 `logs/local-organize-時間/`，含計畫、原區段快照、每筆搬移與驗證結果。搬移採逐筆提交；部分失敗時已完成的分類保留，重新執行可接續。程式不會把已封存的任務還原。此 Python 模組使用跨平台標準庫，但僅在目前 Windows 實測，macOS/Linux 尚未實機驗證。

> 清理／重設時請完全退出 Codex/ChatGPT，但保持這個命令視窗開啟。不要在 Codex 內建終端啟動後再退出 App；先前背景程序曾停在等待狀態。出現 `completed` 表示已知刪除項目的資料庫清理完成；`awaiting_app_reconciliation` 只表示同步重設已提交，仍需重開 App 等它完成核對。

## 紀錄與備份

- `logs/catalog-run-時間/cleanup.jsonl`：UTF-8 JSON Lines，逐筆即時落盤，包含證據來源、受影響 ID／標題、交易結果、錯誤及驗證結果。
- `backups/catalog-run-時間.sqlite`：修改前的 SQLite 一致備份，包含 WAL 中已提交的資料。
- 不要公開上傳備份或完整 log，其中可能包含私人側欄資訊。
- 保留 `logs` 可在 App 原始日誌輪替後繼續辨識先前確認刪除的 ID。移到其他電腦時可以只複製程式與啟動器，當地會重新掃描日誌；若需保留已確認的歷史證據，再一起帶上自己的 `logs`。不要匯入不可信的紀錄。

## 命令列

```powershell
python clean_codex_catalog.py --verify
python clean_codex_catalog.py --apply
python clean_codex_catalog.py --apply --reconcile
python clean_codex_catalog.py --scan-projects
```

離線掃描沒有可調整的日期範圍，會掃描所有現存索引。此行為不會修改 App 雲端核對的約 30 天限制，不會下載未快取的歷史，也不會驗證每一筆雲端對話是否仍存在。自訂 `--database` 時，請同時指定與該資料庫對應的 `--codex-home`，以便讀取正確的專案指派設定。

## 依其他裝置最近清單對齊

支援 `--align-recents plan.json`（唯讀預覽），加 `--apply` 才寫入。這是依使用者確認的完整清單移除本機非專案 ChatGPT 快取，並不代表雲端對話已刪除。計畫必須包含 schema_version=1、scope="visible_nonproject_chatgpt"、confirmed_complete_reference=true、正確 host_id，以及非空且不重疊的 keep_ids/remove_ids。

計畫只適用當次確認的帳戶與清單快照；它不是永久白名單。新增對話或移入專案後，舊計畫會停止，必須重新比對。專案內對話與本機 Codex 任務不納入此模式。可攜壓縮檔不包含你的個人計畫。

預設資料庫：`$CODEX_HOME/sqlite/codex-dev.db`，未設定 CODEX_HOME 時使用目前使用者的 `~/.codex`。可用 `--codex-home` 或 `--database` 指定其他位置；用 `--log-root` 指定桌面日誌根目錄（可重複）；用 `--output-dir` 指定 logs/backups 的父目錄。

```powershell
python clean_codex_catalog.py --verify --database "D:\CodexData\sqlite\codex-dev.db" --log-root "D:\CodexLogs" --output-dir "D:\CleanerResults"
```

## 雲端快照核對

`python compare_cloud_catalog.py --cloud-snapshot cloud-snapshot.json` 可依 ID 比對獨立取得的雲端快照與本機全部 ChatGPT 索引，涵蓋專案歸屬、標題差異、本機獨有與雲端獨有項目；報告保存在 `logs/cloud-compare-時間/`。此功能不會登入、下載雲端清單或修改資料。必須先從相同帳戶取得清單，不能把本機清單當成雲端證據。

快照格式為 JSON 物件：`schema_version: 1`、`source`、ISO 日期 `captured_at`、`account_label`、`coverage` 與 `conversations`。coverage 必須分別以布林值標示 `recents`、`projects`、`archived`、`cloud_work` 是否完整；conversations 每筆包含 UUID 格式 `id`、`title` 與明確的 `project_id`（無專案為 null）。未完整讀取的範圍必須填 false；完整性聲明不等同工具已驗證分頁。

此核對模組使用標準 Python 與唯讀 SQLite，可供其他系統指定相容資料庫使用；尚未在 macOS/Linux 實機測試。Windows 清理功能仍受 Windows 限制。核對報告不會將「雲端未列出」自動判定為已刪除，也不會自動建立刪除計畫。

封存項目可加 `archived: true`，它們不需要出現在最近清單，未快取的封存項目會另外列出。若擷取的文字混有內容預覽，必須加 `title_verified: false`，此時只比對 ID 與專案歸屬，不能宣稱標題已核對。`visible_cloud_aligned_to_snapshot` 只表示可見雲端 ID 與歸屬對上本次快照，不表示內容可載入、未來保持同步或本機 Codex 任務應與手機相同。

## 安全範圍與限制

工具不呼叫雲端刪除 API，不修改登入資料、本機 Codex 對話內容、專案設定或工作檔案。修改前會檢查資料表欄位、備份、檢查完整性；交易失敗會回滾，其他索引及本機同步狀態會比對是否保持不變。資料庫結構不相容或帳戶來源不明確時停止。

「驗證通過」不是已逐筆證明全部雲端對話有效。未曾留下刪除錯誤、超過 App 核對範圍、或位於其他專案清單快取的殘留，仍可能需要額外診斷。不要只因對話從一般清單消失就判斷已刪除：它可能已移入專案。

此版已在目前 Windows 的資料庫上做唯讀驗證，並以測試資料庫驗證專案內外處理、交易回滾及備份。不能承諾未來 App 更新後永遠相容。


## 雲端核對與清理流程

從外部命令視窗執行 `start.cmd`，選 **2. Verify, scan, reconcile with cloud, then clean**：

1. 自動執行原本的檢查，再掃描所有日期、包含專案的本機索引，輸出 JSON／CSV。
2. 依提示完全退出 Codex/ChatGPT，保持命令視窗開啟。程式備份並重設核對進度。
3. 依提示重新開啟 Codex。程式等到唯一 ChatGPT host 的同步狀態顯示初始建置完成、已有核對時間且無待續掃描 checkpoint，才繼續。預設最多等 30 分鐘；逾時不執行清理。
4. 依提示再次完全退出 Codex/ChatGPT。程式重新讀取日誌證據，備份並清除已確認刪除的本機索引。

清理完成後可再開啟 Codex。若需要整理 local tasks，另選選單第 3 項；雲端清理流程不會搬動 local tasks。

任一步失敗即停止後續步驟，先前成功的步驟會保留。整體紀錄位於 `logs/sidebar-workflow-時間/cleanup.jsonl`，各階段保留原有紀錄及備份。獨立清理選項本身已在修改前執行檢查；雲端流程在掃描之前執行檢查；獨立 local tasks 整理在連接 app-server 前執行檢查。

核對由 App 執行。程式觀察其同步狀態完成後，才清除有 `conversation_deleted` 證據的殘留索引；不會僅因同名、404、inaccessible、missing_candidate 或雲端清單未列出而自動刪除。全日期掃描是本機盤點，不代表全歷史雲端核對；既有受測 App 版本的核對範圍約 30 天，專案涵蓋程度仍未逐筆驗證。因此流程完成不代表所有雲端不同步項目都已清除。

```powershell
python maintain_sidebar.py
python maintain_sidebar.py --apply
python organize_local_threads.py --apply --section-name "我的本機任務"
```

不加 `--apply` 時只做檢查及掃描，不重設或等待同步。雲端流程可用 `--codex-home`、`--log-root`（可重複）、`--output-dir`、`--wait-seconds`（等待 App 退出）及 `--reconcile-wait-seconds`（等待核對完成）。所有階段共用同一個 Codex 資料目錄與輸出目錄。

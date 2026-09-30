# Codex 側欄修復工具（Windows / macOS）

## 說明
這是我讓 codex 自己做出清理自己用的工具，將整個資料夾放在可寫入的位置。Windows 雙擊 `start.cmd`；macOS 在終端機執行 `sh start.command`。需要已安裝的 Python 3.10 或以上版本；不需要 pip 套件。macOS 從 `~/Library/Logs/com.openai.codex` 搜尋桌面日誌，Windows 維持原本的位置。

## 選單

原有三個選單功能都先執行原本 Check / verify 的完整性、已知刪除 ID、索引數量及同步狀態檢查；檢查失敗就停止。原本的檢查、要求核對與掃描不再各佔一個選單項目，底層命令仍供整合流程及診斷使用。

1. **Clean confirmed deleted entries**：先檢查，再掃描日誌並清除有 `conversation_deleted` 證據的本機 ChatGPT 索引，包含專案內項目。
2. **Verify, scan, reconcile with cloud, then clean**：檢查 → 全日期掃描（含專案）→ 要求 App 核對 → 等待 App 核對完成 → 清理已確認刪除的索引。
   App 核對後若沒有待清索引，流程會直接完成，不會要求再次退出；只有實際需要清理時才等待第 2 次退出。
3. **Organize local tasks**：先檢查，再把符合條件的 local tasks 移到「本機 Codex」section，與雲端核對清理分開執行。
4. **Delete selected local archived threads**: 輸入本機專案資料夾, 預覽該專案的封存對話與相依項目; 輸入 `DELETE` 確認後備份並刪除
5. **Exit**: 離開

整理會把未封存、未指派專案、未放入其他 section 的本機 Codex 任務移到「本機 Codex」。不存在時建立，同名 section 存在時重用；同名多個則停止。保留內容、釘選、專案、其他 section 及封存狀態。

區段整理需安裝相容的 Codex CLI（可在命令列執行 `codex`），使用其 app-server 區段介面，不直接修改資料庫。此功能不用退出桌面 App；但獨立程式的變更可能需要重開桌面才顯示。若版本不支援、資料結構不同或實際資料目錄不符，會停止並記錄錯誤。

```powershell
python organize_local_threads.py
python organize_local_threads.py --apply
python organize_local_threads.py --apply --section-name "我的本機任務"
```

第一行只預覽。可用 `--codex` 指定 Codex 執行檔，`--codex-home` 指定資料目錄。記錄存於 `logs/local-organize-時間/`，含計畫、原區段快照、每筆搬移與驗證結果。搬移採逐筆提交；部分失敗時已完成的分類保留，重新執行可接續。程式不會把已封存的任務還原。此 Python 模組使用跨平台標準庫；macOS 的區段介面仍須配合已安裝的 Codex CLI 版本驗證。

> 清理／重設時請完全退出 Codex/ChatGPT，但保持這個命令視窗開啟。macOS 請用「結束」而非只關閉視窗。不要在 Codex 內建終端啟動後再退出 App；先前背景程序曾停在等待狀態。出現 `completed` 表示已知刪除項目的資料庫清理完成；`awaiting_app_reconciliation` 只表示同步重設已提交，仍需重開 App 等它完成核對。

## 刪除指定本機封存對話

使用 `delete_archived_threads.py` 的官方 `thread/delete` 介面, 先刪引用歷史的 fork 與 spawned 子對話, 再刪原對話, 可處理隱藏 fork 讓全部刪除失敗的情況

選單第 4 項依輸入的專案資料夾選出封存 user threads, 顯示完整 ID 與相依項目供確認; 空白或未輸入 `DELETE` 就取消. 活動中 fork / 子對話會阻擋整批刪除, 不會自動封存

也可建立 UTF-8 JSON 陣列, 只放明確選定的本機 thread ID, 先預覽再執行:

```powershell
python delete_archived_threads.py --ids-file selected-archives.json
python delete_archived_threads.py --ids-file selected-archives.json --include-archived-dependencies
python delete_archived_threads.py --ids-file selected-archives.json --include-archived-dependencies --apply
```

`--include-archived-dependencies` 明確允許納入引用所選歷史的封存 fork; spawned descendants 屬官方刪除的連帶範圍, 也會先盤點, 備份與確認封存狀態. 不認得的 ID, 雲端對話, 缺少 rollout, 循環引用或備份後有狀態 / 範圍變動都會停止

支援 `--codex`, `--codex-home`, `--output-dir`. 預覽不啟動 app-server, 不刪除資料. 實際刪除需要相容的 Codex CLI; 不需要退出 App, 畫面可能需重新開啟才更新. macOS 僅以標準庫與測試資料驗證, 尚未操作正式對話

`backups/archive-delete-時間/` 保存 SQLite 一致備份, 所有受影響 rollout, 現存歷史 / 記憶 / goals / queue 資料庫, 桌面分類狀態與 manifest; `logs/archive-delete-時間/` 保存計畫及每筆結果. 正式刪除無法在 App 復原, 備份供人工復原評估, 不可直接覆蓋使用中的資料庫. 逐筆成功即提交, 中途失敗不會回滾先前成功的刪除; 先核對 log, 僅以剩餘 ID 建立新計畫再重試

ChatGPT 雲端封存對話請從 ChatGPT 網頁的設定 > 資料控制 > 已封存的對話 > 管理, 逐筆刪除; 已實際確認此路徑可處理 Codex 顯示刪除失敗的對話. 本工具不自動登入或刪除雲端對話, 也不把本機索引消失視為雲端已刪除

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

## 清除使用者確認已從雲端刪除的本機索引

若雲端聊天已由使用者確認刪除，但桌面日誌沒有 `conversation_deleted` 紀錄，可用 `--confirmed-deleted-plan plan.json` 指定單筆或多筆本機索引，包含專案內聊天。此模式只刪本機側欄快取，不會刪雲端聊天；「無法開啟」、404 或雲端清單缺席本身都不足以設定 `confirmed_deleted_in_cloud: true`。計畫應保存在本機，不要上傳到 repo。

```json
{
  "schema_version": 1,
  "scope": "confirmed_deleted_chatgpt_cache",
  "confirmed_deleted_in_cloud": true,
  "host_id": "chatgpt:account-host",
  "entries": [
    {"id": "00000000-0000-0000-0000-000000000001", "title": "已刪除的聊天", "project_id": "原專案 ID"}
  ]
}
```

先執行 `python clean_codex_catalog.py --confirmed-deleted-plan plan.json` 預覽，再於外部終端機以 `--apply` 執行並完全結束桌面 App。寫入時會再次比對帳戶、ID、標題及專案歸屬；任何一筆變動就停止。修改前建立一致的 SQLite 備份，交易失敗會回滾。

預設資料庫：`$CODEX_HOME/sqlite/codex-dev.db`，未設定 CODEX_HOME 時使用目前使用者的 `~/.codex`。可用 `--codex-home` 或 `--database` 指定其他位置；用 `--log-root` 指定桌面日誌根目錄（可重複）；用 `--output-dir` 指定 logs/backups 的父目錄。

```powershell
python clean_codex_catalog.py --verify --database "D:\CodexData\sqlite\codex-dev.db" --log-root "D:\CodexLogs" --output-dir "D:\CleanerResults"
```

## 雲端快照核對

`python compare_cloud_catalog.py --cloud-snapshot cloud-snapshot.json` 可依 ID 比對獨立取得的雲端快照與本機全部 ChatGPT 索引，涵蓋專案歸屬、標題差異、本機獨有與雲端獨有項目；報告保存在 `logs/cloud-compare-時間/`。此功能不會登入、下載雲端清單或修改資料。必須先從相同帳戶取得清單，不能把本機清單當成雲端證據。

快照格式為 JSON 物件：`schema_version: 1`、`source`、ISO 日期 `captured_at`、`account_label`、`coverage` 與 `conversations`。coverage 必須分別以布林值標示 `recents`、`projects`、`archived`、`cloud_work` 是否完整；conversations 每筆包含 UUID 格式 `id`、`title` 與明確的 `project_id`（無專案為 null）。未完整讀取的範圍必須填 false；完整性聲明不等同工具已驗證分頁。

若快照有逐筆核對顯示位置，可為聊天加 `"appearances": ["recents", "project"]`（也可只列其中一處）。報告的 `recents_project_same_chat` 會列出同一 ID 同時出現在兩處的聊天，`same_title_distinct_chats` 則列出同標題但 ID 不同、仍需人工檢查的項目。同一 ID 只有一份聊天資料：工具不會把「最近項目」當成可單獨刪除的副本，也不會為了消除顯示重複而刪除專案聊天。報告中的 `appearance_cleanup_candidates` 預設為空；清理已由使用者確認刪除的聊天仍使用前述單筆計畫。

此核對模組使用標準 Python 與唯讀 SQLite，可供其他系統指定相容資料庫使用。核對報告不會將「雲端未列出」自動判定為已刪除，也不會自動建立刪除計畫。

封存項目可加 `archived: true`，它們不需要出現在最近清單，未快取的封存項目會另外列出。若擷取的文字混有內容預覽，必須加 `title_verified: false`，此時只比對 ID 與專案歸屬，不能宣稱標題已核對。`visible_cloud_aligned_to_snapshot` 只表示可見雲端 ID 與歸屬對上本次快照，不表示內容可載入、未來保持同步或本機 Codex 任務應與手機相同。

## 安全範圍與限制

原有快取清理與區段整理不呼叫雲端刪除 API, 不修改登入資料, 本機對話內容或工作檔案; 新增的本機封存刪除功能只在明確選定與確認後呼叫官方刪除介面, 會刪除所選本機對話及確認的封存相依項目

快取清理修改前會檢查資料表欄位, 備份與完整性; 交易失敗會回滾, 其他索引及本機同步狀態會比對是否保持不變. 本機對話刪除採逐筆提交, 失敗時停止剩餘項目, 保留已成功刪除的結果與備份. 資料結構不相容或來源不明確時停止

「驗證通過」不是已逐筆證明全部雲端對話有效。未曾留下刪除錯誤、超過 App 核對範圍、或位於其他專案清單快取的殘留，仍可能需要額外診斷。不要只因對話從一般清單消失就判斷已刪除：它可能已移入專案。

此版已在 Windows 與 macOS 的資料庫上做唯讀驗證，並以測試資料庫驗證專案內外處理、交易回滾及備份。macOS 正式資料庫清理仍須在 App 完全結束後執行；不能承諾未來 App 更新後永遠相容。


## 雲端核對與清理流程

從外部命令視窗執行 `start.cmd`，選 **2. Verify, scan, reconcile with cloud, then clean**：

1. 自動執行原本的檢查，再掃描所有日期、包含專案的本機索引，輸出 JSON／CSV。
2. 依提示完全退出 Codex/ChatGPT，保持命令視窗開啟。程式備份並重設核對進度。
3. 依提示重新開啟 Codex。程式等到唯一 ChatGPT host 的同步狀態顯示初始建置完成、已有核對時間，且沒有未完成的 full scan checkpoint，才繼續。App 後續產生的 incremental scan checkpoint 不會阻擋流程。預設最多等 30 分鐘；逾時不執行清理。
4. 程式重新讀取日誌證據。若仍有已確認刪除的本機索引，才依提示再次完全退出 Codex/ChatGPT，接著備份並清理；沒有待清項目時直接完成。

清理完成後可再開啟 Codex。若需要整理 local tasks，另選選單第 3 項；雲端清理流程不會搬動 local tasks。

任一步失敗即停止後續步驟，先前成功的步驟會保留。整體紀錄位於 `logs/sidebar-workflow-時間/cleanup.jsonl`，各階段保留原有紀錄及備份。獨立清理選項本身已在修改前執行檢查；雲端流程在掃描之前執行檢查；獨立 local tasks 整理在連接 app-server 前執行檢查。

核對由 App 執行。程式觀察其同步狀態完成後，才清除有 `conversation_deleted` 證據的殘留索引；不會僅因同名、404、inaccessible、missing_candidate 或雲端清單未列出而自動刪除。全日期掃描是本機盤點，不代表全歷史雲端核對；既有受測 App 版本的核對範圍約 30 天，專案涵蓋程度仍未逐筆驗證。因此流程完成不代表所有雲端不同步項目都已清除。

```powershell
python maintain_sidebar.py
python maintain_sidebar.py --apply
python organize_local_threads.py --apply --section-name "我的本機任務"
```

不加 `--apply` 時只做檢查及掃描，不重設或等待同步。雲端流程可用 `--codex-home`、`--log-root`（可重複）、`--output-dir`、`--wait-seconds`（等待 App 退出）及 `--reconcile-wait-seconds`（等待核對完成）。所有階段共用同一個 Codex 資料目錄與輸出目錄。

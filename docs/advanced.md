# 進階用法

一般操作請使用 [啟動選單](../README.md); 帶 `--apply` 的命令會寫入, 其餘範例用於預覽或建立核對資料; 修改前仍會核對範圍與備份

## 本機封存與分類

`selected-archives.json` 是精確本機 thread ID 的 JSON 陣列; 不接受雲端 ID; `--include-archived-dependencies` 允許納入所選歷史的封存 fork, 活動中的 fork 或子對話會阻擋刪除

```powershell
python delete_archived_threads.py --ids-file selected-archives.json --include-archived-dependencies
python organize_local_threads.py --section-name "我的本機任務"
```

內容檔案已遺失時加 `--allow-missing-rollouts`; 備份保存剩餘資料與 `missing_rollout_ids`, 不會還原遺失內容; 實際刪除使用 Codex CLI 的 `thread/delete`, 不直接刪改資料庫; 部分失敗時先查看已成功的 ID, 僅以剩餘 ID 建立新計畫

## 手動修正計畫

| 操作 | 命令 / 格式 |
| --- | --- |
| 舊專案參照 | `python clean_sidebar_references.py --plan plan.json`; 可由 `--interactive` 核對並產生計畫 |
| 已核對的參照與重新同步 | `python maintain_sidebar.py --sidebar-reference-plan plan.json --apply`; 使用已核對的計畫並執行同步 |
| 建立封存請求 | `python plan_sidebar_cleanup.py --titles-file titles.json`; 標題檔為 JSON 字串陣列 |
| 對齊其他裝置最近清單 | `python clean_codex_catalog.py --align-recents plan.json`; 只處理本機非專案快取 |

最近清單計畫需 `schema_version: 1`, `scope: "visible_nonproject_chatgpt"`, `confirmed_complete_reference: true`, 正確 `host_id`, 非空且不重疊的 `keep_ids` / `remove_ids`; 新對話或專案歸屬改變時必須重新核對, 不作為永久白名單

封存請求使用歷史精確 ID 與標題, 排除保留分界及目前有效索引; 同名與模糊比對僅供核對; 請求需透過已登入的 Codex app 執行, 並另行核對封存清單; `maintain_sidebar.py --reviewed-sidebar-plan plan.json --archived-snapshot snapshot.json` 會檢查同帳戶且一小時內取得的原生封存快照, 尚未完成時不進行本機同步

## 已確認從雲端刪除的索引

只有同帳戶的雲端刪除已確認後, 才能使用以下格式; 無法載入或清單缺席不足以設定確認欄位

```json
{
  "schema_version": 1,
  "scope": "confirmed_deleted_chatgpt_cache",
  "confirmed_deleted_in_cloud": true,
  "host_id": "chatgpt:account-host",
  "entries": [
    {"id": "00000000-0000-0000-0000-000000000001", "title": "已刪除的聊天", "project_id": null}
  ]
}
```

```powershell
python clean_codex_catalog.py --confirmed-deleted-plan plan.json
```

寫入時再次比對帳戶, ID, 標題與專案歸屬; 只移除本機索引, 不執行雲端刪除; 計畫與個人快照保存在本機, 不公開提交

## 雲端快照比對

```powershell
python compare_cloud_catalog.py --cloud-snapshot cloud-snapshot.json
```

快照需 `schema_version: 1`, `source`, ISO 日期 `captured_at`, `account_label`, `coverage` 與 `conversations`; `coverage` 以布林值標示 `recents`, `projects`, `archived`, `cloud_work` 是否完整; 每筆對話需 UUID `id`, `title` 與明確的 `project_id` (無專案為 null)

可加 `archived: true`, `title_verified: false` 或 `appearances: ["recents", "project"]`; 專案顯示位置需有 `project_id`; 同一 ID 在最近與專案顯示仍是同一份聊天, 不提供單獨刪除最近位置的動作; 比對報告不把缺席判定為已刪除, 也不自動建立刪除計畫

## 資料位置與等待

各 CLI 的 `--help` 列出可用選項; 介面統一使用英文, `--lang en` 可省略; `--codex-home` 指定 Codex 資料目錄, `--output-dir` 指定 logs / backups 的父目錄; 使用自訂 `--database` 時須提供對應的 `--codex-home`, `--log-root` 可重複指定

`maintain_sidebar.py` 先檢查與掃描, 依核對計畫處理參照後要求 App 重新同步; 依提示退出與重開 App, 預設最多等待 30 分鐘; 同步完成後只清除有刪除證據的索引; 若無待清索引就不要求再次退出; `--wait-seconds` 與 `--reconcile-wait-seconds` 可調整等待時間

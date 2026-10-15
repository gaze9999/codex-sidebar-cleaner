# Codex Sidebar Cleaner

透過 CLI 終端機選單修復 Codex 側邊欄的殘留索引與舊專案參照, 並提供本機 / ChatGPT 雲端封存對話的處理入口

## 開始使用

目前使用原始碼 CLI, 需要既有 Python 3.10+ 與標準函式庫, 不自動安裝相依套件. 下載包依明確發布授權交付, 來源 Tag 與 GitHub Release 分開管理. 本機封存刪除與對話整理仍需相容的 Codex CLI

| 平台 / 來源 | 啟動入口 |
| --- | --- |
| Windows 原始碼 CLI | `launch-cli.cmd`, 顯示終端機選單 |
| macOS 原始碼 CLI | `sh launch-cli.command`, 顯示終端機選單 |
| 原始碼 CLI | 對應平台的啟動入口, 或 `python launch-cli.py` |

請保留原始碼的完整目錄結構. 日後下載免安裝包時需完整解壓, 保留 `runtime/` 與其他子目錄, 不單獨搬出 exe, macOS 選符合處理器的套件

預設繁體中文, 主選單與整理選單可輸入 `L` 切換 English, 編碼無法輸出中文時自動改用英文. Windows 啟動器保持純 ASCII

可在 CMD / macOS 入口後加 `en` 直接以英文啟動, PowerShell 使用 `./launch-cli.cmd en`

請從 Codex 外部啟動, 需要清理快取時, 依提示完全退出 App, 清理完成後再重開 App, 並保留 cleaner 終端機. macOS 請結束 App, 不只關閉視窗

## 選單

按操作後閱讀執行訊息, 依提示輸入一行回覆並按 Enter, 刪除仍需手動輸入 `DELETE` 或 `CLOUD DELETED`

| CLI 主選單 | 用途 |
| --- | --- |
| 1. 檢查側邊欄 (唯讀) | 掃描索引、專案參照與載入錯誤, 只產生本機報告 |
| 2. 整理側邊欄 | 移除指定參照、套用計畫或整理本機分類 |
| 3. 管理 ChatGPT 雲端封存 | 開啟網頁管理, 或核對精確 ID 後清理已刪除對話的本機索引 |
| 4. 永久刪除本機封存對話 | 依專案預覽範圍, 備份後輸入 `DELETE` 確認 |
| L. English | 切換為英文, 英文介面可用 `L. Chinese` 切回中文 |
| 0. 離開 | 結束工具 |

整理側邊欄選單按 `0` 返回主選單:

| 整理側邊欄 | 用途 |
| --- | --- |
| 1. 選擇並移除舊專案參照 | 只修改本機側邊欄分類, 保留雲端專案與對話 |
| 2. 套用已核對的參照清理計畫 | 選擇計畫 JSON, 有 `logs/pending-sidebar-reference-plan.json` 時可按 Enter 使用 |
| 3. 將未分類本機對話集中到 Local Codex | 保留既有分類與對話內容 |
| 4. 建立雲端封存請求清單 | Windows 使用截圖, macOS 使用已核對的標題 JSON, 只建立計畫 |

操作結束後顯示結果, 按 Enter 關閉工具

## 封存對話

**本機**: 輸入專案資料夾後預覽封存對話與相依項目, 輸入 `DELETE` 才備份並永久刪除. 活動中的相依對話會阻擋刪除, 內容檔案已遺失時可清除殘留資料, 但遺失內容無法備份

**雲端**: 開啟 ChatGPT 封存管理, 使用相同帳號在網頁逐筆核對與確認刪除. 網頁已刪除但 Codex 仍顯示時, 選雲端流程第 2 項, 核對精確 ID 後輸入 `CLOUD DELETED` 清理本機索引. 工具不自動刪除雲端對話, 雲端刪除結果由使用者確認

目前網頁入口為 [設定 > Archived chats](https://chatgpt.com/settings/archived-chats), 部分介面仍在 資料控制 > 已封存的對話 > 管理. 刪除後請重新整理並核對原對話連結, 若項目又出現或內容仍可載入, 雲端刪除尚未確認, 不要使用本機索引清理. 可從原對話的 更多 > 刪除 核對操作, 持續失敗時, 保留對話連結、發生時間與錯誤訊息, 依 [OpenAI 支援說明](https://help.openai.com/en/articles/6614161-how-can-i-contact-support) 請支援人員檢查雲端刪除與專案歸屬. 此工具無法修復伺服器上的刪除失敗, 不提供刪除全部對話或專案的替代操作

## 記錄檔與限制

- `logs/` 保存計畫與逐筆結果, `backups/` 保存修改前的資料, 不要公開上傳, 可能包含私人對話資訊
- 修改前會核對帳戶, 精確 ID, 來源及相依項目, 狀態改變或資料格式不符就停止
- 歷史 `conversation_deleted` 記錄未綁定帳戶與資料目錄, 只供診斷, 不自動清除索引, 請由主選單第 3 項核對並確認
- Windows 會排除已辨識的 Codex CLI 與 VS Code CLI 安裝路徑, 未知的 `codex.exe` 或無法讀取的程序路徑仍會阻擋寫入
- 尚未建立自訂側邊欄區段時略過參照清理, 已提供計畫但參照不存在或資料格式異常時停止
- 無法載入, 404, 同名, OCR 或清單缺席都不是刪除證據, 完成 App 同步也不代表逐筆驗證了全部雲端歷史
- 本機對話永久刪除無法在 App 還原, 中途失敗時, 先前成功的刪除仍保留, 請先核對記錄檔再重試
- 本機測試使用隔離資料, 不刪除真實對話, 免安裝包的跨平台檢查由 CI 執行, 尚未執行的 CI 不列為驗證完成
- 發布包目前未設定正式程式簽署與 macOS 公證, 下載後可能出現系統的來源確認, 不代表已完成簽署驗證

`操作已結束` / `Finished` 表示操作結束 (含取消), `仍有項目待處理` / `Action needed` 表示仍有項目待處理, 請依上方提示完成. `流程已停止` / `Stopped` 表示流程停止, 對應結束代碼為 `0`, `2` 與其他非零值

## 命令列

```powershell
python app/clean_codex_catalog.py --verify
python app/maintain_sidebar.py --help
python app/delete_archived_threads.py --help
```

各 CLI 預設使用繁體中文, 可加 `--lang en` 改用英文, 或 `--lang zh-TW` 指定中文, 無法輸出中文時仍會改用英文. `SIDEBAR_CLEANER_LANG` 環境變數也接受這兩個語言值, 指定的命令列語言優先

原始碼的各 CLI 預設資料目錄為 `CODEX_HOME` 或 `~/.codex`, 可用 `--codex-home` 與 `--output-dir` 指定其他位置

原始碼及免安裝包的 logs / backups 都預設保存在各自套件根目錄, 不跟隨目前工作目錄. [進階用法與 JSON 格式](advanced.md) 集中說明手動計畫, 雲端快照與其他 CLI

## 原始碼與打包

根目錄的使用者入口為 `launch-cli.cmd` (Windows)、`launch-cli.command` (macOS) 與共用實作 `launch-cli.py`, 原始碼與免安裝包共用 `launch-cli.py` 的啟動、工具分派與 SQLite runtime 檢查. 免安裝包將共用實作打包進執行檔, 每個平台只附對應的啟動入口. `app/` 放功能模組、scripts 與 CLI 圖示, `tests/` 依 CLI、封存、修正計畫及維護流程集中測試, `docs/` 放中文說明, `tools/` 放打包工具

本機最小檢查:

```powershell
python -X utf8 -m unittest tests.test_cli
```

本機不執行封裝測試, 原生打包交由 Release CI 執行. 打包相依套件集中在 `tools/build-requirements.txt`, 只在 CI 安裝, 啟動不下載或安裝相依套件

GitHub Actions 的 Build portable CLI 可手動執行, 流程設定為產出 Windows x64 ZIP、macOS Intel 與 Apple Silicon tar.gz, 共三份 CLI 套件與各自的 SHA-256. 每個平台檢查 SQLite runtime、全部工具的說明入口與 CLI 選單

完整版本 annotated Tag 使用 `vX.Y.Z`, 0.x 與 1.x 使用相同程序. 推送 Tag 只建置 Actions artifacts, 不建立 Release. 手動執行指定既有 Tag, `publish` 預設 false, 明確啟用且三平台全部通過後才將套件與 SHA-256 附加至草稿 Release, 核對所有資產後依授權公開. 流程不自行建立 Tag、不自動公開、不覆寫已發布 Release 或重複資產, 不完整版本號與 prerelease Tag 不進入此正式建置流程

macOS 使用原生 runner 打包, 保留符號連結與檔案權限, 本機 Windows 檢查不代表 macOS 已通過

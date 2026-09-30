#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ "${1:-}" = en ]; then SIDEBAR_CLEANER_LANG=en; fi
case "${SIDEBAR_CLEANER_LANG:-}" in en) ;; *) SIDEBAR_CLEANER_LANG=zh-TW ;; esac
export SIDEBAR_CLEANER_LANG

while :; do
if [ "$SIDEBAR_CLEANER_LANG" = en ]; then
echo "1. Clean confirmed deleted entries (includes projects)"
echo "2. Verify, review stale project references, reconcile, then clean"
echo "3. Organize local tasks into a separate section"
echo "4. Clean archived conversations that cannot be deleted (local / cloud)"
echo "5. Remove selected stale sidebar project references"
echo "6. Prepare cleanup requests from verified sidebar titles"
echo "7. Exit"
echo "8. 切換為繁體中文"
echo "9. Run a reviewed sidebar repair plan"
printf 'Choose [1-9]: '
else
echo "1. 清除已確認刪除的索引 (含專案)"
echo "2. 檢查舊專案參照, 重新核對清單並清理"
echo "3. 將本機對話整理到獨立區段"
echo "4. 清理無法刪除的已封存對話 (本機 / 雲端)"
echo "5. 移除選定的側邊欄舊專案參照"
echo "6. 依已核對的側邊欄標題建立封存請求"
echo "7. 離開"
echo "8. Switch to English"
echo "9. 執行已核對的側邊欄修正清單"
printf '請選擇 [1-9]: '
fi
read -r choice
if [ "$choice" != 8 ]; then break; fi
if [ "$SIDEBAR_CLEANER_LANG" = en ]; then SIDEBAR_CLEANER_LANG=zh-TW; else SIDEBAR_CLEANER_LANG=en; fi
done
args=--apply
case "$choice" in
  1) script=clean_codex_catalog.py ;;
  2) script=maintain_sidebar.py ;;
  3) script=organize_local_threads.py ;;
  4) script=clean_archived_conversations.py; args=--interactive ;;
  5) script=clean_sidebar_references.py; args=--interactive ;;
  6) script=plan_sidebar_cleanup.py; args=--interactive ;;
  7) exit 0 ;;
  9) script=maintain_sidebar.py; args=--interactive-sidebar-plan ;;
  *) if [ "$SIDEBAR_CLEANER_LANG" = en ]; then echo "Invalid choice"; else echo "選項無效"; fi; exit 2 ;;
esac

if [ "$SIDEBAR_CLEANER_LANG" = en ]; then
  echo "Logs: $(pwd)/logs"
  echo "Cache cleanup needs Codex closed. Section organization does not."
else
  echo "日誌: $(pwd)/logs"
  echo "清理本機快取時需要完全退出 Codex; 區段整理不需要"
fi
case "$choice" in
  2) python3 -X utf8 -u "$script" --apply --review-sidebar-references ;;
  9) python3 -X utf8 -u "$script" --apply --interactive-sidebar-plan ;;
  *) python3 -X utf8 -u "$script" "$args" ;;
esac
status=$?
if [ "$SIDEBAR_CLEANER_LANG" = en ]; then
  echo "Exit code: $status"
  if [ "$status" = 2 ]; then echo "Items remain pending or need web confirmation. See the instructions above."; fi
  printf 'Press Return to close: '
else
  echo "結束代碼: $status"
  if [ "$status" = 2 ]; then echo "尚有項目待處理或需在網頁確認, 請查看上方提示"; fi
  printf '按 Enter 關閉: '
fi
read -r ignored
exit "$status"

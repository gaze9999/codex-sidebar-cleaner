#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ "${1:-}" = en ]; then SIDEBAR_CLEANER_LANG=en; fi
case "${SIDEBAR_CLEANER_LANG:-}" in en) ;; *) SIDEBAR_CLEANER_LANG=zh-TW ;; esac
export SIDEBAR_CLEANER_LANG
menu=main
while :; do
  if [ -t 1 ] && command -v clear >/dev/null 2>&1; then clear 2>/dev/null || :; fi
  if [ "$menu" = main ]; then
    printf 'Codex Sidebar Cleaner\n\n'
    if [ "$SIDEBAR_CLEANER_LANG" = en ]; then
      printf '1. Repair sidebar\n2. Clean archived conversations\n3. Advanced tools\n\nL. 繁體中文\n0. Exit\n'
      printf 'Choose [1-3,L,0]: '
    else
      printf '1. 修復側邊欄\n2. 清理封存對話\n3. 進階工具\n\nL. English\n0. 離開\n'
      printf '請選擇 [1-3,L,0]: '
    fi
  else
    if [ "$SIDEBAR_CLEANER_LANG" = en ]; then
      printf 'Advanced tools\n\n1. Run a reviewed repair plan\n2. Remove stale project references\n3. Prepare an archive plan from verified titles\n4. Organize local conversations\n5. Clean confirmed deleted entries only\n\n0. Back\n'
      printf 'Choose [1-5,0]: '
    else
      printf '進階工具\n\n1. 執行已核對的修正清單\n2. 移除舊專案參照\n3. 依核對標題建立封存清單\n4. 整理本機對話\n5. 只清除已刪除的索引\n\n0. 返回\n'
      printf '請選擇 [1-5,0]: '
    fi
  fi
  read -r choice || exit 0
  args=--apply
  mode=direct
  case "$menu:$choice" in
    main:0) exit 0 ;;
    main:L|main:l)
      if [ "$SIDEBAR_CLEANER_LANG" = en ]; then SIDEBAR_CLEANER_LANG=zh-TW; else SIDEBAR_CLEANER_LANG=en; fi
      continue ;;
    main:3) menu=advanced; continue ;;
    advanced:0) menu=main; continue ;;
    main:1) script=maintain_sidebar.py; mode=workflow ;;
    main:2) script=clean_archived_conversations.py; args=--interactive ;;
    advanced:1) script=maintain_sidebar.py; mode=reviewed ;;
    advanced:2) script=clean_sidebar_references.py; args=--interactive ;;
    advanced:3) script=plan_sidebar_cleanup.py; args=--interactive ;;
    advanced:4) script=organize_local_threads.py ;;
    advanced:5) script=clean_codex_catalog.py ;;
    *) if [ "$SIDEBAR_CLEANER_LANG" = en ]; then echo "Invalid choice"; else echo "選項無效"; fi; exit 2 ;;
  esac
  break
done
case "$mode" in
  workflow) python3 -X utf8 -u "$script" --apply --review-sidebar-references ;;
  reviewed) python3 -X utf8 -u "$script" --apply --interactive-sidebar-plan ;;
  *) python3 -X utf8 -u "$script" "$args" ;;
esac
status=$?
if [ "$SIDEBAR_CLEANER_LANG" = en ]; then
  case "$status" in
    0) echo "Operation finished" ;;
    2) echo "Items remain pending. See the instructions above." ;;
    *) echo "Operation stopped. Code: $status" ;;
  esac
  if [ "$status" != 0 ]; then echo "Logs: $(pwd)/logs"; fi
  printf 'Press Return to close: '
else
  case "$status" in
    0) echo "操作已結束" ;;
    2) echo "尚有項目待處理, 請查看上方提示" ;;
    *) echo "操作已停止, 代碼: $status" ;;
  esac
  if [ "$status" != 0 ]; then echo "日誌: $(pwd)/logs"; fi
  printf '按 Enter 關閉: '
fi
read -r ignored
exit "$status"

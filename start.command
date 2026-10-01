#!/bin/sh
cd "$(dirname "$0")" || exit 1
SIDEBAR_CLEANER_LANG=en
export SIDEBAR_CLEANER_LANG
menu=main
while :; do
  if [ -t 1 ] && command -v clear >/dev/null 2>&1; then clear 2>/dev/null || :; fi
  if [ "$menu" = main ]; then
    printf 'Codex Sidebar Cleaner\n\n'
    printf '1. Fix sidebar\n2. Clean archives\n3. More tools\n\n0. Exit\n'
    printf 'Choose [1-3,0]: '
  else
    printf 'More tools\n\n1. Apply a reviewed plan\n2. Remove old project links\n3. Prepare an archive plan\n4. Organize local chats\n5. Clean deleted chat cache\n\n0. Back\n'
    printf 'Choose [1-5,0]: '
  fi
  read -r choice || exit 0
  args=--apply
  mode=direct
  case "$menu:$choice" in
    main:0) exit 0 ;;
    main:3) menu=advanced; continue ;;
    advanced:0) menu=main; continue ;;
    main:1) script=maintain_sidebar.py; mode=workflow ;;
    main:2) script=clean_archived_conversations.py; args=--interactive ;;
    advanced:1) script=maintain_sidebar.py; mode=reviewed ;;
    advanced:2) script=clean_sidebar_references.py; args=--interactive ;;
    advanced:3) script=plan_sidebar_cleanup.py; args=--interactive ;;
    advanced:4) script=organize_local_threads.py ;;
    advanced:5) script=clean_codex_catalog.py ;;
    *) echo "Invalid choice"; exit 2 ;;
  esac
  break
done
case "$mode" in
  workflow) python3 -X utf8 -u "$script" --apply --review-sidebar-references ;;
  reviewed) python3 -X utf8 -u "$script" --apply --interactive-sidebar-plan ;;
  *) python3 -X utf8 -u "$script" "$args" ;;
esac
status=$?
case "$status" in
  0) echo "Finished" ;;
  2) echo "Action needed. See the message above." ;;
  *) echo "Stopped. Exit code: $status" ;;
esac
if [ "$status" != 0 ]; then echo "Logs: $(pwd)/logs"; fi
printf 'Press Enter to close: '
read -r ignored
exit "$status"

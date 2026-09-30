#!/bin/sh
cd "$(dirname "$0")" || exit 1

echo "1. Clean confirmed deleted entries (includes projects)"
echo "2. Verify, scan, reconcile with cloud, then clean"
echo "3. Organize local tasks into a separate section"
echo "4. Delete selected local archived threads"
echo "5. Exit"
printf 'Choose [1-5]: '
read -r choice
args=--apply
case "$choice" in
  1) script=clean_codex_catalog.py ;;
  2) script=maintain_sidebar.py ;;
  3) script=organize_local_threads.py ;;
  4) script=delete_archived_threads.py; args=--interactive ;;
  5) exit 0 ;;
  *) echo "Invalid choice"; exit 2 ;;
esac

echo "Logs: $(pwd)/logs"
echo "Cache cleanup needs Codex closed. Section organization does not."
python3 -X utf8 -u "$script" "$args"
status=$?
echo "Exit code: $status"
printf 'Press Return to close: '
read -r ignored
exit "$status"

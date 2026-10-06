#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ -x "runtime/CodexSidebarCleaner/CodexSidebarCleaner" ]; then
    exec ./runtime/CodexSidebarCleaner/CodexSidebarCleaner --cli "$@"
fi
exec python3 -X utf8 -u launch-cli.py "$@"

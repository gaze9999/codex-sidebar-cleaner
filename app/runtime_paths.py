"""Keep reports and backups at the repository root after moving source files."""
from pathlib import Path
import sys

def output_root() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent
    executable = Path(sys.executable).resolve()
    return executable.parents[2]  # package/runtime/<bundle>/<executable>


ROOT = output_root()
APP = Path(sys._MEIPASS) / "app" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def tool_command(script: Path, *flags: str) -> list[str]:
    if getattr(sys, "frozen", False):
        suffix = ".exe" if sys.platform == "win32" else ""
        executable = ROOT / "runtime" / "CodexSidebarCleaner" / ("CodexSidebarCleaner" + suffix)
        return [str(executable), "--tool", script.name, *flags]
    return [sys.executable, "-X", "utf8", "-u", str(script), *flags]

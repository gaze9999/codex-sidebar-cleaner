"""Build the portable CLI on its native platform with an installed PyInstaller."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def smoke(executable: Path) -> None:
    subprocess.run([str(executable), "--self-test"], check=True, timeout=30)
    tools = ["cli", "clean_archived_conversations", "clean_codex_catalog", "clean_sidebar_references",
             "compare_cloud_catalog", "delete_archived_threads", "maintain_sidebar",
             "organize_local_threads", "plan_sidebar_cleanup"]
    for tool in tools:
        result = subprocess.run([str(executable), "--tool", tool + ".py", "--lang", "en", "--help"],
                                capture_output=True, check=True, timeout=30)
        if b"usage:" not in result.stdout:
            raise RuntimeError(f"Missing packaged help: {tool}")
    result = subprocess.run([str(executable), "--cli", "en"], input=b"0\n", capture_output=True,
                            check=True, timeout=30)
    if b"Fix sidebar" not in result.stdout:
        raise RuntimeError("Packaged CLI did not display its menu")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true", help="Build and smoke test without creating release archives")
    parser.add_argument("--check", action="store_true", help="Validate the source and build tool without building")
    args = parser.parse_args()
    if sys.platform not in ("win32", "darwin"):
        parser.error("Build on Windows or macOS, using the target architecture")
    if importlib.util.find_spec("PyInstaller") is None:
        parser.error("PyInstaller is not installed. This command does not install packages; use the CI build workflow")
    icon = ROOT / "app/assets/cleaner.ico"
    if sys.platform == "win32" and not icon.is_file():
        parser.error("Missing Windows CLI icon: app/assets/cleaner.ico")
    if args.check:
        print("CLI build prerequisites: OK")
        return 0
    # A fresh owned directory avoids overwriting an existing local build or package.
    from datetime import datetime
    run = ROOT / "build" / ("release-test-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    run.mkdir(parents=True)
    binary_output = run / "binaries"
    name = "CodexSidebarCleaner"
    modules = [p.stem for p in (ROOT / "app").glob("*.py") if p.stem != "frozen_entry"]
    data = []
    for path in sorted((ROOT / "app").glob("*.py")):
        data.extend(["--add-data", f"{path}:app"])
    data.extend(["--add-data", f"{ROOT / 'app/scripts'}:app/scripts"])
    command = [sys.executable, "-m", "PyInstaller", "--onedir", "--noupx", "--name", name,
               "--console", "--paths", str(ROOT / "app"), "--distpath", str(binary_output),
               "--workpath", str(run / "work" / name), "--specpath", str(run),
               "--python-option", "u", *data]
    if sys.platform == "win32":
        command.extend(["--icon", str(icon)])
    for module in modules:
        command.extend(["--hidden-import", module])
    command.append(str(ROOT / "app/frozen_entry.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    system = "windows" if sys.platform == "win32" else "macos"
    machine = platform.machine().lower()
    architecture = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("amd64", "x86_64") else machine
    package = run / f"codex-sidebar-cleaner-{system}-cli-{architecture}"
    runtime = package / "runtime"
    runtime.mkdir(parents=True)
    shutil.copytree(binary_output / name, runtime / name, symlinks=True)
    shutil.copytree(ROOT / "docs", package / "docs")
    entrances = ("launch-cli.cmd", "launch-cli.ps1") if sys.platform == "win32" else ("launch-cli.command",)
    for entrance in entrances:
        shutil.copy2(ROOT / entrance, package / entrance)
        if entrance.endswith(".command"):
            (package / entrance).chmod(0o755)
    suffix = ".exe" if sys.platform == "win32" else ""
    smoke(runtime / name / (name + suffix))
    print(f"Test package: {package}")
    if args.test_only:
        return 0
    release = ROOT / "dist" / package.name
    release.parent.mkdir(exist_ok=True)
    if release.with_suffix(".zip" if sys.platform == "win32" else ".tar.gz").exists():
        raise FileExistsError("Release archive already exists. Keep or move it before rebuilding")
    archive = Path(shutil.make_archive(str(release), "zip" if sys.platform == "win32" else "gztar",
                                       root_dir=run, base_dir=package.name))
    digest = file_hash(archive)
    (ROOT / "dist" / (package.name + ".sha256")).write_text(digest + "  " + archive.name + "\n", encoding="ascii")
    print(f"Release artifact: {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

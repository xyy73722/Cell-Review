"""Build the Windows application and its corresponding public source archive."""
import hashlib
import os
import platform
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

VERSION = "0.2.0"
NAME = "Cell Review"
MODULES = ("app", "canvas", "model", "paths", "prepare_data", "preview", "series", "session_picker", "traces")
PUBLIC_FILES = ("README.md", "CHANGELOG.md", "LICENSE", "LGPL-3.0.txt", "THIRD_PARTY.md", ".gitignore",
                "requirements.txt", "requirements-build.txt", "build_release.py")
RUNTIME_PACKAGES = ("numpy", "scipy", "Pillow", "h5py", "PyQt5", "PyQt5-Qt5",
                    "PyQt5-sip", "PyInstaller")
TEMPLATES = Path(__file__).resolve().parent
ROOT = TEMPLATES if (TEMPLATES / "src").is_dir() else TEMPLATES.parent


def collect_source(destination):
    (destination / "src").mkdir(parents=True)
    for name in PUBLIC_FILES:
        shutil.copyfile(TEMPLATES / name, destination / name)
    for name in MODULES:
        filename = name + ".py"
        shutil.copyfile(ROOT / "src" / filename, destination / "src" / filename)
    for filename in ("cell-review.svg", "cell-review.ico"):
        shutil.copyfile(ROOT / "src" / filename, destination / "src" / filename)


def collect_licenses(destination):
    destination.mkdir()
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", destination / "CPython.txt")
    shutil.copyfile(TEMPLATES / "LGPL-3.0.txt", destination / "LGPL-3.0.txt")
    for name in RUNTIME_PACKAGES:
        dist = metadata.distribution(name)
        package = destination / f"{name}-{dist.version}"
        package.mkdir()
        for entry in dist.files or ():
            if ".." in entry.parts or entry.is_absolute():
                continue
            if not (entry.name.lower().startswith(("license", "licence", "copying", "notice", "authors"))
                    or "licenses" in entry.parts or entry.name.upper() == "COPYRIGHT"):
                continue
            target = package.joinpath(*entry.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dist.locate_file(entry), target)
        # Some wheel distributions publish their copyright and license in METADATA.
        (package / "METADATA.txt").write_text(dist.read_text("METADATA"), encoding="utf-8")


def write_zip(folder, target, prefix):
    with ZipFile(target, "w", ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, prefix / path.relative_to(folder))


def main():
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64") or sys.maxsize <= 2**32:
        raise SystemExit("Build this release with 64-bit Python on Windows x64.")
    release = ROOT / "release"
    final = release / f"v{VERSION}"
    if final.exists():
        raise SystemExit(f"Release folder already exists: {final}")
    release.mkdir(exist_ok=True)
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    stem = f"Cell-Review-v{VERSION}"
    with TemporaryDirectory(dir=release) as staging, TemporaryDirectory(dir=work) as scratch:
        output = Path(staging) / "artifacts"
        source = output / "github-source"
        collect_source(source)
        scratch = Path(scratch)
        env = os.environ.copy()
        for name, folder in (("TEMP", scratch / "temp"), ("TMP", scratch / "temp"),
                             ("PYTHONPYCACHEPREFIX", ROOT / "cache/pycache"),
                             ("PYINSTALLER_CONFIG_DIR", ROOT / "cache/pyinstaller")):
            folder.mkdir(parents=True, exist_ok=True)
            env[name] = str(folder)
        icon = source / "src/cell-review.ico"
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
                        "--icon", str(icon), "--add-data", f"{icon}:.",
                        "--windowed", "--noupx", "--name", NAME, "--distpath", str(scratch / "dist"),
                        "--workpath", str(scratch / "build"), "--specpath", str(scratch),
                        str(source / "src/app.py")], cwd=source, env=env, check=True)
        app = scratch / "dist" / NAME
        for name in ("README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY.md"):
            shutil.copyfile(source / name, app / name)
        shutil.copytree(source, app / "source")
        collect_licenses(app / "licenses")
        windows_zip = output / f"{stem}-Windows-x64.zip"
        source_zip = output / f"{stem}-source.zip"
        write_zip(app, windows_zip, Path(NAME))
        write_zip(source, source_zip, Path(stem))
        lines = []
        for artifact in (windows_zip, source_zip):
            with artifact.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            lines.append(f"{digest}  {artifact.name}\n")
        (output / "SHA256SUMS.txt").write_text("".join(lines), encoding="ascii")
        output.rename(final)
    print(final)


if __name__ == "__main__":
    main()

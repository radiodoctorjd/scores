"""Build the Windows program folder: dist/RDSBridge/RDSBridge.exe (+ zip).

Run on Windows:  python packaging\\build_windows.py   (needs: pip install pyinstaller pyserial)
"""

import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "RDSBridge")
EXTRAS = ["config.example.json", "README.md", "Start RDS Bridge.bat", "Install autostart.bat",
          "Remove autostart.bat", "samples", "tools"]


def main() -> int:
    import PyInstaller.__main__

    sys.path.insert(0, ROOT)
    from rdsbridge import __version__

    shutil.rmtree(OUT, ignore_errors=True)
    args = [
        os.path.join(ROOT, "packaging", "rdsbridge_main.py"),
        "--name", "RDSBridge", "--onedir", "--console", "--noconfirm", "--clean",
        "--distpath", DIST,
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        "--paths", ROOT,
        "--add-data", f"{os.path.join(ROOT, 'rdsbridge', 'static')}{os.pathsep}rdsbridge/static",
    ]
    try:
        import serial  # noqa: F401  (bundle pyserial so serial encoders work)
        args += ["--hidden-import", "serial"]
        if os.name == "nt":
            args += ["--hidden-import", "serial.serialwin32"]
    except ImportError:
        print("pyserial not installed: the build will not support serial encoders")
    PyInstaller.__main__.run(args)

    for name in EXTRAS:
        src = os.path.join(ROOT, name)
        dst = os.path.join(OUT, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    zip_path = os.path.join(DIST, "RDSBridge-windows.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, _, files in os.walk(OUT):
            for f in files:
                full = os.path.join(folder, f)
                zf.write(full, os.path.join("RDSBridge", os.path.relpath(full, OUT)))
    print(f"\nBuilt RDS Bridge {__version__}: {OUT}\nZip: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

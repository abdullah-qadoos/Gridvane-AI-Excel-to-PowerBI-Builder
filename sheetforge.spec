# Portable one-folder build. Runtime resources remain inspectable beside the EXE.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / "sheetforge" / part), "sheetforge/" + part)
         for part in ["schemas", "web", "storage/migrations"]]
datas += collect_data_files("litellm")
datas += copy_metadata("litellm", recursive=True)
datas += copy_metadata("keyring", recursive=True)
a = Analysis(
    [str(root / "desktop_entry.py")], pathex=[str(root)], datas=datas,
    hiddenimports=["uvicorn.protocols.http.h11_impl", "uvicorn.loops.asyncio",
                   "uvicorn.lifespan.off", "sqlalchemy.dialects.sqlite.pysqlite",
                   "keyring.backends.Windows", "pyarrow.parquet", "tiktoken_ext.openai_public"],
    excludes=["pytest", "ruff"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="sheetforge",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, uac_admin=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="sheetforge")

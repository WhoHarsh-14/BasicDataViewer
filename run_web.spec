# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\HARSH\\TAG Pack\\tag\\MonitoringSystem\\run_web.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\HARSH\\TAG Pack\\tag\\MonitoringSystem\\static', 'static'), ('C:\\Users\\HARSH\\TAG Pack\\tag\\MonitoringSystem\\system_config.json', '.'), ('C:\\Users\\HARSH\\TAG Pack\\tag\\MonitoringSystem\\line_config.json', '.'), ('C:\\Users\\HARSH\\TAG Pack\\tag\\MonitoringSystem\\register_config.json', '.')],
    hiddenimports=['fastapi', 'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'sqlalchemy.ext.asyncio', 'sqlalchemy.dialects.postgresql.asyncpg', 'sqlalchemy.dialects.postgresql.psycopg2', 'asyncpg', 'psycopg2', 'pymcprotocol', 'openpyxl', 'websockets', 'pydantic'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='run_web',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='run_web',
)

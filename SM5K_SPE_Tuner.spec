# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# customtkinter laddar sina temafiler (.json) i runtime — de måste bundlas,
# annars kraschar den frysta byggen med "theme not found". ee_theme.py är en
# lokal modul och plockas upp automatiskt.
_ctk_datas   = collect_data_files('customtkinter')
_ctk_hidden  = collect_submodules('customtkinter')

a = Analysis(
    ['spe_tuner.py'],
    pathex=[],
    binaries=[],
    datas=_ctk_datas,
    hiddenimports=_ctk_hidden,
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
    a.binaries,
    a.datas,
    [],
    name='SM5K_SPE_Tuner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

import shutil, os
_dst = r"C:\claude\aktiv\tune"
os.makedirs(_dst, exist_ok=True)
shutil.copy(os.path.join(DISTPATH, "SM5K_SPE_Tuner.exe"), os.path.join(_dst, "SM5K_SPE_Tuner.exe"))

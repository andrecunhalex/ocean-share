# PyInstaller spec — build cross-platform (Windows/Linux)
# No Windows: pyinstaller surfshare.spec
import os
import sys

block_cipher = None

icon_file = None
if sys.platform == 'win32' and os.path.exists('icon.ico'):
    icon_file = 'icon.ico'
elif sys.platform == 'darwin' and os.path.exists('icon.icns'):
    icon_file = 'icon.icns'

a = Analysis(
    ['surfshare.py'],
    pathex=[],
    binaries=[],
    datas=[('ocean-icon.jpg', '.')],
    hiddenimports=['webview', 'webview.platforms.edgechromium', 'webview.platforms.winforms'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='OceanShare',
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
    icon=icon_file,
)

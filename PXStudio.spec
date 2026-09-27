# -*- mode: python ; coding: utf-8 -*-
# PX Studio 단일 exe 빌드:  .venv\Scripts\pyinstaller --noconfirm PXStudio.spec
from PyInstaller.utils.hooks import collect_all

datas = [('index.html', '.')]
binaries = []
hiddenimports = []
# pywebview(Edge WebView2)와 pythonnet(clr)은 런타임 DLL·어셈블리를 동적으로 불러 hook만으로 빠지는 파일이 있다
for pkg in ('webview', 'clr_loader', 'pythonnet'):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

a = Analysis(['app.py'], pathex=[], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], runtime_hooks=[], excludes=['tkinter'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='PXStudio', icon='px.ico',
          debug=False, strip=False, upx=False, console=False, runtime_tmpdir=None)

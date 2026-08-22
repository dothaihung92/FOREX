@echo off
REM ===================================================================
REM  Gold Bot launcher for Windows.
REM
REM  Double-click this file. It sets up a private Python environment on
REM  first run, then offers a menu. Nothing here touches a real account
REM  unless you pick option 3 and type LIVE to confirm.
REM ===================================================================
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Gold Bot

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"

echo.
echo   ================================================
echo     GOLD BOT
echo   ================================================
echo.

REM --- locate a usable Python -----------------------------------------
if not exist "%PY%" (
    echo   [setup] Khong tim thay moi truong Python rieng, dang tao lan dau...
    echo.
    set "BOOTSTRAP="
    py -3 --version >nul 2>&1 && set "BOOTSTRAP=py -3"
    if not defined BOOTSTRAP (
        python --version >nul 2>&1 && set "BOOTSTRAP=python"
    )
    if not defined BOOTSTRAP (
        echo   [LOI] Chua cai Python.
        echo.
        echo   Tai Python 3.10 tro len tai: https://www.python.org/downloads/
        echo   Khi cai NHO TICH vao o "Add Python to PATH".
        echo.
        pause
        exit /b 1
    )

    REM Reject anything older than 3.10 now, rather than failing later
    REM with a confusing syntax error deep inside the package.
    !BOOTSTRAP! -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo   [LOI] Python qua cu. Can 3.10 tro len.
        !BOOTSTRAP! --version
        echo.
        pause
        exit /b 1
    )

    !BOOTSTRAP! -m venv "%VENV%"
    if errorlevel 1 (
        echo   [LOI] Tao moi truong that bai.
        pause
        exit /b 1
    )
    echo   [setup] Dang cai thu vien can thiet...
    "%PY%" -m pip install --upgrade pip --quiet
    "%PY%" -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo   [LOI] Cai thu vien that bai. Kiem tra ket noi mang.
        pause
        exit /b 1
    )
    echo   [setup] Xong.
    echo.
)

REM A half-finished venv would fail later with a confusing error on
REM whichever menu option the user picked, so check once, here.
if not exist "%PY%" (
    echo   [LOI] Moi truong Python chua san sang.
    echo   Xoa thu muc "%VENV%" roi chay lai file nay de cai lai tu dau.
    echo.
    pause
    exit /b 1
)

:menu
echo.
echo   ------------------------------------------------
echo     1  Xem bieu do (du lieu offline, KHONG rui ro)
echo     2  Bieu do + tai khoan Exness that (CHI DOC)
echo     3  Tai khoan that + DAT LENH  ^<-- tien that
echo     4  Chay backtest
echo     5  Cap nhat code moi nhat
echo     6  Chay kiem thu (tests)
echo     0  Thoat
echo   ------------------------------------------------
echo.
set "CHOICE="
set /p "CHOICE=  Chon (0-6): "

if "%CHOICE%"=="1" goto demo
if "%CHOICE%"=="2" goto readonly
if "%CHOICE%"=="3" goto live
if "%CHOICE%"=="4" goto backtest
if "%CHOICE%"=="5" goto update
if "%CHOICE%"=="6" goto tests
if "%CHOICE%"=="0" exit /b 0
echo   Lua chon khong hop le.
goto menu

:demo
echo.
echo   Che do: du lieu offline, lenh mo phong. Khong dung tien that.
echo   Nhan Ctrl+C de dung.
echo.
"%PY%" scripts\run_dashboard.py
goto done

:readonly
echo.
echo   Che do: du lieu that tu MT5, CHI DOC - khong the dat lenh.
echo   Yeu cau: MT5 dang chay va da dang nhap tai khoan Exness.
echo   Nhan Ctrl+C de dung.
echo.
"%PY%" scripts\run_dashboard.py --broker mt5
goto done

:live
echo.
echo   ################################################################
echo   #  CANH BAO: che do nay dat lenh THAT tren tai khoan that.     #
echo   #  Moi lenh gui di la tien that, khong hoan tac duoc.          #
echo   #                                                              #
echo   #  Neu ban chua thu tren tai khoan DEMO, hay chon 2 truoc.     #
echo   ################################################################
echo.
set "SURE="
set /p "SURE=  Go chinh xac chu LIVE roi Enter (bo trong de huy): "
if /i not "%SURE%"=="LIVE" (
    echo   Da huy. Khong co gi duoc gui di.
    goto menu
)
echo.
echo   Dang mo che do dat lenh that. Nhan Ctrl+C de dung.
echo.
"%PY%" scripts\run_dashboard.py --broker mt5 --live-trading
goto done

:backtest
echo.
"%PY%" scripts\run_backtest.py --config config\config.yaml --data data\XAUUSD_M5_real.csv
goto done

:update
echo.
"%PY%" update.py
goto done

:tests
echo.
"%PY%" -m pytest tests -q
goto done

:done
echo.
echo   ------------------------------------------------
pause
goto menu

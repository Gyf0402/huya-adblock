@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 优先用系统 python；找不到再看 py 启动器
set "PY=python"
where python >nul 2>nul || set "PY=py"

echo ============================================
echo   虎牙直播 PC 客户端 - 去广告补丁
echo ============================================
echo.

if /i "%~1"=="restore" goto do_restore

rem 运行中不允许写文件
tasklist /fi "imagename eq huyapc.exe" 2>nul | find /i "huyapc.exe" >nul
if not errorlevel 1 (
    echo [!] 检测到虎牙直播正在运行。
    echo     请完全退出客户端（含右下角托盘图标）后再执行。
    echo.
    pause
    exit /b 1
)

echo [*] 正在应用补丁...
echo.
%PY% patch_asar.py %*
echo.
pause
exit /b 0

:do_restore
echo [*] 正在从备份还原...
echo.
%PY% patch_asar.py --restore
echo.
pause
exit /b 0

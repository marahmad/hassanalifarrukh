@echo off
title TikTok iPhone Organizer - Hassan Ali Farrukh
cd /d "%~dp0"
echo ========================================================
echo   SYNCING TIKTOK VIDEOS TO IPHONE (ICLOUD DRIVE)
echo ========================================================
python -m tools.organize_iphone_posts --export-folders --days 7
echo.
echo ========================================================
echo   SYNC COMPLETE! OPEN 'FILES' APP ON YOUR IPHONE.
echo   Path: iCloud Drive -> TikTok_Schedule
echo ========================================================
pause

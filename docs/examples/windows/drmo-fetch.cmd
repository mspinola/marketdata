@echo off
REM marketdata DR. MO deck fetch: step 1 of the weekly Dr. Mo routine (drmo-routine.md).
REM Copy this file into your scheduler folder and overwrite the two markers below.
REM Do NOT put angle brackets in a .cmd file, comments included.
REM   REPLACE_WITH_DECKS_SHARE = the decks folder as this box sees it  e.g. \\YOUR-MAC\decks
REM   REPLACE_WITH_VENV_PATH   = your venv  e.g. C:\Users\you\code\marketdata\.venv
REM
REM Copies ONE deck PDF to %TEMP%\drmo\deck.pdf and prints where the Dr. Mo slide is.
REM The deck is the newest Around the Horn file (ath\) when its name is dated within the
REM last 6 days, else the newest Leadoff Morning Note file (lmn\). Files are named by date,
REM so sorting by name is sorting by date. The copy keeps the share from being held open
REM while the routine reads the page. Output on success is two lines:
REM   drmo: deck ath/YYYY-MM-DD.pdf
REM   drmo: slide on page N, report date YYYY-MM-DD
REM Exit 1 when no deck is found or the finder refuses the deck.
powershell -NoProfile -Command "$d='REPLACE_WITH_DECKS_SHARE'; if (-not (Test-Path $d)) { Write-Output ('drmo: cannot reach the decks folder ' + $d + ' (marker not filled in, share not set up, or the Mac is asleep)'); exit 1 }; $t=Join-Path $env:TEMP 'drmo'; New-Item -ItemType Directory -Force $t | Out-Null; $cut=(Get-Date).AddDays(-6).ToString('yyyy-MM-dd'); $f=Get-ChildItem (Join-Path $d 'ath') -Filter *.pdf -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1; if (-not $f -or $f.Name.Substring(0,10) -lt $cut) { $f=Get-ChildItem (Join-Path $d 'lmn') -Filter *.pdf -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1 }; if (-not $f) { Write-Output 'drmo: no deck found'; exit 1 }; Copy-Item $f.FullName (Join-Path $t 'deck.pdf') -Force; Write-Output ('drmo: deck ' + $f.Directory.Name + '/' + $f.Name)"
if errorlevel 1 exit /b 1
"REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe" --find-drmo-slide "%TEMP%\drmo\deck.pdf"

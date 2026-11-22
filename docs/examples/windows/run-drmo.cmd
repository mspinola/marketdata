@echo off
REM marketdata DR. MO build wrapper: stage 2 of the weekly 42 Macro Dr. Mo table.
REM Copy this file into your scheduler folder and overwrite the three markers below.
REM Do NOT put angle brackets in a .cmd file, comments included: cmd reads them as
REM redirection.
REM   REPLACE_WITH_MARKETDATA_STORE_PATH = your BAR store  e.g. C:\Users\you\marketdata_store
REM   REPLACE_WITH_VENV_PATH             = your venv       e.g. C:\Users\you\code\marketdata\.venv
REM   REPLACE_WITH_SCHEDULER_DIR         = this folder     e.g. C:\Users\you\cotdata\scheduler
REM
REM WHAT RUNS THIS
REM ------------------------------------------------------------------------
REM Not a Task Scheduler task. Stage 1 is a Claude Desktop LOCAL ROUTINE (see
REM drmo-routine.md beside this file): Claude transcribes the week's slide to
REM MARKETDATA_STORE\_raw\fortytwo\drmo\YYYY-MM-DD.csv and its last step is this
REM file. Stage 2 is the one command below, which reads only those local files.
REM It can also be run by hand at any time: the build is idempotent, and with
REM nothing new filed it prints "already current" and exits 0.
REM
REM EXIT CODES (same contract as run-series.cmd)
REM ------------------------------------------------------------------------
REM   0  nothing refused.
REM   2  PARTIAL: a week was refused, the table still holds good weeks.
REM   1  nothing usable: no transcription filed, or the only one was refused.
REM Syncs on 0 and on 2, stops on 1. A refusal is printed by the build with the
REM file name and the reason, and it needs a person: see the three resolutions in
REM marketdata docs/design/drmo.md (fix the file, --replace-drmo-week,
REM --accept-drmo-universe). A retry cannot fix a refused file, so there is no
REM retry loop here.
REM
REM The raw CSVs never ride along: both sync scripts exclude _raw by name.
setlocal
set "MARKETDATA_STORE=C:\Users\matt\code\cotdata\scheduler"
set "MDEXE=C:\Users\matt\code\marketdata\Scripts\marketdata-update.exe"

"%MDEXE%" --build-drmo
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" if not "%RC%"=="2" exit /b %RC%

REM Mac sync first, VPS push second, the push NOT gated on the sync (see
REM run-series.cmd for why). Each ERRORLEVEL is captured on its own line.
call "C:\Users\matt\code\cotdata\scheduler\sync-store.cmd"
set "SYNC_RC=%ERRORLEVEL%"

call "C:\Users\matt\code\cotdata\scheduler\push-to-server.cmd"
set "PUSH_RC=%ERRORLEVEL%"

if "%SYNC_RC%"=="" set "SYNC_RC=1"
if "%PUSH_RC%"=="" set "PUSH_RC=1"
set "WORST=%SYNC_RC%"
if %PUSH_RC% GTR %WORST% set "WORST=%PUSH_RC%"
if not "%WORST%"=="0" echo drmo: delivery incomplete ^(sync %SYNC_RC%, push %PUSH_RC%^).

if not "%RC%"=="0" exit /b %RC%
exit /b %WORST%

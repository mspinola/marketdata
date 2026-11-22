# The Dr. Mo routine: what Claude does on the producer box each Sunday

The whole instruction set for the Claude Desktop **local routine** that files the week's
42 Macro Dr. Mo table into marketdata. Copy it into your scheduler folder beside
`run-drmo.cmd`, fill the markers, and point the routine at it with one line:

> Follow REPLACE_WITH_SCHEDULER_DIR\drmo-routine.md exactly.

Markers: `REPLACE_WITH_CODE_ROOT` (e.g. `C:\Users\you\code`),
`REPLACE_WITH_MARKETDATA_STORE_PATH`, `REPLACE_WITH_SCHEDULER_DIR`, and
`REPLACE_WITH_DECKS_SHARE` (the folder holding the 42 Macro deck PDFs as this box sees it,
e.g. `\\YOUR-MAC\decks`; see "Where the decks are" below), and `REPLACE_WITH_VENV_PATH`.

Design and the file format's reasons: marketdata `docs/design/drmo.md`. The transcription is
subscriber data. It stays under `_raw` on this box and is never committed anywhere.

## Where the decks are

The decks are saved on another machine (the Mac), in one folder with a subfolder per deck
kind: `ath\` for Around the Horn and `lmn\` for the Leadoff Morning Note, each file named
by its date, `YYYY-MM-DD.pdf` (older files `YYYY-MM-DD_Slides.pdf`). That folder is shared
to this box **read-only** over SMB, as its own share, apart from the two store shares the
mirror writes to. One-time setup, by a person:

1. On the Mac: System Settings, General, Sharing, File Sharing, add the decks folder, and
   give the account this box connects with Read Only.
2. On this box: open `REPLACE_WITH_DECKS_SHARE` in Explorer once and save the credentials,
   so a routine can read it without a prompt.
3. On this box: `uv pip install --python REPLACE_WITH_VENV_PATH\Scripts\python.exe pypdf`
   (step 1's page finder needs it).

The Mac must be awake when the routine runs. The routine copies the one PDF it needs to a
local temp folder and reads the copy, so nothing is held open on the share. The decks are
subscriber material: the copy stays in the temp folder and nothing from it is committed.

## Routine settings

| field | value |
|---|---|
| Type | Local |
| Folder | `REPLACE_WITH_CODE_ROOT` |
| Schedule | **Sunday**, two routines: about 18:00 ET and about 19:45 ET (the second is the retry). The slide is dated Saturday but the deck normally appears Sunday afternoon, around the 17:00 ET Sunday call (its recording follows at about 18:30). Both runs sit after the 17:00 vintage task and before the 20:55 futures window, whose repeats sync the same replicas this wrapper syncs. On a week already filed the second run writes nothing and exits 0. |
| Instructions | the one line above |
| Permission mode | the default, plus the allow rules below |

Allow rules, in `REPLACE_WITH_CODE_ROOT\.claude\settings.json`, in the style of
`series-routine.md` (paths in POSIX form; `Edit`, not `Write`):

```json
{
  "permissions": {
    "allow": [
      "Read(//YOUR-MAC/decks/**)",
      "Read(/c/Users/you/AppData/Local/Temp/drmo/**)",
      "Bash(cmd //c \"C:/Users/you/code/cotdata/scheduler/drmo-fetch.cmd\")",
      "Edit(marketdata_store/_raw/fortytwo/drmo/**)",
      "Edit(/c/Users/you/code/marketdata_store/_raw/fortytwo/drmo/**)",
      "Bash(cmd //c \"C:/Users/you/code/cotdata/scheduler/run-drmo.cmd\")"
    ]
  }
}
```

`drmo-fetch.cmd` is three lines you keep beside `run-drmo.cmd` (it is not a template here
because it is nothing but your paths). It copies the newest deck to the temp folder and
prints where the slide is. No angle brackets anywhere in it:

```
@echo off
REM Newest Around the Horn deck no older than 6 days, else newest Leadoff deck.
powershell -NoProfile -Command "$d='REPLACE_WITH_DECKS_SHARE'; $t=Join-Path $env:TEMP 'drmo'; New-Item -ItemType Directory -Force $t | Out-Null; $cut=(Get-Date).AddDays(-6).ToString('yyyy-MM-dd'); $f=Get-ChildItem (Join-Path $d 'ath') -Filter *.pdf | Sort-Object Name | Select-Object -Last 1; if (-not $f -or $f.Name.Substring(0,10) -lt $cut) { $f=Get-ChildItem (Join-Path $d 'lmn') -Filter *.pdf | Sort-Object Name | Select-Object -Last 1 }; if (-not $f) { Write-Output 'drmo: no deck found'; exit 1 }; Copy-Item $f.FullName (Join-Path $t 'deck.pdf') -Force; Write-Output ('drmo: deck ' + $f.Directory.Name + '/' + $f.Name)"
if errorlevel 1 exit /b 1
"REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe" --find-drmo-slide "%TEMP%\drmo\deck.pdf"
```

Its output is two lines: which deck was taken, and `drmo: slide on page N, report date
YYYY-MM-DD`. Run it once interactively and copy any rule text that differs from the above
out of the prompt.

## Steps

Do these in order, and nothing else.

1. **Find this week's Dr. Mo slide.**
   - First look at the raw folder (step 5's path). If it already holds a file dated within
     the last six days, this week is filed: write nothing and go to step 6.
   - Run, typed exactly: `cmd //c "C:/Users/you/code/cotdata/scheduler/drmo-fetch.cmd"`.
     It takes this weekend's **Around the Horn** deck, or the most recent **Leadoff Morning
     Note** deck when no Around the Horn deck from the last six days is there, and prints
     the page of the slide and the report date in its title.
   - If it prints REFUSED or `no deck found`, or the report date is more than six days
     ago, this week's is not out: repeat the line and stop. Write nothing.
   - Open **only that page** of `%TEMP%\drmo\deck.pdf`. It is the slide with five blocks
     side by side, each with VAMS, RSI and PROPER TRADE columns. Do not read the rest of
     the deck.
2. **Take the report date from step 1's output**, and check it against the date in the
   slide's title: if they differ, stop and say so. Read the **market regime** from the line
   under the title.
3. **Transcribe every asset row of every block** into CSV with exactly this header:

   ```
   report_date,regime,group,asset,ticker,vams,rsi,proper_trade
   ```

   - `report_date`, `regime`: the same on every row.
   - `group`: the title of the block the row is in, as printed.
   - `asset`: the name as printed, without the parenthesised ticker. Wrap it in double
     quotes if it contains a comma.
   - `ticker`: the text in parentheses after the name. Empty when the row prints none.
   - `vams`: read the mark with the slide's own legend. The check mark is `bullish`, the
     exclamation mark is `neutral`, the cross is `bearish`.
   - `rsi`: the number in the RSI column, digits only.
   - `proper_trade`: from the cell's TEXT, not its colour: `long_max`, `long_half`, `none`
     (printed as no position), `short_half`, `short_max`.

   Do not add a column, a comment, a total or a blank line. Do not skip a row because it
   looks like last week's. Do not correct anything that looks inconsistent: transcribe what
   is printed.
4. **Check your own work before writing.** Count the rows in each block on the slide and in
   your CSV and make them equal. Then read the slide a second time, block by block, against
   the CSV, and fix any cell that differs. If any cell is not legible, stop and say which
   one. Do not guess a cell.
5. **Write the file** to
   `REPLACE_WITH_MARKETDATA_STORE_PATH\_raw\fortytwo\drmo\YYYY-MM-DD.csv`, named by the
   report date, UTF-8. If a file of that name already exists, do not overwrite it: go to
   step 6.
6. **Run the build wrapper**, typed exactly:

   ```
   cmd //c "C:/Users/you/code/cotdata/scheduler/run-drmo.cmd"
   ```

7. **Report** the wrapper's output lines that start with `drmo:` and its exit code. If the
   build printed REFUSED, repeat the refusal verbatim and stop. Do not edit the file to make
   a refusal go away, and do not pass `--replace-drmo-week` or `--accept-drmo-universe`:
   those are a person's decisions.

## When the build refuses

| message | meaning | what the person does |
|---|---|---|
| header / vams / rsi / proper_trade / appears twice | the file is malformed | fix the CSV by hand against the slide, rerun `run-drmo.cmd` |
| the asset list differs from week ... | a row was dropped or renamed, or the publisher changed the list | fix the CSV, or if the slide really changed: `marketdata-update --build-drmo --accept-drmo-universe`, then `run-drmo.cmd` |
| differs from the stored week ... | this file disagrees with what is already stored for that date | compare both with the slide; if the file is right: `marketdata-update --build-drmo --replace-drmo-week YYYY-MM-DD`, then `run-drmo.cmd` |
| every reading equals week ... | last week's slide was transcribed again | delete that CSV, wait for the new slide |

## From a browser session instead

If the transcription is made in a claude.ai Project in a browser, use steps 2 to 4 as the
prompt, have it return the CSV as a file, save it anywhere on the box, and run:

```
marketdata-update --build-drmo C:\path\to\the-download.csv
```

That files it under its own report date. Then run `run-drmo.cmd` for the syncs.

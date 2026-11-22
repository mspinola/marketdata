# The Dr. Mo routine: what Claude does on the producer box each Saturday

The whole instruction set for the Claude Desktop **local routine** that files the week's
42 Macro Dr. Mo table into marketdata. Copy it into your scheduler folder beside
`run-drmo.cmd`, fill the markers, and point the routine at it with one line:

> Follow REPLACE_WITH_SCHEDULER_DIR\drmo-routine.md exactly.

Markers: `REPLACE_WITH_CODE_ROOT` (e.g. `C:\Users\you\code`),
`REPLACE_WITH_MARKETDATA_STORE_PATH`, `REPLACE_WITH_SCHEDULER_DIR`, and
`REPLACE_WITH_HOW_THE_SLIDE_IS_OBTAINED` (one sentence: where this week's slide is, for
example a folder the PDF is saved to, or the connector and search the session should use).

Design and the file format's reasons: marketdata `docs/design/drmo.md`. The transcription is
subscriber data. It stays under `_raw` on this box and is never committed anywhere.

## Routine settings

| field | value |
|---|---|
| Type | Local |
| Folder | `REPLACE_WITH_CODE_ROOT` |
| Schedule | Saturday, once the report is normally out (proposed 12:00 ET), and a second routine Sunday 12:00 ET as the retry. Both clear of the 08:10 COT catch-up, the 17:00 vintage task and the 20:55 futures window, whose runs sync the same replicas this wrapper syncs. On a week already filed the second run writes nothing and exits 0. |
| Instructions | the one line above |
| Permission mode | the default, plus the allow rules below |

Allow rules, in `REPLACE_WITH_CODE_ROOT\.claude\settings.json`, in the style of
`series-routine.md` (paths in POSIX form; `Edit`, not `Write`):

```json
{
  "permissions": {
    "allow": [
      "Edit(marketdata_store/_raw/fortytwo/drmo/**)",
      "Edit(/c/Users/you/code/marketdata_store/_raw/fortytwo/drmo/**)",
      "Bash(cmd //c \"C:/Users/you/code/cotdata/scheduler/run-drmo.cmd\")"
    ]
  }
}
```

Add whatever read rule step 1 needs (a `Read(...)` of the folder the slide is saved to, or
the connector tool), copied from the prompt text of one interactive run.

## Steps

Do these in order, and nothing else.

1. **Get this week's Dr. Mo slide.** REPLACE_WITH_HOW_THE_SLIDE_IS_OBTAINED. It is the slide
   whose title names the Discretionary Risk Management Overlay and ends with a date. If the
   newest one you can find is dated more than six days ago, this week's is not out: say so
   and stop. Write nothing.
2. **Read the report date and the market regime** from the slide's title and the line under
   it. The date becomes `YYYY-MM-DD`.
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

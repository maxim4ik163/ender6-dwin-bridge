# DWIN protocol notes (Ender 6, InsanityAutomation DW7.4.6 UI)

Collected by parsing the compiled `DWIN_SET` (`13TouchFile.bin`, `14ShowFile.bin`) and cross-checking with
InsanityAutomation Marlin, branch [`CrealityDwin_2.0`](https://github.com/InsanityAutomation/Marlin/tree/CrealityDwin_2.0) (release tag [`DW_746`](https://github.com/InsanityAutomation/Marlin/releases/tag/DW_746)), `Marlin/src/lcd/extui/ia_creality/creality_extui.{h,cpp}`.

## Screen

- DGUS II, OS files `T5OS_V20_NOACK.BIN` + `T5UID1_V22.BIN`. "NOACK": the screen does not acknowledge writes.
- 480×272, 41 pages, ASCII-only font (`0_DWIN_ASC.HZK`).
- UART 115200 8N1, 3.3 V TTL.

## Frames

| Direction | Frame | Meaning |
|---|---|---|
| host → screen | `5A A5 len 82 VPh VPl data…` | write variable(s); text ends with `FF FF` |
| host → screen | `5A A5 07 82 00 84 5A 01 00 NN` | switch to page `NN` |
| host → screen | `5A A5 04 83 00 14 01` | read current page (answer `… 83 00 14 01 00 NN`) |
| screen → host | `5A A5 06 83 VPh VPl 01 vh vl` | touch: key/value |

The screen switches pages by itself where the touch file defines a target page; everything else is up to the host.
Keys that only switch pages report `VP 0x0000`. Increment arrows change the number locally and report the new value.

## Main variables

| VP | Meaning |
|---|---|
| 0x1034 / 0x1036 | hotend target / actual |
| 0x103A / 0x103C | bed target / actual |
| 0x1048 / 0x104A / 0x104C | X / Y / Z ×10 |
| 0x1006 / 0x1300 / 0x101E | speed % / flow % / fan % |
| 0x1026 | Z offset ×100 (signed) |
| 0x1016, 0x100E/0x100F | progress %, progress bar icons (2 halves) |
| 0x1010 / 0x1012 | print time hours / minutes |
| 0x2000 | file name (20 chars) |
| 0x2064 | status line (40 chars) |
| 0x1060 | machine name / error text on fault page 88 |
| 0x200A + 0x14·n | file list rows (4 per page), 0x1201..4 row icons, 0x6003 + 16·row text colour |
| 0x1100 + 2·k | 5×5 mesh, serpentine from front-left, value ×1000 (int16) |
| 0x0080 / 0x0082 / 0x00A1 | system config / brightness+standby / volume |

## Keys

| VP | Values |
|---|---|
| 0x1002 | 1 Print (files), 2 Finish, 3 Temperature, 4 Settings |
| 0x1004 | Adjust: 1 open, 2 back, 3 fan toggle |
| 0x1008 / 0x100A / 0x100C | Stop (240 = no) / Pause (0xF1 = yes) / Resume |
| 0x1030 | temperature page: 1 presets, 3 fan, 5/6/7 presets, 0xF1 cool down |
| 0x1032 | 0 back, 1 hotend off, 2 bed off |
| 0x103E | Settings: 1 leveling, 2 filament, 3 move, 4 tools, 5 info, 6 motors off, 10 E-steps, 11 PID |
| 0x1040 | back (1 → settings, 2 → leveling) |
| 0x1044 | leveling/tramming: 1 home+Z0, 2/3 Z ±0.1, 4 tramming, 5 measure, 6–10 points, 11 mesh on/off, 17–22 screen settings |
| 0x1046 | move page: 0/1/2 step 10/1/0.1 mm, 3 home |
| 0x1056 | filament: 1/2 unload/load, 5 heat, 6 / 0xF1 cancel |
| 0x105C | tools: 1 screen config, 2 hotend PID, 4 BLTouch reset, 5 bed PID, 6 STORE |
| 0x1088 | volume (steps of 32) |
| 0x20D3 / 0x20D2 | file row 1–4 / 0 back, 1 print, 2 page down, 3 page up, 4 refresh |

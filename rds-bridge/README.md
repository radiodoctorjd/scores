# RDS Bridge

Takes the **now-playing XML from WideOrbit** (WO Automation for Radio), parses it, and sends song text to
**Audemat / WorldCast FMB80** and **Inovonics 730** RDS encoders. One running copy handles any number of
stations, and each station can feed any number of encoders.

- **Inputs:** a file WideOrbit writes, or XML over UDP, TCP or HTTP POST. Several stations can share one input.
- **Outputs:** ASCII commands or UECP (EBU SPB 490), over TCP, UDP or RS-232 serial.
- **Debug screen:** a web page that shows each XML document received, the fields parsed from it, every command
  or byte sequence sent to each encoder, and the encoder's reply.
- **Customization:** a web editor for the text templates, default/idle text, music filter and encoder
  commands. It has a live preview, a "test with sample XML" box and a manual override. Changes apply without a restart.
- Runs on Windows as a standalone `RDSBridge.exe` (no Python needed), or from source with Python 3.8+
  (standard library only; `pyserial` is optional, for serial encoders).

## Running it on Windows

Everything for this program is in this one folder (`rds-bridge`).

### Option 1: the ready-made Windows program (no Python needed)

1. On GitHub, open **Actions → "RDS Bridge - Windows build"**, click the newest green run and download
   **RDSBridge-windows** under *Artifacts*. You get a zip file.
2. Unzip it somewhere permanent, e.g. `C:\RDSBridge`.
3. Double-click **`Start RDS Bridge.bat`**. On the first run it creates `config.json` from the example,
   with every encoder output **disabled**, and opens the web screen in your browser.
4. On the **Settings** tab, enter your stations, the WideOrbit file path or port, and the encoder IP
   addresses and ports. Set the outputs to `"enabled": true`, then click **Save & apply**.
   (In JSON, Windows paths need double backslashes, e.g. `"C:\\WO\\NowPlaying.xml"`, or use forward slashes.)
5. If Windows Firewall asks, allow RDS Bridge on your **private/domain** network so WideOrbit can reach it.

The downloaded folder looks like this:

```
RDSBridge\
  RDSBridge.exe              the program
  _internal\                 files the program needs (leave alone)
  Start RDS Bridge.bat       start it in a window and open the web screen
  Install autostart.bat      start it automatically with Windows (runs in the background)
  Remove autostart.bat       undo that
  config.example.json        example settings (your settings go in config.json)
  samples\                   example WideOrbit XML files
  tools\                     scripts used by the autostart .bat files
```

**Start automatically with Windows:** double-click **`Install autostart.bat`** and approve the admin prompt.
It creates a Windows scheduled task named "RDS Bridge". The task:
- starts the program at boot, before anyone logs in
- restarts it within a minute if it ever stops
- adds a firewall rule so WideOrbit can reach it

It asks which account to run as. Press Enter for SYSTEM, or enter a Windows account if WideOrbit writes
its XML to a **network share**, because SYSTEM usually can't read shares. While the background copy is
running, use the web screen (http://localhost:8080/) rather than also starting `Start RDS Bridge.bat`.
Remove it with **`Remove autostart.bat`**.

Logs go to `rdsbridge.log` next to `config.json`. The file rotates at 5 MB and keeps 5 old copies.

**Building the .exe yourself:** on a Windows PC with Python 3 installed, double-click
**`Build Windows exe.bat`**. The program appears in `dist\RDSBridge` (plus `dist\RDSBridge-windows.zip`).
The GitHub build does the same on a Windows machine, and also runs all tests and a smoke test of the
finished `.exe`. To publish a permanent download on the GitHub Releases page, push a tag such as
`rds-bridge-v1.0.0`.

### Option 2: run from source with Python

1. Install Python 3.8 or newer from python.org (tick "Add python.exe to PATH").
2. Download this `rds-bridge` folder and double-click **`Start RDS Bridge.bat`**. When there is no `.exe`
   in the folder it runs the Python source instead. The first run works the same as in Option 1.
   `Install autostart.bat` also works with the Python source.

From a command prompt in the folder you can also check the config, or see what a sample file would send
without sending anything:

```bash
python -m rdsbridge --check
```
```bash
python -m rdsbridge --test-xml samples\wideorbit_nowplaying.xml
```

`config.json` is always looked up next to the program (or pass `--config path\to\file.json`).

## Setting up WideOrbit

In WO Automation for Radio, add an **Event Driver** with export type **XML**, trigger **Start Play**, and route it
to one of these outputs:

| WideOrbit sends to | Config `input` |
|---|---|
| A file (local or network share) | `{"type": "file", "path": "C:\\WO\\NowPlaying\\WAAA.xml"}` |
| UDP to this PC | `{"type": "udp", "port": 5000}` |
| TCP to this PC | `{"type": "tcp", "port": 5001}` |
| HTTP POST to this PC | `{"type": "http", "port": 8081, "path": "/nowplaying"}` |

If you export **all media types**, use the station's `filter` so spots and liners send the default text
instead of an ad title. The **Parsed** rows on the debug screen show which values WideOrbit sends for
`media_type` and `category`.

If several stations send to the same UDP, TCP or HTTP port, give each station a `match` rule such as
`{"station": "WAAA*"}` so it only takes its own events.

### What the parser accepts

The XML layout is not hard-coded. Every element and attribute becomes a template field (lower case),
so whatever WideOrbit sends can be used, e.g. `{title}`, `{artist}`, `{album}`, `{cart}`, `{category}`,
`{media_type}`, `{duration}`. A few extra fields are added for you:

- `{title}`, `{artist}`, `{album}`, `{cart}` etc. are also filled from common alternative names
  (`song`, `performer`, `cut`...). Add your own with `"field_map": {"title": ["songname"]}`.
- `{duration_seconds}` is parsed from `3:45`, `00:03:45` or milliseconds.
- If one document holds several events (current and upcoming), the current one is used and the next
  one's fields are available as `{next_title}`, `{next_artist}` and so on.
- The parser tolerates a UTF-8 BOM, Windows-1252 characters, bare `&` characters and control characters.

## Customizing what is sent

Each station builds named **text fields**, usually `rt` (RadioText, 64 characters) and `dps` (dynamic or
scrolling PS), plus `ps` if you want it. Each field has:

- **templates**: tried in order. The first one whose fields all have values is used:
  ```json
  "templates": {
    "rt":  ["Now playing {title} by {artist} on WAAA", "{title} on WAAA"],
    "dps": ["{title} - {artist}", "{title}"]
  }
  ```
  Modifiers: `{title!u}` UPPER, `{title!l}` lower, `{title!t}` Title Case, `{title:.30}` first 30 characters.
- **default**: sent when the event is not music, or after the song ends if `revert_after_duration` is on.

Each **output** (encoder) then turns those fields into commands. Its `commands` list is fully editable:

```json
{ "name": "WAAA FMB80", "device": "fmb80", "host": "192.168.10.50", "port": 5401,
  "commands": ["RT1={rt}", "DPS1={dps}"] }
```

An output can also override `templates`, `default`, `max_lengths` (text is cut at a word boundary) and
`uppercase` (e.g. `["dps"]`). By default text is converted to plain ASCII, so accents and curly quotes
are safe on any receiver. Set `"ascii_only": false` to keep them.

Everything above can be edited on the **Customize** tab with a live preview. The **Settings** tab edits
the whole `config.json`. Saving validates the config, keeps the old file as `config.json.bak` and
reloads without a restart.

## Encoder notes

> **Check the command syntax against your encoder's manual and firmware.** The built-in device
> presets are starting points and are fully editable per output.

| | Audemat FMB80 (`"device": "fmb80"`) | Inovonics 730 (`"device": "inovonics730"`) |
|---|---|---|
| Default commands | `RT1={rt}`, `DPS1={dps}` | `DPS={dps}`, `TEXT={rt}` |
| Line ending | CR LF (`"terminator": "\r\n"`) | CR (`"terminator": "\r"`) |
| Port | Set in config (the port must be set to the ASCII protocol on the FMB80, e.g. `udp1.protocol=ascii`) | Set in config (see the 730's network setup) |

Both encoders also support **UECP**. Set `"protocol": "uecp"` on the output (and set that port on the
encoder to UECP) to send standard RadioText (MEC 0x0A) and PS (MEC 0x02) frames. UECP options
(`site`, `encoder`, `dsn`, `psn`, `rt_field`, `ps_field`) go under `"uecp": {...}`.

Other output settings:

| Setting | Default | Meaning |
|---|---|---|
| `transport` | `tcp` | `tcp`, `udp` or `serial` (`serial_port`, `baudrate`) |
| `persistent` | `true` | Keep the TCP connection open (`false` connects for each update) |
| `init_commands` | `[]` | Sent after each connect, e.g. a password |
| `command_delay` | `0.1` | Seconds between commands |
| `read_response` | on for TCP/serial | Show the encoder's reply on the debug screen |
| `skip_empty` | `true` | Don't send a command if a field it uses is empty |
| `encoding` | `latin-1` | Character encoding of the bytes sent |

If an encoder is offline, its output keeps retrying (1 s up to 30 s apart) and sends only the **latest**
text when it comes back. Other encoders are not affected.

## Station settings

| Setting | Default | Meaning |
|---|---|---|
| `match` | `{}` | Only accept events whose fields match, e.g. `{"station": "WAAA*"}` |
| `filter` | `null` | `{"field": "media_type", "include": ["SONG"], "exclude": []}`; `*` wildcards allowed |
| `require_fields` | `["title"]` | Events missing these are treated as not music |
| `non_music_action` | `default` | `default` sends the default text; `ignore` keeps the last song |
| `revert_after_duration` | `false` | Send the default text when the song's duration has elapsed |
| `revert_grace` | `5` | Extra seconds before reverting |
| `refresh_interval` | `0` | Resend the current text every N seconds (useful after an encoder reboot) |
| `event_path` | auto | ElementTree path to the event elements, if auto-detection picks the wrong ones |
| `event_select` | `first` | Which event in a multi-event document is "now playing": `first`, `last` or an index |

## Web screen

- **Monitor**: each station's current song, mode (song, default or override), elapsed time, next song,
  and each encoder's status, last commands and reply. **Resend** pushes the current text again.
  **Manual text…** sends your own RT/DPS/PS until the next song, or holds it.
  Below that is the traffic log. Filter it by station, type and search text, and click a row to see the
  raw XML, every parsed field, or the exact bytes sent (UECP is shown as hex).
- **Customize**: templates, default text, music filter and encoder commands, with a live preview.
  Click a field chip to insert it. You can paste sample XML to preview it or send it to the encoders.
- **Settings**: the full config as JSON.

By default the screen is only reachable from the same PC (`"bind": "127.0.0.1"`). To open it to the
network, set `"bind": "0.0.0.0"` and set a `username` and `password` under `web`.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests cover XML parsing edge cases, templates, UECP framing and CRC, and full end-to-end runs (UDP,
file and HTTP inputs, shared inputs, overrides, live reload, an offline encoder coming back) against
fake TCP encoders, plus the web API.

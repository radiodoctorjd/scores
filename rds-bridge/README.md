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
- Only the Python standard library is needed (Python 3.8 or newer). `pyserial` is optional, for serial encoders.

## Quick start (Windows)

1. Install Python 3 from python.org (tick "Add Python to PATH").
2. Copy `config.example.json` to `config.json` and edit the station names, the WideOrbit file path or port,
   and the encoder IP addresses and ports.
3. Check the config, then try it on a sample file without sending anything:
   ```bash
   python -m rdsbridge --config config.json --check
   ```
   ```bash
   python -m rdsbridge --config config.json --test-xml samples\wideorbit_nowplaying.xml
   ```
4. Run it (or double-click `run.bat`):
   ```bash
   python -m rdsbridge --config config.json
   ```
5. Open **http://localhost:8080/** for the debug and customization screen.

To run it unattended, install it as a Windows service with [NSSM](https://nssm.cc/)
(`nssm install RDSBridge`, program `python.exe`, arguments `-m rdsbridge --config config.json`,
startup directory this folder), or start it from Task Scheduler at boot.

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

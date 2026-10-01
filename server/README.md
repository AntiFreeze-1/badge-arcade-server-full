# Badge Arcade personal server

A self-contained server for **Nintendo Badge Arcade** (3DS), for personal use.
It runs as a single Python process with no external databases: the NEX
servers, save storage and SpotPass file hosting are all included.

It is built on [kinnay's NintendoClients](https://github.com/kinnay/NintendoClients),
and its protocol behaviour is ported from Pretendo Network's Go servers
([badge-arcade-authentication](https://github.com/PretendoNetwork/badge-arcade-authentication),
[badge-arcade-secure](https://github.com/PretendoNetwork/badge-arcade-secure)).

Setting up and running it day to day is easiest with the manager window; see
the [main README](../README.md). This file covers the server itself.

## What it runs

| Component | Port | Purpose |
|---|---|---|
| NEX authentication | UDP 59400 | Kerberos login; hands out tickets for the secure server |
| NEX secure | UDP 59401 | SecureConnection (+ `GetMaintenanceStatus`), DataStore (+ `GetMetaByOwnerId`), Shop (`PostPlayLog`, `GetRivToken`) |
| HTTP | TCP 8080 | NASC login (`/ac`), save-data upload/download (S3-style), SpotPass `.boss` files |

Saves are kept in `data/` (SQLite + one file per save version). The server creates
`data/badge_arcade.db` and `data/objects/` on its first start, so neither is in the
repository. The server keeps the previous version of each save as a backup.

Game flow, as implemented:

1. The 3DS asks NASC (`nasc.nintendowifi.net/ac`) for the game server. The
   mitmproxy addon sends that request here, and the reply points at the local
   auth server.
2. The 3DS logs in to the auth server with its PID and NEX password, then
   connects to the secure server.
3. DataStore: `GetPersistenceInfo` → first run: `PostMetaBinary` (FreePlayData,
   data type 100) + `PreparePostObject` + HTTP upload + `CompletePostObject`.
   Later runs: `PrepareGetObject` + HTTP download, `ChangeMeta`,
   `GetMetaByOwnerId`, `PrepareUpdateObject` + upload + `CompleteUpdateObject`.

## Requirements

- Python **3.12+**
- A 3DS with custom firmware (Luma3DS)
- mitmproxy, which `mitm/setup_mitm.py` installs and configures using
  Pretendo's [mitmproxy-nintendo](https://github.com/PretendoNetwork/mitmproxy-nintendo)
  3DS setup (see [Point the 3DS at it](#point-the-3ds-at-it))

## Install

`python install.py` in the project folder (or `Setup.bat`) installs everything,
creates `config.json` and sets up the proxy. To install the packages by hand:

Linux/macOS:

```sh
pip install -r requirements.txt
```

Windows: `netifaces` (pulled in by `anynet`) has no prebuilt wheel and needs a C
compiler. The server doesn't use it, so install around it:

```sh
pip install "pycryptodome>=3.20,<4" "anyio~=4.0" "pyopenssl>=24.0" "multidict>=6.0" "pillow>=10.0" netifaces-plus
pip install --no-deps "anynet~=1.2" "nintendoclients==5.0.0"
```

## Configure

`install.py` creates `config.json` from `config.example.json`, with a random
`kerberos_password`. The defaults work as they are.

| Key | Meaning |
|---|---|
| `public_host` | The address the 3DS reaches this PC on. `auto` (the default) gives each 3DS the PC's address on its network when it logs in (the hotspot's address for a 3DS on the hotspot, the LAN address otherwise), so the hotspot can start after the server and the PC can change networks without a restart. An address here is always used as it is |
| `kerberos_password` | Any random string; used internally between the auth and secure servers |
| `boss_dir` | Folder with SpotPass files (defaults to `../other`) |
| `boss_key_file` | `boot9.bin` (or the SpotPass key), used to make the USA SpotPass files work on an EUR Badge Arcade (defaults to `../spotpass-letter/boot9.bin`) |
| `nex_keys_file` | `nex-keys.txt` dumped from the console, next to the config by default (see below) |
| `accounts` | Alternative to `nex_keys_file`: `{"<PID>": "<NEX password>"}` |
| `default_nex_password` | Fallback password for PIDs not listed |
| `maintenance` | Values returned by `GetMaintenanceStatus` (defaults match Pretendo's) |
| `maintenance_file` | Where the manager's Maintenance tab keeps its state (`maintenance.json` next to the config). While maintenance is on, logins get `RendezVous::GameServerMaintenance` and the 3DS shows the maintenance error; the file is read again when it changes, so no restart is needed |
| `game_date` | Date (`YYYY-MM-DD`) the server tells the game at login. `null` (the default) = the current date. Served SpotPass weeks are moved to this date (see below) |
| `nex_settings` | Raw NintendoClients setting overrides, e.g. `{"prudp.resend_limit": 8}` |

### The console's NEX password

Usually you don't need this. Badge Arcade asks the Nintendo Network ID
account server (`account.nintendo.net/.../nex_token`) where its game server is
and which NEX password to use, and the proxy answers that from this server,
which picks the password itself. The rest of this section is for logins that
go through NASC instead, where the log says `No NEX password known`.

NEX logins use Kerberos: the server encrypts the login ticket with a key
derived from the console's NEX password, so **the server must know that
password** or the console can't decrypt the ticket.

A console doesn't send its password when it logs in to a game: NASC game
logins only carry the PID (`userid` + `uidhmac`). The password is only sent
once, when the friends system first registers an account (this is how
Pretendo's NASC server behaves). So you need to read it from the console once:

1. Copy [get_3ds_pid_password.3dsx](https://9net.org/~stary/get_3ds_pid_password.3dsx)
   to the `3ds` folder on the SD card and run it from the Homebrew Launcher
   ([source](https://github.com/Stary2001/nex-dissector/tree/master/get_3ds_pid_password);
   Pretendo's **Get_PID_Passwrd** does the same). It writes `nex-keys.txt` to
   the root of the SD card, containing your NEX PID and NEX password (not a
   Nintendo Network ID password).
2. Copy `nex-keys.txt` next to `config.json`. The server reads it
   automatically, even while running, and logs the PIDs it found. A file that
   the NEX Wireshark dissector has rewritten (password replaced by a key) works
   too.

Instead of the file, you can also put the PID and password in `config.json` as
`"accounts": {"<PID>": "<password>"}`. Keep `nex-keys.txt` and `config.json`
private (both are git-ignored).

The dump reads whichever friends account is active: Nintendo's, or Pretendo's
if you use Nimbus. That's the same account Badge Arcade logs in with.

If no password is known, the log says `No NEX password known for PID <pid>`,
which also tells you your PID. (If a console ever does send `passwd` to NASC,
the server stores it automatically, but don't rely on that.)

## Run

```sh
python -m badge_arcade config.json        # add -v for packet-level logs
python -m badge_arcade config.json --public-host 192.168.137.1   # hotspot mode
```

`--public-host` sets the address for this run when the 3DS's own address isn't known
(with `public_host` set to `auto`, each 3DS otherwise gets the address it can reach).

On Windows, allow Python through the firewall for inbound **UDP 59400–59401**
and **TCP 8080**, plus **TCP 8083** for the proxy (Windows usually asks the
first time each one starts). Turning on hotspot mode adds these rules.

## Point the 3DS at it

The console reaches the server through mitmproxy, either as its proxy or,
in [hotspot mode](#hotspot-mode), without any settings on the 3DS. Either way
it needs the NoSSL patch (see [One-time setup](#one-time-setup)). The proxy uses Pretendo's
[mitmproxy-nintendo](https://github.com/PretendoNetwork/mitmproxy-nintendo)
3DS configuration plus `mitm/badge_arcade_redirect.py`, which:

- sends Badge Arcade's NASC login (`nasc.nintendowifi.net`, or
  `nasc.pretendo.cc` with Nimbus) to this server. Logins from the friends
  system and other games pass through untouched.
- serves SpotPass files (`npdl.cdn.nintendowifi.net`, or `npdl.cdn.pretendo.cc`
  with Nimbus) that exist in `boss_dir`. Badge Arcade's other SpotPass files
  come from Nintendo's CDN, which still has them, also with Nimbus (Pretendo's
  CDN doesn't serve them: error 004-3003). Other titles' files pass through.

The NEX traffic is UDP and goes straight to the server. Save uploads and
downloads use plain HTTP URLs that the server hands out.

### One-time setup

```sh
python mitm/setup_mitm.py
```

This creates `mitm/.venv` with mitmproxy, downloads Pretendo's
mitmproxy-nintendo into `mitm/mitmproxy-nintendo/` (for the 3DS client
certificate and NoSSL patch), and puts the patch in `mitm/sd-card/`.

On the 3DS:

1. Copy the `luma` folder from `mitm/sd-card/` to the root of the SD card.
   It holds `luma/sysmodules/0004013000002F02.ips`, Pretendo's NoSSL patch,
   which makes the console accept mitmproxy's certificates.
2. In Luma3DS's configuration menu (hold SELECT while powering on), make sure
   **Enable game patching** is on.

### Each session

The manager window does steps 1 and 2. By hand:

1. Start the server: `python -m badge_arcade config.json`
2. Start the proxy in a second terminal: `python mitm/start_mitm.py`
   (`--web` for mitmproxy's web interface, `--dump` to save a HAR network dump
   in `mitm/dumps/`). It prints the address to use on the console.
3. On the 3DS: *System Settings → Internet Settings → Connection Settings →
   your connection → Change Settings → Proxy Settings → Yes → Detailed Setup*:
   the PC's IP address and port **8083**.
4. Launch Badge Arcade and watch both logs.

When you're done, turn the proxy off on the console. The NoSSL patch makes the
3DS accept any certificate, so delete it from `luma/sysmodules/` when you're
not using the server.

**Region-changed consoles:** if Badge Arcade says *"This service is not
available in your region"* straight away, without going online, the app has
rejected the console's country setting (for example a Japanese 3DS running USA
firmware that still has Japan as its country). Either set the country in
*System Settings → Other Settings → Profile → Region Settings*, or have Luma
emulate it for Badge Arcade only: `mitm/sd-card/luma/titles/0004000000153500/locale.txt`
contains `USA EN US` (region, language, country) and goes to the same place on
the SD card. It needs **Enable game patching**, like the NoSSL patch.

### Hotspot mode

The 3DS joins the PC's Windows Mobile Hotspot and has no proxy set:

```sh
python mitm/hotspot.py on        # UAC prompt: hosts file + firewall; starts the hotspot
python -m badge_arcade config.json --public-host 192.168.137.1
python mitm/start_mitm.py --hotspot 192.168.137.1
python mitm/hotspot.py status    # name, password, clients, and a DNS check
python mitm/hotspot.py off       # stops the hotspot and removes the hosts entries
```

- Windows' hotspot (Internet Connection Sharing) gives its devices the PC as
  their DNS server, and resolves names with the PC's own resolver, which reads
  the hosts file. `hotspot.py on` adds a marked block that points the hosts in
  `mitm/nintendo_hosts.py` (NASC, the account server, SpotPass policy lists and
  downloads, for Nintendo and Pretendo) at the hotspot address, 192.168.137.1
  unless ICS is set up differently. Other names resolve normally, so the rest
  of the 3DS's traffic doesn't touch the PC's proxy.
- `start_mitm.py --hotspot` adds two reverse-proxy listeners on that address,
  on ports 443 and 80, next to the usual proxy port. The addon routes their
  requests by the Host header, exactly like proxied ones. When it passes a
  request through to the real server, it looks the name up with the system's
  DNS servers directly (not the hosts file, which would point it back at
  itself).
- The server has to give the 3DS the hotspot address (`--public-host`) for the
  NEX servers and save downloads.
- The status command checks the setup by asking the hotspot's DNS for
  `nasc.nintendowifi.net`, as the 3DS would.

The hotspot shares the PC's internet connection, and the 3DS only sees 2.4 GHz
networks. `hotspot.py` sets the hotspot's band to 2.4 GHz where Windows allows
it, and warns when the PC's own Wi-Fi is on 5 GHz.

### Other title IDs

If the addon should also handle another title ID (for example the Japanese
version), add it with `python mitm/start_mitm.py -- --set badge_title_ids=0004000000153500,0004000000153600,<id>`.
The proxy log prints the title ID of every NASC login it passes through.

### Why a pinned mitmproxy

The 3DS only supports old TLS versions. `requirements-mitm.txt` pins
`cryptography` 46 (OpenSSL 3.5), because newer releases bundle OpenSSL 4, which
makes mitmproxy 12 crash when old TLS versions are enabled. With this setup,
a TLS 1.0 client using the 3DS's `AES128-SHA` cipher completes the handshake
and gets redirected.

If a console still can't connect, Pretendo's Docker image, which is built
against OpenSSL 1.1.1, is an alternative (untested):

```sh
docker run -it --rm -p 8083:8083 -v "<full path to server>/mitm:/home/mitmproxy/badge" ghcr.io/pretendonetwork/mitmproxy-nintendo:3ds mitmdump -s /home/mitmproxy/badge/badge_arcade_redirect.py --set badge_server=host.docker.internal:8080 --set connection_strategy=lazy
```

(On Linux, add `--add-host=host.docker.internal:host-gateway`.)

## Managing saves

```sh
python -m badge_arcade.admin saves          # list stored saves per PID
python -m badge_arcade.admin stats          # days played, streaks and play reports per PID
python -m badge_arcade.admin backup         # zip the database and save files into backups/
python -m badge_arcade.admin reset <PID>    # back up, then forget that player's saves
```

These use `config.json` by default (`--config` for another file) and are safe
to run while the server is running.

`reset` is for when a save gets stuck, for example if the very first setup was
interrupted after the save was created but before its data was uploaded. The
game then sets up a new save on its next connection, as on first launch.
Reset while the game isn't connected. A backup is always made first.

To restore a backup, stop the server, move the `data` folder aside and extract
the zip in its place.

The server also keeps the previous version of each save file in
`data/objects/`.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest tests
cd ../spotpass-letter && python -m pytest tests
```

GitHub Actions runs these on Windows and Linux for every push and pull
request, along with `ruff check .` from the project folder.

`tests/test_end_to_end.py` starts the whole server on localhost and uses
NintendoClients as a stand-in 3DS over both PRUDP v1 and v0. It covers NASC,
Kerberos login, secure registration, the maintenance check, the full DataStore
save/load/update cycle with real HTTP uploads and downloads (byte-for-byte),
the Shop methods, and SpotPass file serving and revalidation. It also covers
`nex-keys.txt` (added while the server is running, including negative PIDs
and derived keys), rejecting unknown accounts, and the save tools, including
a fresh first-time setup after `reset`. `tests/test_hotspot.py` covers the
hotspot mode's hosts-file editing and DNS check, and `--public-host`.
`tests/test_units.py` covers the save tools' FreePlayData helpers, cleaning up
unfinished uploads and the HTTP server's request size limit,
`tests/test_update.py` covers `update.py` (without the network), and
`tests/test_manager.py` covers how the manager reads the 3DS's PID and NNID
from the proxy's log.
`spotpass-letter/tests/` covers the SpotPass container, free plays and SARC
code with throwaway keys and made-up data.

The proxy addon has an offline self-test (it needs mitmproxy, so it runs with
the proxy's Python):

```sh
mitm/.venv/Scripts/python mitm/selftest_addon.py    # Windows
mitm/.venv/bin/python mitm/selftest_addon.py        # Linux/macOS
```

## Status and known unknowns

Everything above passes the local end-to-end tests and has been used with a
real 3DS through the proxy (login, saves, SpotPass weeks). Hotspot mode has
been tested on Windows 10 with simulated 3DS requests. Protocol details come from Pretendo's Go
servers (NEX 3.7.16, PRUDP v1, access key `82d5962d`). Things most likely to
need adjusting on real hardware:

- **SpotPass**: files are matched by file name, which fits the URLs documented
  on 3dbrew (`p01/nsa/<region code>/FGONLYT/playinfo_v131.dat`,
  `.../data/data_v131.dat`, `.../data/allbadge_v131.dat`). The
  `news/<language>/news_v131.dat` file isn't in `other/`, so those requests
  pass through. The files have to match the console's region (the log shows
  which region the console asks for), and the game needs the 1.3.1 update to
  request the `v131` files. Downloads carry ETag/Last-Modified headers, so the
  console can check for changes without downloading the files again. The news
  task is fine without a file: it had none before the shutdown either.
- **The game date decides which machines show**: the server sends
  `game_date` to Badge Arcade at login (the 3DS clock doesn't matter), and the
  game only uses SpotPass content whose dates include it. With the real date,
  every archived week is over and the game falls back to its built-in default
  machines, so `spotpass-letter/serve.py` (and the manager) move a week's
  schedule to the game date when serving it. Free-play campaigns are made for
  the game date too. Restart the server after changing `game_date`.
- **Switching weeks**: the console only downloads a weekly file whose NsData
  ID it doesn't have yet (the serial at offset 12 is not what decides this),
  so a week the console has seen before needs a new ID
  (`spotpass-letter/repack.py`). Use a `playinfo` and a `data` file from the
  same week.
- **Buying plays can't work**: purchases went through the 3DS eShop, which
  stopped selling in 2023. `GetRivToken` is stubbed as in Pretendo's server,
  so only free plays are available.
- **FreePlayData / play-info layout**: like Pretendo, both share one data ID
  in persistence slot 0. If the game expects them separate, the relevant logic
  is in `badge_arcade/protocols/datastore.py`.
- **`GetMaintenanceStatus`**: the meaning of the response fields is a guess
  carried over from Pretendo.

Run with `-v` when trying it on a console. Each DataStore call is logged
with its parameters, which makes mismatches easy to spot.

## Layout

```
badge_arcade/
  __main__.py          entry point (python -m badge_arcade)
  admin.py             save tools and play stats (python -m badge_arcade.admin)
  server.py            wires the servers together
  config.py            config file loading
  storage.py           SQLite + file storage
  nex_keys.py          nex-keys.txt reader
  nex_common.py        NEX settings, Kerberos keys and tickets
  http_server.py       NASC, save upload/download, SpotPass
  protocols/
    auth.py            authentication server
    secure.py          SecureConnection + GetMaintenanceStatus
    datastore.py       DataStore + GetMetaByOwnerId
    shop.py            Shop (Badge Arcade)
mitm/
  setup_mitm.py        one-time proxy setup (venv, Pretendo files, SD card folder)
  start_mitm.py        starts mitmproxy with Pretendo's 3DS settings + the addon
  badge_arcade_redirect.py   mitmproxy addon
  nintendo_hosts.py    the hosts the addon handles (shared with hotspot.py)
  hotspot.py           hotspot mode: Windows hotspot, hosts file, firewall
  requirements-mitm.txt      pinned mitmproxy versions
  selftest_addon.py    offline addon test
tests/
  test_end_to_end.py   the whole server against a stand-in 3DS
  test_hotspot.py      hotspot mode helpers
```

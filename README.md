# Nintendo Badge Arcade server

A self-hosted server for **Nintendo Badge Arcade** on the 3DS, for use
after Nintendo's shutdown. With it, the game connects to the ad-hoc server, saves your
progress, loads weekly machines from SpotPass data, and can hand out
free plays. You can also make your own badges and claw machines and put them in
a week. A manager window takes care of setup and the day-to-day.

> **No Nintendo files are included.** SpotPass data, console dumps, the game's
> code and keys are copyrighted or console-specific, so you provide your own
> (see [What you need to provide](#what-you-need-to-provide)). 
> Please don't commit them. We don't want to be taken down. 😊

## Quick start

1. Install [Python 3.12 or newer](https://www.python.org/downloads/).
2. Double-click **Setup.bat** (Windows) or run **python install.py** (MacOS/Linux). 
    It installs the Python packages, creates
   `server/config.json` and sets up the proxy (about 100 MB of downloads).
4. Add the files from [What you need to provide](#what-you-need-to-provide).
5. Copy the `luma` folder from `server/mitm/sd-card` to the root of the 3DS's
   SD card, and make sure **Enable game patching** is on in Luma3DS's settings
   (hold SELECT while powering on). This will enable the NoSSL patch, which will
   allow the 3DS to connect to the server.
6. Double-click **Badge Arcade Manager.bat** (Windows) or **python -m badge_arcade config.json** (MacOS/Linux). 
   The **Setup** tab shows what's
   still missing, and how to connect the 3DS.
7. Connect the 3DS (below), then open Badge Arcade.

For MacOS/Linux further usage, (see [server/README.md](server/README.md)).

## Connecting the 3DS

Pick an option from the manager's **Setup** tab.

### Through the PC's hotspot (recommended)

The 3DS will join a Wi-Fi hotspot run by your PC, and needs **no proxy settings**.
Press **Turn on hotspot**: Windows asks for admin rights once, the hotspot
starts, and the server and proxy start with it. Then, on the 3DS:

On the 3DS, navigate to *System Settings → Internet Settings → Connection Settings → New Connection →
Manual Setup → Search for an Access Point*, pick the network name shown in the
manager, enter the password, and connect. Run the
connection test and open Badge Arcade.

How it works: Windows' Mobile Hotspot gives its devices the PC as their DNS
server, and answers from the PC's hosts file. Turning the hotspot on adds a
marked block to the hosts file that points only the Nintendo servers Badge
Arcade needs (login, account, SpotPass) at the PC, and adds firewall rules for
the server's ports. Everything else the 3DS does goes straight to the internet,
and anything that isn't Badge Arcade (the friends list, the eShop) is passed on
to the real servers. **Turn off hotspot** removes the block again (closing the
manager offers to do the same). The same thing works from the command line:
`python server/mitm/hotspot.py on|off|status`.

It needs a PC with Wi-Fi and an internet connection to share. The 3DS only
sees 2.4 GHz networks: the manager sets the hotspot to 2.4 GHz where Windows
allows it, and warns if it can't.

### Through a proxy

The 3DS stays on your usual Wi-Fi and uses the PC as its proxy. On the 3DS:
*System Settings → Internet Settings → Connection Settings → your connection →
Change Settings → Proxy Settings → Yes → Detailed Setup*, and enter the proxy
server and port shown in the manager. Turn the proxy off on the 3DS when you're
done playing.

## What you need to provide

| What | Where it goes | Needed for | How to get it |
|---|---|---|---|
| A 3DS with Luma3DS custom firmware and **Nintendo Badge Arcade 1.3.1** installed | – | Everything | Your own console and copy of the game. |
| SpotPass files | `other/` | Machines and free plays | The archived Badge Arcade SpotPass data (archive.org's [*Nintendo Badge Arcade Data*](https://ia800600.us.archive.org/view_archive.php?archive=/32/items/3ds-boss-data-4/OvbmGLZ9senvgV3K.zip) item for the .boss files, or their [*Updated Data Dumps*](https://archive.org/download/nintendo-badge-arcade-data-updated) for the .enc files), or your own dumps. See the file list below. |
| `boot9.bin` | `spotpass-letter/` | Switching weeks, custom weeks, free plays, letters | GodMode9: `[M:] MEMORY VIRTUAL` → `boot9.bin` → copy to `0:/gm9/out`. See [spotpass-letter/README.md](spotpass-letter/README.md#getting-the-key-from-your-own-console). |
| Your console's NEX password (`nex-keys.txt`) | `server/` | Usually nothing | Only if the server's log says *No NEX password known*: see [server/README.md](server/README.md#the-consoles-nex-password). |

The proxy's 3DS certificate and the NoSSL patch are downloaded from Pretendo
Network by Setup.bat.

You don't provide `server/data/badge_arcade.db`: the server creates it the first
time it starts, and it holds your saves. It isn't in the repository because it's
different for every player.

### SpotPass files in `other/`

The server sends whatever is in `other/` under the names the 3DS asks for.
The tools expect these names (US region). They work for an EUR copy of the game
too: every SpotPass file names the game it's for, so the server rewrites that to
the EUR Badge Arcade as it sends them. It needs `boot9.bin` in `spotpass-letter/`
for that.

| File | What it is |
|---|---|
| `data_v131-2022-12-29-09-40-NA.enc` | A weekly machine set (the Dec 29, 2022 week). The tools use it as the base for custom weeks. |
| `playinfo_v131-2022-12-29-09-40-NA.enc` | The same week's play settings. The base for free plays. |
| `allbadge_v131.dat.boss` | Every badge's graphics. The game needs it; custom weeks take machines from it. |
| other `data_v131-*.enc` weeks | Optional: more weeks to switch between. |

Once they're in place, serve a week and give free plays in the manager's
**Serve a week** and **Free plays** tabs (Windows), or from the command line (MacOS/Linux):

```sh
cd spotpass-letter
python serve.py week dec29
python serve.py free-plays --plays 2
```

This creates `data_v131.dat.boss` and `playinfo_v131.dat.boss`, which the
server sends. Serving a week moves its schedule to the current date 
(or to a fixed `"game_date"` in `server/config.json`, if you set one). 
The manager keeps the served week on the current date as days pass.

## The manager

| Tab | What it does |
|---|---|
| **Setup** | The checklist of what's installed and provided, where your own badges' files are, connecting the 3DS (hotspot or proxy), and updates. |
| **Server** | Start and stop the server and proxy, see whether the proxy has seen the 3DS's PID and NNID (until the server has working ones from an earlier session), set the game date, see what the 3DS gets next, and watch logins, SpotPass downloads and saves as they happen. |
| **Serve a week** | Every archived Nintendo week and your custom weeks; pick one and press *Serve*. |
| **Build a week** | Pick machine setups from every archived week (by series or one by one, with their badges listed) and build your own week. |
| **Badges** | Make your own badges from pictures, in sets that get their own page in the badge collection. |
| **Machine editor** | Make your own claw machines from Nintendo's: place badges and obstacles, check the physics, pick a background, icon and arm. |
| **Custom weeks** | Put your machines and any of Nintendo's in a week, change the hall pictures, Arcade Bunny's lines and the gallery, and *Build and serve*. |
| **Free plays** | Give free plays for the game date, and see which daily campaigns your save has collected. |
| **Letters** | Write letters for the 3DS's Notifications applet, with a picture, and send them through SpotPass (experimental: see [spotpass-letter/README.md](spotpass-letter/README.md)). |
| **Saves** | List, back up and reset saves. |
| **Maintenance** | Put the server into maintenance, now or for a scheduled window: the 3DS can't log in and shows the system's maintenance error. No restart needed. |

After serving a week or giving free plays, fully close and reopen Badge Arcade.

## Updating

The installed version is in `version.txt` and the manager's title bar.
The manager checks for a new version once a day and shows **Update now** on
the Setup tab when there is one. Tick *Install updates automatically* to have
it install updates as it opens.

Updates come from this project's `main` branch on GitHub: when the `version.txt`
there holds a higher number than yours, the files on `main` are downloaded. They
replace the project's own files and never touch yours: `server/config.json`,
saves, SpotPass files, keys and dumps, and the manager's settings are kept. The
files an update replaces are zipped into `backups/` first. If the folder is a
git checkout, it is updated with `git pull` instead.

From the command line, or for updating on a schedule:

```sh
python update.py --check        # exit code 0: up to date, 10: update available, 1: couldn't check
python update.py --apply --yes  # install the newest version without asking
```

Stop the server and proxy first: `update.py` refuses to update while they're
running. To update unattended, schedule `python update.py --apply --yes` in the
project folder for a time the server isn't running (Windows Task Scheduler, or
cron on Linux/macOS: `0 4 * * * cd /path/to/badge-arcade-server-full && python3 update.py --apply --yes`).

To publish a new version, raise the number in `version.txt` and `VERSION` (for
example `2.0.0` to `2.1.0`) in the change that goes to `main`. Installs pick it
up at their next check. Merge the rest of the change first or in the same merge:
whatever is on `main` when the number goes up is what gets installed. The
repository has to stay public for installs to see it.

Every install updates with the `update.py` it already has, so a few things
stay as they are for older versions:
- Version 1.0.0 looked for GitHub *releases* (a tag like `v2.0.0`) instead of
  `version.txt`, and wants a `VERSION` file in them. The `v2.0.0` release
  brings those installs up to date; after that they update from `main` too.
- `Setup.bat` and `Badge Arcade Manager.bat` never change (a test checks):
  Windows goes on reading a running `.bat` file where it left off, so an
  update that changed `Setup.bat` while it runs `update.py` would run pieces
  of the new one. New setup steps go in `install.py`.
- An update runs the new `install.py --packages`. It installs the helper's
  numpy and pymunk too, but if those fail the update still counts as done,
  and the Setup tab offers them again.

## What's here

| Folder / file | What it is |
|---|---|
| `Setup.bat`, `install.py` | One-time setup, and the checklist the manager shows. |
| `Badge Arcade Manager.bat`, `manager.py` | The manager window. |
| `update.py`, `version.txt`, `VERSION` | Checks for and installs new versions (`VERSION` is for installs from 1.0.0). |
| [`server/`](server/README.md) | The server: NEX authentication and secure servers, save storage, SpotPass file hosting, and the proxy and hotspot (`server/mitm/`). |
| [`spotpass-letter/`](spotpass-letter/README.md) | SpotPass tools used by the manager: switch weeks (`serve.py`), build custom weeks (`custom_week.py`), free plays (`free_plays.py`), letters (`letters.py`, `make_letter.py`, experimental), and find the game's signing key (`find_sign_key.py`). |
| [`helper/`](helper/README.md) | Your own badges and machines (the Badges, Machine editor and Custom weeks tabs): `bahelper/` does the work, `gui/` has the tabs, and `workspace/` keeps what you make. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | For working on the project: how the pieces fit, where new code goes, and the rules that keep installs updating. |

## Troubleshooting

- **The 3DS doesn't see the hotspot.** The 3DS only has 2.4 GHz Wi-Fi. If the
  PC is hosting the hotspot on 5GHz and unable to broadcast on 2.4GHz, switch
  to proxy mode.
- **The connection test fails on the hotspot.** Check the Setup tab for a
  DNS warning, and that the NoSSL patch is on the SD card with game patching
  turned on in Luma. If that doesn't work, switch to proxy mode.
- **The hosts file still has the Badge Arcade block** (for example after the
  PC restarted with the hotspot on). The manager offers to remove it when it
  starts, or run `python server/mitm/hotspot.py off`.
- **Error 022-2534 when Badge Arcade starts.** The server didn't know which
  3DS was logging in. The proxy learns the console's PID when the 3DS goes
  online and its NNID when Badge Arcade signs in, and needs one of them; the
  Server tab shows which ones it has seen. Close Badge Arcade, reconnect the
  3DS to the internet while the proxy is running, and open it again.
- **"This service is not available in your region"** straight away: see
  *Region-changed consoles* in [server/README.md](server/README.md#each-session).
- **"Could not save to the SD card" right after the download (EUR game).**
  The SpotPass files are the USA ones, and the console can't save them for
  the EUR game unless the server converts them, which needs `boot9.bin` in
  `spotpass-letter/`. The server's log says *Made SpotPass file ... out to
  Badge Arcade EUR* when it does. Restart the game after adding `boot9.bin`.
- **Proxy mode stopped working after a restart.** The PC's IP address can
  change; the Setup tab shows the current one to enter on the 3DS.

## Legal

This project isn't affiliated with or endorsed by Nintendo. It contains no
Nintendo code or assets, and it is meant for preserving your own copy of the
game on your own console. Purchases are not possible: Nintendo closed the 3DS
eShop in 2023, and this project doesn't bypass that.

## Credits

Special thanks to:
- The [Pretendo Network](https://github.com/PretendoNetwork) team and the developers of all of the reverse-engineering tools. The server's protocol behaviour is ported from Pretendo's [badge-arcade-authentication](https://github.com/PretendoNetwork/badge-arcade-authentication) and [badge-arcade-secure](https://github.com/PretendoNetwork/badge-arcade-secure) servers, and the proxy uses their [mitmproxy-nintendo](https://github.com/PretendoNetwork/mitmproxy-nintendo) setup.
- The documentation of the NEX protocol made by [kinnay](https://github.com/kinnay/NintendoClients/wiki), whose NintendoClients library the server uses.
- [3dbrew](https://www.3dbrew.org/wiki/Nintendo_Badge_Arcade) contributors, for the SpotPass and Badge Arcade documentation.
- The people who archived Badge Arcade's SpotPass data before the shutdown.
- The agentic coding tool, Claude Code, for putting together all the pieces

The original codebase of this project is based on Pretendo Network's
[BOSS](https://github.com/PretendoNetwork/BOSS) servers.

Licensed under the GNU AGPL v3 (see [LICENSE](LICENSE)).

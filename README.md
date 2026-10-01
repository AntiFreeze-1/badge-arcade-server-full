# Nintendo Badge Arcade server

A self-hosted server that keeps **Nintendo Badge Arcade** (3DS) working after
Nintendo's shutdown. It saves your progress, serves weekly claw machines from
SpotPass data, hands out free plays, and lets you make your own badges, machines
and weeks. A manager window handles setup and day-to-day use.

> **No Nintendo files are included.** You provide your own SpotPass data and keys
> (see [What you need to provide](#what-you-need-to-provide)). Please don't commit
> them, so the project doesn't get taken down. 😊

## Quick start

1. Install [Python 3.12 or newer](https://www.python.org/downloads/).
2. Run **Setup.bat** (Windows) or `python install.py` (macOS/Linux). This installs
   everything and creates `server/config.json` (about 100 MB of downloads).
3. Add the files listed in [What you need to provide](#what-you-need-to-provide).
4. Copy the `luma` folder from `server/mitm/sd-card` to the root of your 3DS's SD
   card. In Luma3DS settings (hold SELECT while powering on), turn on
   **Enable game patching**.
5. Open the manager: **Badge Arcade Manager.bat** (Windows) or
   `python -m badge_arcade config.json` (macOS/Linux). The **Setup** tab shows
   anything still missing.
6. Connect the 3DS (below) and open Badge Arcade.

For more on macOS/Linux, see [server/README.md](server/README.md).

## Connecting the 3DS

Choose one option in the manager's **Setup** tab.

**PC hotspot (recommended, Windows).** Press **Turn on hotspot** and approve the
admin prompt. On the 3DS, go to *System Settings → Internet Settings → Connection
Settings → New Connection → Manual Setup → Search for an Access Point*, choose the
network shown in the manager, and enter the password. No proxy settings needed.
Your PC needs Wi-Fi and an internet connection, and the hotspot must be on
2.4 GHz (the 3DS can't see 5 GHz). Press **Turn off hotspot** when done.

**Proxy.** Keep the 3DS on your normal Wi-Fi. Go to *Connection Settings → your
connection → Change Settings → Proxy Settings → Yes → Detailed Setup* and enter
the proxy address and port shown in the manager. Turn the proxy off on the 3DS
when you're done playing.

## What you need to provide

| What | Put it in | How to get it |
|---|---|---|
| A 3DS with Luma3DS and **Badge Arcade 1.3.1** | – | Your own console and game. |
| SpotPass files | `other/` | From archive.org: [.boss files](https://ia800600.us.archive.org/view_archive.php?archive=/32/items/3ds-boss-data-4/OvbmGLZ9senvgV3K.zip) or [.enc files](https://archive.org/download/nintendo-badge-arcade-data-updated), or your own dumps. |
| `boot9.bin` | `spotpass-letter/` | Dump it with GodMode9 ([instructions](spotpass-letter/README.md#getting-the-key-from-your-own-console)). Needed for switching weeks, custom weeks, free plays, letters, and EUR games. |
| `nex-keys.txt` (rarely needed) | `server/` | Only if the server log says *No NEX password known*. See [server/README.md](server/README.md#the-consoles-nex-password). |

**SpotPass files needed in `other/`** (US names; they also work with EUR games):

- `data_v131-2022-12-29-09-40-NA.enc` – a weekly machine set, used as the base for custom weeks
- `playinfo_v131-2022-12-29-09-40-NA.enc` – that week's play settings, used for free plays
- `allbadge_v131.dat.boss` – all badge graphics (required)
- Any other `data_v131-*.enc` weeks (optional)

Your save database (`server/data/badge_arcade.db`) is created automatically.

## Using the manager

| Tab | What it does |
|---|---|
| **Setup** | Checklist, connection options, and updates. |
| **Server** | Start/stop the server, set the game date, and watch activity. |
| **Serve a week** | Pick an archived or custom week and serve it. |
| **Build a week** | Mix machines from archived weeks into a new week. |
| **Badges** | Make your own badges from pictures. |
| **Machine editor** | Design your own claw machines. |
| **Custom weeks** | Combine machines and customize the hall, gallery and Arcade Bunny's lines. |
| **Free plays** | Give free plays. |
| **Letters** | Send letters to the 3DS's Notifications (experimental). |
| **Saves** | Back up, list and reset saves. |
| **Maintenance** | Put the server into maintenance mode. |

After serving a week or giving free plays, **fully close and reopen Badge Arcade.**

On macOS/Linux, you can do the same from the command line:

```sh
cd spotpass-letter
python serve.py week dec29
python serve.py free-plays --plays 2
```

## Updating

The manager checks for updates daily and shows **Update now** on the Setup tab.
Updates never touch your config, saves, SpotPass files or keys, and old files are
backed up to `backups/` first.

From the command line (stop the server first):

```sh
python update.py --check        # check for an update
python update.py --apply --yes  # install it
```

Contributors: see [CONTRIBUTING.md](CONTRIBUTING.md) for how releases and
versioning work.

## Troubleshooting

- **3DS can't see the hotspot:** it's probably on 5 GHz. Use proxy mode instead.
- **Connection test fails on the hotspot:** check the Setup tab for a DNS warning
  and confirm the NoSSL patch and game patching are on. Otherwise use proxy mode.
- **Hosts file still has the Badge Arcade block** (e.g. after a restart): the
  manager offers to remove it, or run `python server/mitm/hotspot.py off`.
- **Error 022-2534:** the server didn't recognize your 3DS. Close the game,
  reconnect the 3DS to the internet while the proxy is running, and try again.
- **"Not available in your region":** see *Region-changed consoles* in
  [server/README.md](server/README.md#each-session).
- **"Could not save to the SD card" (EUR game):** add `boot9.bin` to
  `spotpass-letter/` and restart the game.
- **Proxy stopped working after a restart:** your PC's IP may have changed. Check
  the Setup tab for the current one.

## Legal

Not affiliated with or endorsed by Nintendo. Contains no Nintendo code or assets
and is meant for preserving your own copy of the game. Purchases aren't possible
and the closed eShop is not bypassed.

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

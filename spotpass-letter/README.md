# SpotPass tools and letters (Badge Arcade)

The tools the manager uses to put SpotPass files live: weekly machines, free
plays, and **letters**, entries in the 3DS Notifications applet like the ones
Nintendo sent from Badge Arcade.

**Letters are experimental.** Building them is done and tested, and the manager's
**Letters** tab sends them through the server. **They haven't been confirmed on a
console yet.** Two parts of the format are informed guesses (see
[Verified vs. unverified](#verified-vs-unverified)), and the 3DS may never run the
SpotPass task that fetches them (see [Delivery route](#delivery-route)).

## Files

| Path | What |
|---|---|
| `serve.py` | Switch the weekly machines and give free plays (used by the manager window). |
| `custom_week.py` | Build a custom week from every archived machine setup. |
| `free_plays.py` | Rebuild `playinfo` with a different number of daily free plays. |
| `repack.py` | Re-issue a SpotPass file under a new NsData ID. |
| `find_sign_key.py` | Find the game's signing key in its dumped code. |
| `letters.py` | Letters for the manager: turns any picture into the 400×240 JPEG a letter needs, and keeps the history of your letters in `out/letters/`. |
| `make_letter.py` | Builds and inspects letters (`build`, `selftest`, `inspect`, `key`, `find-urls`). Standard library only (it has its own AES-128), faster with pycryptodome. |

Not in the repository (you provide or generate them, see the main README):
`boot9.bin`, `badge_arcade_hmac.key`, the game's `.code`, `serve_state.json`,
`out/` (built files, including custom weeks in `out/weeks/`) and `ref/` (local
copies of the sources listed at the end).

## Findings

### How a SpotPass letter reaches the Notifications applet
1. The game registers a BOSS (SpotPass) task. Badge Arcade has `data`, `FGONLYT` and `news`.
   The BOSS system module downloads the task's URL in the background and parses the
   **BOSS container** it gets back.
2. A container holds one or more *payloads*. Each payload header names a **program ID**.
   Normal payloads go to that title's SpotPass extdata. A payload whose program ID is the
   **news system module (`0004013000003502`)** is handed to the news module, which stores
   it in its own save (`news.db` plus `newsXXX.txt` and `newsXXX.mpo`, system save 00010035).
   The Notifications applet shows the entries from that save. The blue LED and the
   "unread" dot come from the same save.
3. Badge Arcade really did this. The archive.org dump of notification saves has 34
   Badge Arcade letters from Feb 2016 to Jun 2017 ("New Badges at the Arcade!",
   "A Message from Arcade Bunny!", ...). Each has source program ID `0004000000153500`,
   version 1, nsDataIds 0x69e4–0x9639 (Nintendo's server counter), the jump parameter
   set to the title ID (0 on one letter), and most have a 400×240 JPEG of about 43 KB
   (header flag "is JPEG" = 1). See `ref/real_badge_arcade_letters.csv`.
4. The same dump has 178 letters in all, from about 20 games and the system. They all
   use the layout below. See [Checked against real letters](#checked-against-real-letters).

### Container format (all big-endian)
```
0x000  BOSS header, 0x28, cleartext
       "boss" | u32 0x00010001 | u32 file size | u64 serial | u16 1 | u16 0
       | u16 hash type 2 (SHA-256) | u16 RSA type 2 (RSA-2048) | 12-byte IV
0x028  AES-128-CTR (keyslot 0x38 normal key, IV = header[0x1C:0x28] + 00000001):
       content header 0x132: 0x12 bytes (byte0 bit 0x80 = "always mark arrived",
         u16 at 0x10 = payload count) | SHA-256(first 0x12 bytes + 00 00) | RSA-2048 sig
       payload header 0x13C: u64 program ID | u32 0 | u32 content datatype
         | u32 payload size | u32 nsDataId | u32 version
         | SHA-256(first 0x1C bytes + 00 00 + payload) | RSA-2048 sig
       payload
```
The console treats a download as new when the **serial** changes. The script uses the
Unix time for it by default.

### News payload (little-endian)
```
0x00  u8 valid=1, unread=1, is_jpeg, spotpass=1, opted_out=0, has_url, unknown(1), pad
0x08  u64 source program ID   (Badge Arcade: USA 0004000000153500, EUR 0004000000153600)
0x10  u32 nsDataId, u32 version
0x18  u64 jump parameter      (real letters: the title ID)
0x20  title: UTF-16LE, 0x40 bytes (31 characters + NUL)
0x60  message: UTF-16LE + NUL [+ URL as UTF-8 + NUL], zero-padded to 0x1780 bytes
0x17E0 optional image: JPEG (or MPO), 400x240, <= 50 KB (hard max 0x10000)
```
The news module adds the arrival time itself. Its saved header is 0x70 bytes: this same
layout, with the date-time inserted at 0x28 and the title moved to 0x30.

### Key and signatures
- **AES key:** the keyslot 0x38 normal key, which the ARM9 bootrom loads from a key
  table inside boot9. That makes it the same on every retail console and readable from
  your own `boot9.bin`. It sits at offset `0xDBD0` in a full 64 KiB `boot9.bin` and at
  `0x5BD0` in a 32 KiB `boot9_prot.bin`. The script checks the key against the MD5 that
  Pretendo publishes (`86fbc2bb…`). If you have the real Nintendo containers in `other/`,
  it also decrypts the content header of `other/playinfo_v131.dat.boss` (read-only) and
  checks its SHA-256. No key is included here.
- **RSA-2048 signatures:** only Nintendo can sign them, so the script fills them with
  zeros, just like Pretendo's boss-crypto. 3dbrew says custom containers only work with
  "CFW / ARM11-kernelhax with the sigchecks for this patched". Pretendo's boss-crypto
  README says "Luckily Luma patches these signature checks anyway". **So this can only
  work on a console running Luma3DS custom firmware, and that part is unconfirmed.**
  The SHA-256 hashes are computed correctly.

### Delivery route
- The `news` task URL: 3dbrew gives
  `https://npdl.cdn.nintendowifi.net/p01/nsa/OvbmGLZ9senvgV3K/news/<lang>/news_v131.dat`
  for USA (EUR code `J6la9Kj8iqTvAPOq`), but Badge Arcade's Japanese news task really
  served **`news.dat`** (SpotPass Archive: `JP/ja/news/filelist.txt` lists
  `news.dat 585 1455082053`, a letter from Feb 2016 still live at shutdown). The file
  name is what matters here: the proxy redirects any npdl request whose *file name*
  exists in `other/`, whatever the region and language path. So a letter goes live as
  both `other/news.dat.boss` and `other/news_v131.dat.boss`.
- The proxy's policy list already lists `news` at HIGH with DefaultStop=false.
- **The console has never requested `news_v131.dat` in the available logs**
  (mitm.log/server.log, 27 Sep 03:05–04:20). In that time it only fetched
  `FGONLYT/playinfo_v131.dat` and `data/data_v131.dat`, and only while the game was
  running. `news` is probably a background task that runs on the BOSS schedule, mostly in
  sleep mode. It may also not be registered at all, if SpotPass notifications were
  declined or the task expired. Nintendo's US and EU `news` tasksheets were empty at
  shutdown, but the Japanese one still had a letter (see above).
- **Conclusion:** the `news` task is the right path, and the only one that goes through
  the existing server. Whether it fires is the biggest open question. Use
  `find-urls` on the BOSS system save to check whether the task is registered.
- **More reliable alternative (not built here):** a small homebrew app that calls
  `news:s` AddNotification directly (libctru `newsInit()` + `NEWS_AddNotification()`).
  It needs no key, no signature and no SpotPass. It works on any console with Luma, but
  it bypasses the server.

## Switching machines and free plays

`serve.py` puts a weekly machine set or free plays live on the server:

```sh
python serve.py week dec29       # Nintendo's Dec 29, 2022 machines
python serve.py week custom      # Monster Hunter, Mega Man, ... (custom_week.py)
python serve.py free-plays       # 10 free plays for today (--plays N)
python serve.py status           # what's live
```

Then fully close and reopen Badge Arcade. What makes this work:

- Badge Arcade uses the server's `game_date` (server/config.json; the current
  date by default), not the 3DS clock, and the week's `Schedule.xml` for that
  day's machines. `serve.py` moves a week's schedule to the game date when
  serving it.
- The console only downloads a file whose SpotPass (NsData) ID is new to it,
  and pays out each free-play campaign ID once, so every run uses new ones
  (tracked in `serve_state.json`). The console keeps files with a blank
  Nintendo signature; `playinfo` must carry a valid HMAC from the game's key
  (`badge_arcade_hmac.key`, found in its code with `find_sign_key.py`).

### How many machines a week can have

The schedule numbers each machine on the floor through the week
(`DefaultStageName000` to `999`), and Badge Arcade crashes when a week goes past
999: for example 147 machines with *Every machine, every day* (147 × 7 = 1,029).
The builder therefore puts at most as many machines on the floor each day as
Nintendo ever did (read from the archived weeks in `other/`), and never more than
fits the numbering. When you choose more, each day shows a different part of them,
so every machine still gets its turn during the week. `serve.py` refuses weeks
built before this limit; build them again.

## Getting the key from your own console
You need Luma3DS/boot9strap custom firmware with GodMode9. This is the same step the
Azahar/Citra emulators ask for.
1. Start GodMode9 by holding START while powering on.
2. Open `[M:] MEMORY VIRTUAL`, select `boot9.bin`, press A and choose **Copy to 0:/gm9/out**.
3. Copy `gm9/out/boot9.bin` from the SD card to your PC and keep it private. Save it
   outside the project, or next to the script as `boot9.bin`. **Do not share or commit it.**
4. Check it: `python make_letter.py key --boot9 path\to\boot9.bin --save boss_key.hex`.
   This prints OK when the MD5 matches and the key decrypts the real container in
   `other/`. After that you can use `--key-file boss_key.hex`.
   If you already have an Azahar/Citra `aes_keys.txt` containing `slot0x38KeyN=...`,
   `--key-file aes_keys.txt` also works.

## Sending a letter

In the manager, open the **Letters** tab:

1. Write a title (up to 31 characters) and a message (up to 2,999). A link is
   optional. Pick the region of your copy of Badge Arcade.
2. Optionally choose a picture. Any picture works: it's scaled and cropped to
   400×240 and saved as a JPEG under 50 KB, and the tab shows a preview.
3. Press **Send to 3DS**. The letter goes live as `other/news_v131.dat.boss`,
   with a new SpotPass ID, and is kept in the list on the left. **Save draft**
   keeps it without sending it.
4. With the server and proxy running, leave the 3DS in sleep mode with Wi-Fi on.
   When the server sends the letter, the tab says it was downloaded, and it
   should appear in the Notifications applet.
5. **Take letter down** removes it from the server. Your letters stay in the list;
   pick one to send it again (a sent letter becomes a new letter when you change it).

Back up the news save first (step 1 of [Delivering it](#delivering-it)).

From the command line:

```sh
python serve.py letter --title "A Message from Arcade Bunny!" --message-file letter.txt --image bunny.png
python serve.py letter --remove
python serve.py status
```

## Making a letter by hand
`make_letter.py build` writes a letter to `out/` without putting it live, which
is useful for trying variants of the format:

```bat
cd spotpass-letter
python make_letter.py selftest --boot9 path\to\boot9.bin
python make_letter.py build --boot9 path\to\boot9.bin ^
    --title "A letter from Arcade Bunny!" ^
    --message-file letter.txt ^
    --image bunny_400x240.jpg
```
- Title: at most 31 characters. Message: plain text, `\n` for new lines (use
  `--message-file` for long text), at most about 2,999 characters. `--url` adds a link.
  `--image` must be a 400×240 JPEG of 50 KB or less.
- EUR console: add `--region EUR`.
- Output: `out/news_v131.dat.boss`. Build every letter fresh: the serial and the
  nsDataId both come from the current time.
- Without `--boot9` or `--key-file`, the script writes only the unencrypted payload and
  container, for inspection.
- Other options: `--datatype`, `--payload-program-id`, `--jump-param`, `--unknown-flag`,
  `--serial` and `--ns-data-id` let you try variants if the default doesn't work.
- `inspect FILE --key-file ...` decrypts any container (quickly with pycryptodome,
  slowly without it).
- `compare --reference --boot9 boot9.bin` downloads the real Badge Arcade letter from
  the SpotPass Archive and prints each of its fields next to what this script builds
  for the same text and picture, marking any that differ.

## Delivering it
A letter goes out two ways at once:

- **Inside playinfo (the reliable way).** The 3DS downloads `playinfo_v131.dat`
  (the FGONLYT task) every time Badge Arcade opens. The letter is added to it as a second
  payload for the news module, the way Nintendo packed its own letters (Badge Arcade's
  real Japanese letter had a game payload and a letter payload in one file). SpotPass
  hands each payload to the program it names, so the letter reaches the Notifications
  applet as soon as the game has downloaded its data. The game's own payload is
  unchanged; it gets a new SpotPass ID so the 3DS downloads it again. This needs a live
  playinfo, which the first free plays create.
- **As the news task's file** (`news.dat` / `news_v131.dat`). The 3DS runs that task on
  its own schedule, often in sleep mode, and the user's server log showed it never ran
  while connected through the proxy. When it runs elsewhere it reaches Pretendo's server
  instead, which is how Pretendo's "Badge Arcade is Back!" letter arrived. That letter
  also showed the console accepts SpotPass files with blank signatures.
  The proxy's policy list now gives the news task the EXPEDITE priority and Persistent
  and Revive (as Pretendo's list does for its 3DS tasks), so it should run sooner: leave
  the 3DS in sleep mode on the hotspot for a while and watch for `Sent SpotPass file
  news`.

Steps:
1. Back up first: in GodMode9, copy `1:/data/<id0>/sysdata/00010035/00000000` (the
   news save) to the SD card. Having a NAND backup is wise too.
2. Send it from the Letters tab (or `python serve.py letter`). The proxy picks up the
   new file list within 60 s. Nothing needs restarting.
3. With the server and proxy running, open Badge Arcade and let it finish "Downloading
   Data". server.log shows `Sent SpotPass file playinfo_v131.dat.boss (USA, N bytes)`;
   with a letter inside, N is the letter's size (picture included) bigger than without.
   The Letters tab marks the letter as downloaded. Close the game and open Notifications.
4. If Badge Arcade can't download its data after a letter was sent, press **Take letter
   down**: playinfo goes back to the game's payload alone. If it's downloaded but no
   letter appears, the payload layout or datatype is the suspect (`make_letter.py
   compare` checks them against Nintendo's letter).
5. Undo: **Take letter down** (or `python serve.py letter --remove`). You can delete the
   letter in the Notifications applet.

## Checked against real letters

**Saved letters** (archive.org, [Nintendo 3DS SpotPass Notifications](https://archive.org/details/nintendo-3ds-spot-pass-notifications)):
178 letters from consoles' news saves, including 33 from Badge Arcade, others from about 20
games, and system letters. They show the letter after the news module has stored it, and
they agree with what we send:
- **Header:** the same fields as ours. The module stores 8 zero bytes and the arrival
  time at 0x20 (so the title moves from 0x20 to 0x30), as Azahar's `NotificationHeader`
  does.
- **Flags:** byte 0 valid is 1. Byte 1 is 1 = unread (3dbrew; the unopened letters
  still have 1). Byte 3 is 1 for SpotPass (0 on system letters). Byte 6 is 1 on almost
  all of them, Badge Arcade's included.
- **Picture flag:** byte 2 is 1 on every letter with a picture (155), including the 16
  with 3D (MPO) pictures, and 0 on every letter without one. 3dbrew calls it "is JPEG",
  but it means "has a picture": `make_letter.py` used to set 0 for MPO pictures and now
  sets 1.
- **Pictures:** every one is 400×240, baseline, and at most 50 KB, like the ones the
  manager makes.
- **Text:** UTF-16LE without a BOM, ending with one NUL. With a link, the link follows
  as UTF-8 with its own NUL, and byte 5 is 1; a link typed into the text is just text.
  Titles are up to 31 characters and messages up to 2,920, within our limits.
- **Source and jump:** the source is the sending title; the jump parameter is that title
  (Badge Arcade), 0, or another title (e.g. a demo letter pointing at the game).

**Sent letters** (SpotPass Archive, [3ds-boss-data](https://archive.org/details/3ds-boss-data)):
57 `news` tasks (one per game and region) still had a letter of 50 KB or more when the servers closed, among them
Badge Arcade's Japanese one. They show the letter as Nintendo sent it:
- **Container header:** it's unencrypted and matches ours field by field: `boss`,
  0x10001, the file size, the serial, `1, 0`, hash type 2, RSA type 2 and a 12-byte IV.
  Nintendo's serials are small counters; ours are Unix times, and the console only needs
  a new one.
- **Size:** the letters are 54–58 KB, which is the 0x60 header, 0x1780 bytes of text and
  a picture of about 50 KB after the container and payload headers.
- **Decrypted** (`make_letter.py compare --reference`, run with a console's key on Badge
  Arcade's real Japanese letter):
  - **Letter payload:** program ID `0004013000003502`, datatype `0x20001` and version 1
    match ours. The letter itself (header, flags, title, message and picture) is byte for
    byte what this script builds from the same text.
  - **Content flags:** 0x00. Pretendo's boss-crypto sets 0x80 ("always mark arrived"),
    which this script used to copy; it now uses 0x00 like Nintendo.
  - **A second payload** for the game (`0004000000134600`, datatype `0x10010`, 585 bytes):
    a SARC with an empty `Schedule.xml` and `emergencyStop/emergencyStop` containing
    `emergency`. It's a switch for the Japanese game's SpotPass, not part of the letter,
    so our letters **don't** include it.
  - In the letter's own header, the version field is 0 and the payload header's is 1.
    Ours uses 1 for both, which is what all 178 saved letters show.

## Verified vs. unverified
**Verified (by me, locally or from primary sources):**
- The content datatype `0x20001` and the payload layout, against Nintendo's real Badge
  Arcade letter (see above).
- The BOSS header layout matches the real files in `other/` (read-only check).
- The container builder follows Pretendo's boss-crypto field by field. The
  build → encrypt → decrypt → hash check → compare round trip passes, and the AES code
  passes the FIPS-197 and NIST SP 800-38A CTR test vectors.
- Badge Arcade sent real Notifications-applet letters, and their header values are in
  the CSV.
- The key offset in boot9 matches the order used by both yellows8's `boot9_keytool.sh`
  and pyctr, and the MD5 check guards it.
- No news request appears in the available logs. The local file-name matching would
  serve the file.

**Unverified:**
- Whether Luma3DS really bypasses the BOSS RSA check (Pretendo's claim).
- Whether the `news` task is registered and when it runs, and the letter case of the
  language path (irrelevant here).
- Whether the news module skips a letter whose nsDataId or serial it has already seen.

## Risks
- On a stock console, or if the signature check isn't patched, BOSS most likely just
  throws the file away: nothing happens.
- A malformed payload could at worst leave a broken entry or upset the Notifications
  applet. That's why step 1 backs up the news save; restore it with GodMode9 if needed.
- The key is Nintendo's and the same on every console. Keep `boot9.bin` and the key file
  private, and never publish containers or tools with the key built in.
- Only the `news` file name is affected. Other SpotPass files and the server are untouched.

## Sources
- 3dbrew: [SpotPass](https://www.3dbrew.org/wiki/SpotPass) (container, hashes, keyslot, custom-content note), [BOSS Services](https://www.3dbrew.org/wiki/BOSS_Services), [NEWSS:AddNotification](https://www.3dbrew.org/wiki/NEWSS:AddNotification) (header, message, URL, image), [News Services](https://www.3dbrew.org/wiki/News_Services) (news.db), [Notifications](https://www.3dbrew.org/wiki/Notifications) (limits), [AES Registers](https://www.3dbrew.org/wiki/AES_Registers) (0x38 set by bootrom, key-init order), [Nintendo Badge Arcade](https://www.3dbrew.org/wiki/Nintendo_Badge_Arcade) (URLs), [BOSS Savegame](https://www.3dbrew.org/wiki/BOSS_Savegame), [Config Savegame](https://www.3dbrew.org/wiki/Config_Savegame).
- Pretendo: [boss-crypto](https://github.com/PretendoNetwork/boss-crypto) (`src/3ds.ts`, README on key and signatures), [BOSS server](https://github.com/PretendoNetwork/BOSS) (npdl URL routes, Badge Arcade tasks).
- Rokkubro's Citra BOSS branch: [spotpass_utils.cpp](https://github.com/Rokkubro/citra/blob/feature/boss/src/core/hle/service/boss/spotpass_utils.cpp) (news payload detection and layout); Azahar [online_service.h](https://github.com/azahar-emu/azahar/blob/master/src/core/hle/service/boss/online_service.h) (`NEWS_PROG_ID`).
- yellows8: [3dscrypto-tools bosstool.c](https://github.com/yellows8/3dscrypto-tools/blob/master/bosstool.c), [boot9_tools boot9_keytool.sh](https://github.com/yellows8/boot9_tools/blob/master/boot9_keytool.sh); ihaveamac [pyctr engine.py](https://github.com/ihaveamac/pyctr/blob/master/pyctr/crypto/engine.py).
- archive.org: [Nintendo 3DS SpotPass Notifications](https://archive.org/details/nintendo-3ds-spot-pass-notifications) (real letters), [3ds-boss-data-4](https://archive.org/details/3ds-boss-data-4) (`OvbmGLZ9senvgV3K.zip`, `US/en/news/tasksheet.xml`).

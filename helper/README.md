# Your own badges and machines (Badge Arcade Helper)

The **Badges**, **Machine editor** and **Custom weeks** tabs of the
[Badge Arcade Manager](../README.md) make **your own badges and claw machines** for Nintendo
Badge Arcade, put them in a week alongside Nintendo's machines, and serve that week to your
3DS. They were the Badge Arcade Helper, a program of its own, until version 2.0.0.

- **Custom badges** from any picture: 64×64 PNGs (the usual custom-badge size, like the
  Portal pack), or 128×64, 64×128 and 128×128 for big badges. The helper makes everything
  the game needs: the HOME Menu picture, the in-machine texture with its white sticker
  rim, the shadow, and the collision shapes the claw's physics use.
- **Badge sets**: each set is its own category, with its own page (book) in the badge
  collection and its own icon.
- **Custom machines** in a visual editor. Start from any of Nintendo's 800+ archived
  machines, then drag, turn and resize the badges, add yours or any of Nintendo's (retired
  badges too), and use your own background picture and hall icon. Obstacles (ramps, walls,
  holes, turntables, hooks) can be moved, added from a library of 313, or removed. The
  editor has undo/redo and multi-select, fills a machine from a set in one go, and checks
  the layout with a physics simulation.
- **Weeks**: mix your machines with Nintendo's and serve them. SpotPass IDs are tracked
  so the 3DS always takes the new week.

> **Not tried on a console yet.** Everything the helper builds reads back correctly with
> the same checks the console makes, and matches Nintendo's own files byte for byte where
> the format is known. But custom badges and machines haven't been played on a 3DS yet.
> **Back up first**: Badge Arcade's extra data and the HOME Menu's badge data (extdata
> `0x14d1`), with Checkpoint or GodMode9.

## Setup

1. Set up the manager (see its [README](../README.md#quick-start-windows)). Setup.bat and
   updates also install numpy and pymunk (for the physics check); if they're missing, the
   Setup tab's checklist and these tabs offer to install them.
2. The same files as the rest of the manager:
   - **boot9.bin** from your own 3DS, in `spotpass-letter/` (see its
     [README](../spotpass-letter/README.md#getting-the-key-from-your-own-console)).
   - **Nintendo's archived SpotPass files** in `other/`:
     - `data_v131-2022-12-29-09-40-NA.enc`, required (every week is built on it);
     - `allbadge_v131.dat.boss`, required;
     - `allbadge_v130.dat.boss` (the 2016 file), optional: 398 more badges, 27
       backgrounds and 66 machines;
     - every other archived week, optional: more machines to choose from.

     With everything, that's 6,274 badges, 371 backgrounds and 954 machines. A different
     boot9.bin or SpotPass folder can be set in the Setup tab's *Your own badges and
     machines* part, which also lists what's found.
3. That's all: the first time you open one of these tabs, it reads the archived weeks (a
   minute or so; after that it's cached in `helper/workspace/cache`). Weeks become the
   server's own custom weeks and are served with `spotpass-letter/serve.py` (see Custom
   weeks below).

**Coming from the standalone helper?** Its work isn't lost: when the old
`badge-arcade-helper` folder is next to this one, the manager offers to copy your sets,
badges, machines, weeks, settings and SpotPass counter the first time it opens. Or press
**Import from Badge Arcade Helper...** in the Setup tab and pick the old folder. Nothing in
the old folder changes; delete it once everything's here. (It only imports into an empty
workspace, so the two don't get mixed.)

## Making things

**Badges tab.** *New set...* makes a set. *Import pictures...* takes PNGs, or ZIPs of them
(preview sheets called `preview.png` are skipped). Pick a badge to see its three textures
and its collision shapes in red. For your own badges you can change:
- **Title**: the badge's name in the HOME Menu.
- **Set**: which set it belongs to.
- **Size**: in 64-pixel tiles.
- **Pixel art**: sharp or smooth scaling. It's guessed from the number of colours.
- **Shape**: *Simple collision shape* uses one outline for fiddly pictures.
- **Opens on the HOME Menu**: what tapping the badge on the HOME Menu opens, like
  Nintendo's shortcut badges. Choose a system program (Download Play, System Settings,
  Activity Log, Camera, Sound, Mii Maker, StreetPass Mii Plaza, eShop) and your 3DS's
  region, or type any game's or app's title ID (16 hex digits, from FBI or GodMode9). It's
  stored in the badge at 0xA4, which is 0xFFFFFFFFFFFFFFFF (nothing) on ordinary badges.

**Series not in your files** (at the bottom of the list) names the series whose badges are
in none of your SpotPass files. Nintendo's category list names them, but their badges only
came in weeks your archive doesn't have. With the archived US files, that's 9 series and
514 badges:
- Pikmin
- Mario & Friends
- Mario Party
- Mario vs. Donkey Kong
- Yoshi's Woolly World
- Majora's Mask 3D
- Happy Home Designer
- BOXBOY!
- Pushmo

If you find SpotPass files that have them (other weeks, another region's data or
allbadge), put them in an `extra` folder (in `other/` or `helper/`, or one set on the
Setup tab). The helper reads every SpotPass file in it, whatever its name, and
skips anything else.

Nintendo's badges are all listed as well, by series.

**Machine editor tab.** *New machine...* shows Nintendo's machines with a preview. Your machine
keeps the template's ramps, holes, pegs and turntables. All 954 machines in the archive can
be templates, including 79 that are only partly there: their missing parts (usually a ramp
or air vent, sometimes a badge or the background) are left out, and the preview says
which. You can start it with the
template's badges, with just one of them, or filled with one of your sets (on the
template's badge spots, scattered, or in rows). In the editor:

- **Selecting**: click; Ctrl+click adds to the selection; drag on empty space to
  box-select; Ctrl+A selects everything; Esc clears. Alt+click picks the obstacle under
  a badge.
- **Changing**: drag to move, mouse wheel to resize, Shift + wheel to turn, arrow keys to
  nudge (Shift: 10 px), Ctrl+D to duplicate, Delete to remove. Right-click for more. The
  *Selected* panel sets X, Y, width, height and turn exactly; with several things
  selected, width, height and turn apply to all of them.
- **Undo and redo**: Ctrl+Z / Ctrl+Y, or the buttons. That's up to 200 steps, including
  background, icon, arm and colour changes.
- The white rectangle is the top screen. Things outside it (often ramps and walls) are
  part of the machine too.
- Badges sitting on a turntable or hook take it along when you move them, and moving a
  turntable takes its badges along.
- **Obstacles**: tick *Move obstacles too* to select and edit the machine's fixed objects
  and attachments. The *Obstacles* tab lists every fixed object and attachment in the
  archive, with its collision shape in red. *Add to machine* puts one in the middle, with
  the size and physics values Nintendo gave it. Invisible walls show as dashed outlines.
- **Fill with badges...**: puts a set's badges (or the badges picked in the list) on the
  machine's own badge spots, scattered over the free places, or in rows. Badges are made
  smaller when that's the only way they fit. Badges held by turntables keep their spots.
- **Collision shapes** draws every object's physics outline.
- **Check physics** runs the machine for three seconds and replays it. It reports badges
  that overlap an obstacle, get pushed away or leave the screen, and circles them in red.
  *Keep the settled positions* takes the result (undoable).
- *Badges to add*: select badges (yours or Nintendo's; use *Find*), then *Add to
  machine*. *Swap for the palette's badge* replaces the selected ones in place.
- **Look and arm** tab:
  - **Background**: any of Nintendo's (*Browse Nintendo's backgrounds...* shows all 371 as
    pictures), or your own picture. Your picture is cropped to the top screen (404×244),
    and the cabinet trim comes from the Nintendo background picked before.
  - **Icon** in the arcade hall: made from the machine's badges on a colour you choose,
    your own picture, or the template's.
    - *Arrange badges...* picks which of the machine's badges are in it (1 to 4) and where.
      Drag a badge to move it, scroll over it to resize it, and use the arrow keys to nudge
      it. You can also type its centre and size.
    - Badges can run off the edge, like on most of Nintendo's icons. The dimmed border
      shows what gets cut off, and Nintendo's icon for the template is shown next to it
      for comparison.
    - *Start from* gives layouts like Nintendo's for 1 to 4 badges. *Grid (automatic)*
      goes back to the first four badges in a grid.
  - **Arm**: 0 standard claw, 1 hammer arm, **2 half-claw**, 3 stick arm, 4 bomb arm.
    - 1, 2 and 4 were checked on a 3DS.
    - 3 is the stick arm because it's the only other number Nintendo's machines use.
    - The half-claw is in the game, but no Nintendo machine uses it: it pushes the badges
      away from itself.

    *Make an arm test week...* makes an "Arm test 3" copy of the machine and a week of
    just that, to confirm the stick arm.
  - **Machine colour**: probably the cabinet frame's colour (a guess).
- Limits: 20 badges, 20 attachments and 20 fixed objects, and 20 different kinds of
  each. Warnings point out badges off the screen or piled on top of each other.

**Custom weeks tab.** Add your machines and Nintendo's, choose how many per series are on the
floor each day, and *Preview the game date*. Then:
- **Build and serve** does three things:
  - Saves the week as one of the server's custom weeks
    (`spotpass-letter/out/weeks/<name>.boss` and `.json`, like the *Build a week* tab),
    so the Serve a week tab lists it.
  - Updates allbadge with your content, if it changed (below).
  - Serves the week with the server's `serve.py week <name>`, which moves it to the game
    date, gives it a new SpotPass ID, puts it live, and keeps it on the current date as
    days pass.

  The server sends the new files by itself. Fully close and reopen Badge Arcade on the 3DS.
- **Save to the server's weeks** only saves it (serve it later from the Serve a week tab).
  **Export .boss file...** saves it anywhere.
- **Update allbadge now** and **Put Nintendo's allbadge back** (below).
- **Week extras...** changes things the week carries besides machines:
  - **Hall pictures**: the ads on the hall's board, the start-up pictures and the
    campaign pictures (13 in the base week). Pick one and *Replace with my picture*. It's
    cropped to the picture's area (320×156 for hall ads, 320×240 or 256×192 for the
    others).
  - **Bunny's lines**: all 331 of Arcade Bunny's lines (hall ads, her own lines, gallery
    comments, everyday lines), searchable. Codes in a line (colours, pauses, things the
    game fills in) show as ⟨1⟩, ⟨2⟩ ...: keep them where they are. The window warns when a
    line is longer than any of Nintendo's.
  - **Gallery**: the Miiverse gallery's 20 posts. Replace a post's picture (320×240),
    the poster's Mii picture (128×128; a transparent background looks best) or name (up
    to 10 characters).

  Nintendo's versions stay underneath, and *Reset* brings one back. The changes are saved
  with the week and go out with it.

## What the helper builds, and how

Badge Arcade's weekly SpotPass file (`data_v131.dat`) is a SARC archive with a schedule and
one `PrizeCollection` archive of machines, badges and their parts. A new week starts from
Nintendo's Dec 29, 2022 week. The helper swaps in the chosen machines with everything they
use, adds every custom name to `PrizeCollection.xml` (the game's registry), and writes the
day-by-day schedule.

The file formats were worked out from the archived data. Every archived file of each type
is rebuilt byte for byte by the helper (`tests/test_archive.py`).

| File | What | Layout |
|---|---|---|
| `pc/rt/Pr/*.prb` (PRBS) | badge | ID, name, category, 16 titles; the HOME Menu picture (64×64 RGB565 + A4, 32×32 too, one per tile for big badges); the machine texture (128×128 ETC1A4); the shadow (128×128 L8); up to 8 convex collision shapes of up to 8 corners |
| `pc/ci/*.cib` (CIBS) | machine | ID, name, background, icon, 16 titles; tuning at 0xC0 (colour as 3 floats at 0xC8, arm at 0xD4); name tables for badges, attachments and fixed objects; 0x60-byte placements (index, scale x/y, clockwise turn in degrees, x, y on the 400×240 top screen with y pointing down, physics); 32-byte joints (kind 0: attachment holds badge, kind 1: attachment holds attachment; the anchor point) |
| `pc/rt/FO/*.fob`, `pc/rt/At/*.atb` | fixed object, attachment | name, texture size, ETC1A4 texture (attachments also have a shadow), then collision polygons like a badge's, in texture pixels |
| `pc/rt/Ca/*.cab` (CABS) | category | ID, name, titles, 64×64 icon; (badges in book, groups, book ID) |
| `pc/rt/CI/*.icb` (ICBS) | hall icon | 64×64 RGB565 |
| `pc/rt/Cr/*.crb` (CRBS) | background | 512×256 ETC1: cabinet trim on the left, the top-screen picture at (108, 0)–(512, 244) |

Custom badges follow recipes fitted to Nintendo's badges:
- **Machine texture**: the picture fitted into 118×118, centred, with a white rim of
  radius 5 behind it. This matched Nintendo's textures to within 0.8% of pixels.
- **Shadow**: a Gaussian blur (radius 4) of that texture's outline, with a mean
  difference of 1.3/255 from Nintendo's.
- **Collision shapes**: the outline split into convex pieces (ear clipping, then
  Hertel–Mehlhorn merging). They cover about 98% of the badge.

Retired badges have only their HOME Menu picture in the archive. They get the same
treatment when you put them in a machine.

**The physics check** rebuilds a machine in pymunk (Chipmunk2D) from these polygons:
- Badges are free bodies.
- Fixed objects and attachments are solid.
- Badges held by attachments stay put.

Two things about the game were settled by running 60 of Nintendo's machines (376 badges)
through it:
- **Turns are clockwise on screen.** That way only 6 of Nintendo's badges touch an
  obstacle and none get pushed; the other way round, 73 would overlap.
- **A machine is a table seen from above, without gravity.** With gravity pulling down,
  210 of those badges would move and 73 fall out; without it, none move.

Friction and damping are guesses, so treat the check as a guide rather than an exact
replay of the game.

**IDs.** New badges, machines and categories get IDs no Nintendo file uses, and each keeps
its ID for good, so rebuilding a week never changes something the 3DS has already saved:
- **Machines** start at 4,600. Nintendo's machine IDs are one counter in release order
  (133 to 4,498), so yours carry on from there. Versions before this one used 60,000 and
  up; those machines are moved when the archive is read.
- **Badges** start at 90,000,000 and **categories** at 7,000, both inside Nintendo's
  ranges.

**Sets in the collection.** Badge Arcade's collection groups badges by set, a set being
the machine they come from. Each book counts its badges and its sets: Pokémon has 175
sets, one per Pokémon machine. A machine's badge list (its catalogue) is what the
collection's top screen shows, with the badges you have and the ones you don't.

So the helper does the same for yours:
- Machines are named after their set, the way Nintendo's are named after their book
  (`Amiibo_000` ... in `AmiiboBook`): `CuPortal2_000` in `CuPortal2Book`. A machine gets
  its name from the set most of its badges belong to, when you save it (and when the
  archive is read), and your weeks follow the new name.
- Names stay within 15 characters. Nintendo's longest are 11 for a category
  (`AnimalItem2`), 12 for a book and 13 for a machine. A machine called
  `CuRetroConsole_000` (18) didn't load, while the 13-character `CuPortal2_000` did.
  - So a set's code is at most 9 characters: `Cu` + 9 + `Book` is 15, and so is
    `Cu` + 9 + `_000`.
  - Sets made by older versions are shortened when the archive is read, and their badges are
    renamed to match (`Pr_CuRetroCons_GB`). Their titles in the game don't change.
- The catalogue entries (the set's display) carry the values every one of Nintendo's
  6,995 has.
- A set's book counts the badges that are on your machines, and the machines they're on.
- allbadge only gets badges that are on a machine; a badge on none of your machines stays
  out until it is.

**allbadge.** `allbadge_v131.dat.boss` holds every badge, machine, icon, background and
category the game knows (Nintendo's: 5,809 badges, 888 machines). The game keeps what's in
it; its collection shows badges from it after their week is over. So the helper adds your
content to it too:
- every custom badge;
- each set's category and book;
- every custom machine with its own icon and background.

Details:
- Nintendo's file is kept as `other/allbadge_v131-nintendo.enc`, and the helper builds
  from that.
- The new allbadge goes live under a new SpotPass ID, only when your content changed,
  because the 3DS downloads all 43 MB again.
- *Put Nintendo's allbadge back* serves Nintendo's again under a new ID.

**Week extras.** The week file also carries:
- `talkpic/*.sarc`: the hall pictures. `xml/talkpic/TalkPic.xml` gives each one's
  format, texture size and visible area, and that area sits in the middle of the texture
  (checked on all 13 of Nintendo's).
- `message/boss_USA/USen/boss/<slot>/BossText.msbt`: Bunny's lines, a standard MSBT file.
  The helper rewrites its text section and keeps everything else; unchanged, it's rebuilt
  byte for byte.
- `post/*.sarc`: gallery posts, each with `post.xml`, `Image.jpg` and `Mii.Etc1_a4`.

**SpotPass IDs.** `serve.py` gives weeks their IDs from `spotpass-letter/serve_state.json`,
and allbadge takes the next one from the same counter, so every file that goes live has a
new, higher ID. The helper also keeps its own count in `helper/helper_state.json`, with what
it last served.

## Files

| Path | What |
|---|---|
| `bahelper/` | Everything but the window: `boss.py` (containers), `sarc.py`, `yaz0.py`, `textures.py` (RGB565, A4, ETC1/ETC1A4), `formats.py` (the files above), `shapes.py`, `makers.py` (pictures to assets), `layout.py` (filling machines), `physics.py` (the physics check), `archive.py`, `workspace.py`, `week.py` (building weeks), `msbt.py` (Bunny's lines), `extras.py` (hall pictures, lines, gallery), `allbadge.py`, `titles.py` (programs a badge opens), `serving.py` (through the server), `importer.py` (from the standalone helper) |
| `gui/` | The tabs, and their part of the Setup tab (`setup_tab.py`); `app.py` holds what they share, and the Machine editor's dialogs are in `machine_dialogs.py` |
| `workspace/` | Your sets, badges, machines, weeks, pictures and caches (JSON + PNG); updates never touch it |
| `helper_settings.json`, `helper_state.json` | The Setup tab's settings for these tabs, and the SpotPass count |
| `tests/` | `python -m pytest tests` (the archive tests need boot9.bin and `other/`) |

The SpotPass code (containers, SARC, Yaz0, schedules, redating) is adapted from the
[`spotpass-letter`](../spotpass-letter/README.md) tools. Like the rest of the project, it's
licensed under the GNU AGPL v3.

## Troubleshooting

- **A machine doesn't appear.** Check the Setup tab's checklist, and that the server (and
  its proxy or hotspot) is running. The Custom weeks tab and the server's log
  show which SpotPass ID went out. Fully close Badge Arcade, don't just go back to the
  HOME Menu.
- **My badges don't show in the collection's previews.** Versions before this one wrote
  the badge settings at 0xA4-0xE0 8 bytes short, which left the texture scale at 0xD0 at
  0 (invisible). Fixed; build and serve again so the badges and allbadge are rebuilt.
- **My badges show up one by one in the collection, without their set.** Versions before
  this one gave machines IDs far past Nintendo's, counted a set's machines wrongly, and put
  badges that aren't on any machine into allbadge. All three are fixed. Build and serve
  again: that sends a new allbadge with your sets. Put each badge on a machine so it
  belongs to a set.
- **A badge I deleted from a machine still shows up.** Fixed in this version: the
  machine's badge list used to keep it. Build and serve the week again.
- **A machine won't load in the game, or its week won't.** Names longer than Nintendo's
  can stop it: older versions allowed set codes up to 16 characters, giving machines like
  `CuRetroConsole_000`. Reopen the manager to shorten them, then build and serve again.
  The helper now refuses to build a machine whose name is too long.
- **A badge falls through things or behaves oddly.** Try *Simple collision shape* on the
  Badges tab, or a slightly larger size in the editor.
- **"Too many machines".** The game crashes when a week puts more than 999 machines on the
  floor. The helper spreads them over the week instead.

Not affiliated with or endorsed by Nintendo. No Nintendo files are included: you provide
your own archived SpotPass data and console key.

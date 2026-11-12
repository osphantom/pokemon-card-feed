# Micro Center Pokémon tracker

Watches **one Micro Center store** (default: Parkville, MD — store `125`) for sealed
English Pokémon TCG, prices each hit against the feed's resale data, and posts in-stock
deals (and new arrivals, so you catch releases) to a Discord channel.

Micro Center shows a Cloudflare bot check to anything automated, so this reads the catalog
through the **`mcenter` CLI with a session you log in yourself** — nothing here gets past
that check. Because the session lives in your browser, this runs on **your PC**, not in
GitHub Actions.

## One-time setup

1. Install the tool and log in (opens your browser; you solve the check):
   ```bash
   pip install microcenter-cli
   mcenter session interactive
   ```
2. Confirm your store id (Parkville, MD is `125`; change `config/microcenter.json` if yours differs):
   ```bash
   mcenter stores find baltimore
   ```
3. Make a Discord channel (e.g. `#micro-center`), add a **webhook** in it (Channel settings →
   Integrations → Webhooks → New Webhook → Copy URL), and save it once:
   ```bash
   setx MICROCENTER_WEBHOOK "https://discord.com/api/webhooks/..."
   ```
   (Or point it at `#drop-pings` if you'd rather it land with your other deals.)

## Run it

From this folder:
```bash
python -m tools.microcenter          # posts to Discord
python -m tools.microcenter --dry    # prints what it found, posts nothing
```
The first run just records what's in stock; after that it only posts **new** deals and
**new** arrivals, so it won't repeat itself.

## Keep it running

Point Windows Task Scheduler at it every 30–60 minutes while your PC is on, the same way
your Micro Center bundle refresh is set up. When the Micro Center session expires, runs
stop with a clear message and the fix is always: `mcenter session interactive` again.

## Notes

- Stock shown is from Micro Center's site, not a live shelf count — **call the store with
  the item before you drive or line up.**
- Profit assumes you buy at the Micro Center price and sell at the current eBay/TCGplayer
  market, after fees. It only prices items it can confidently match; others still show up
  as new arrivals with their Micro Center price and a link.
- The per-machine `data/microcenter_state.json` (what it's already seen) is git-ignored.

# Pokémon Sealed Feed

An auto-updating feed for reselling sealed English Pokémon cards: drop windows, release dates, preorders, restock news, and eBay profit estimates.

- **Dashboard:** https://osphantom.github.io/pokemon-card-feed/
- **Playbook:** https://osphantom.github.io/pokemon-card-feed/playbook.html
- **RSS:** https://osphantom.github.io/pokemon-card-feed/feed.xml
- **Calendar:** https://osphantom.github.io/pokemon-card-feed/calendar.ics

GitHub Actions rebuilds it on a 15-minute schedule, but GitHub can start those runs hours late. To keep it on time, a Windows Task Scheduler task named **Pokemon Feed Refresh** on the owner's PC runs `tools/trigger_refresh.py` every 15 minutes while the PC is on and signed in. GitHub's schedule remains the backup.

It reads public feeds and public price data, identifies itself honestly, and skips any source that refuses it. It doesn't watch store pages, join queues, or check anything out.

## Set up Discord

These steps need your own login, so they're yours to do. It takes about five minutes.

1. In Discord, press **+** in the server list, choose **Create My Own**, then **For me and my friends**, and name the server.
2. Open **User Settings > Advanced** and turn on **Developer Mode**. Right-click your server icon and choose **Copy Server ID**.
3. Go to https://discord.com/developers/applications and choose **New Application**. Copy the **Application ID** from General Information.
4. Open the **Bot** tab, press **Reset Token**, and copy the token. Treat it like a password.
5. Open this link with your Application ID filled in, pick your server, and authorize:

   `https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot&permissions=537086992`

6. Save the two values as encrypted secrets. Each command asks you to paste the value:

   ```bash
   gh secret set DISCORD_BOT_TOKEN --repo osphantom/pokemon-card-feed
   ```

   ```bash
   gh secret set DISCORD_GUILD_ID --repo osphantom/pokemon-card-feed
   ```

7. Build the channels:

   ```bash
   gh workflow run setup-discord.yml --repo osphantom/pokemon-card-feed
   ```

The bot builds about 20 read-only channels: a welcome guide, a live list of the next drops, one channel that pings you, a channel for each store, and news. The next refresh starts posting.

`@everyone` is only used in `#drop-pings`, and only for drop reminders and fresh alerts from real-time sources. `#best-buy` is private and pings only the server owner.

## Add eBay asking prices (optional)

The Flip List works without this. Adding keys fills in the eBay asks column. It uses about 1,100 of eBay's default 5,000 daily calls.

1. Create a Production keyset at https://developer.ebay.com/.
2. This project stores no eBay user data, so request the exemption from Marketplace Account Deletion notifications. eBay can disable a new keyset that hasn't dealt with this.
3. Save the keys:

   ```bash
   gh secret set EBAY_CLIENT_ID --repo osphantom/pokemon-card-feed
   ```

   ```bash
   gh secret set EBAY_CLIENT_SECRET --repo osphantom/pokemon-card-feed
   ```

## Add Best Buy stock alerts (optional)

This reads Best Buy's official [Developer API](https://developer.bestbuy.com/) and posts to a private `#best-buy` channel when sealed cards come back online or show up in a Maryland store. Change the area in `config/bestbuy.json`.

1. Get a key at https://developer.bestbuy.com/.
2. Save it:

   ```bash
   gh secret set BESTBUY_API_KEY --repo osphantom/pokemon-card-feed
   ```

Best Buy's [terms](https://developer.bestbuy.com/legal) set the rules this follows: credit Best Buy and show its logo, keep its data no longer than 72 hours, and don't use the key for anyone else. This public repo saves only one-way fingerprints of what's in stock, alerts delete themselves after two days, and Best Buy's data stays out of the public site and RSS feed. Keep the channel private and don't reuse the key for other people.

## Update the calendar by hand

- `data/events.json`: dated releases and preorders
- `data/windows.json`: weekly drop windows
- `data/retail_watch.json`: prices from the latest store drawing
- `config/filters.json`: what gets filtered out of the feed

## Run it locally

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

```bash
.venv/Scripts/python -m unittest discover -s tests
```

```bash
.venv/Scripts/python -m pipeline.run
```

The site is written to `site/`.

## Notes

Fan-made and for personal use. Not affiliated with Pokémon, Nintendo, or any retailer. General information only, not financial, tax, or legal advice.

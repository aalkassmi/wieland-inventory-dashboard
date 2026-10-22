# Wieland Regional Inventory Performance

Five-store rollup of the Chattanooga inventory dashboard for the area VP: Chattanooga (955), Knoxville (966), La Crosse (967), Roberts (979) and Eau Claire (959).

- `index.html`: the page. Location dropdown (All locations or one store), Type and Status filters, search, sortable columns.
- `data/inventory.json`: the data snapshot. A refresh replaces only this file.

Sources: Mixpanel project 3822144 for views, ad share, leads and calls (90-day window); Motive for live listings and price; days on lot from the listing's inventory age in Mixpanel.

Hosted on Cloudflare Workers, auto-deploying from `main`.

## Weekly refresh

A scheduled task runs every Monday morning. It pulls Motive listings and Mixpanel activity for the five stores into `raw/` (not committed), runs `python3 scripts/build_inventory.py --end <Sunday>`, and pushes only `data/inventory.json`. The script refuses to write if a store comes back with no listings or no views, or total views drop below 25% of the previous file, so the site keeps last week's data instead of going blank.

## Jura Connect

Home Assistant integration for WiFi-connected JURA coffee machines (S8, EB,
TT237W series). Built on the reverse-engineered
[`jura-connect`](https://github.com/makefu/jura-connect) library — talks
directly to the machine's WiFi dongle on TCP/51515. No cloud, no vendor
account.

**Features:**
- Auto-discovery of machines on the local network
- Per-machine profiles for 88 known JURA models (correct alert + brew names)
- Status sensor + binary sensors for every alert (water, beans, drip tray,
  milk warning, …)
- Per-recipe brew counters (espresso, coffee, cappuccino, …) plus a
  lifetime total
- Maintenance counters and percent-to-next indicators (cleaning, descale,
  filter, cappu rinse)
- Machine settings as dropdowns / sliders (language, units, auto-off,
  water hardness, milk rinsing, …) with profile-driven validation
- Services for brewing, cleaning, descaling, filter-change, lock/unlock,
  restart, power-off — all destructive operations gated explicitly
- Entities keep their last value when the machine is offline; a dedicated
  connectivity sensor reports reachability

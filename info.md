## Jura Coffee

Home Assistant integration for WiFi-connected JURA coffee machines (S8, EB, TT237W series).

Built on the reverse-engineered [`jura-connect`](https://github.com/makefu/jura-connect) library:
talks directly to the machine's WiFi dongle on TCP/51515 — no cloud, no vendor account.

**Features:**
- Auto-discovery of machines on the local network
- Active alerts as binary sensors (water low, beans empty, drip tray, milk warning, …)
- Maintenance counters and percent indicators (cleaning / decalc / filter / cappu)
- Named services to trigger brewing, cleaning, decalcification, screen lock, power off, …
- All destructive operations are gated explicitly per service

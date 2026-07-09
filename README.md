# CA Tools.extension

Unified pyRevit extension — all CA Tools consolidated into a single **CA Tools** tab.

## Panels & buttons
1. **Grids** — Auto Dim Grids, Grid Bubbles
2. **Families** — Export Families, Family Browser, Load Family
3. **Parallel MEP** — Parallel/Stack Conduits, Ducts, Pipes, Trays
4. **Auto Offset** — CableTray, Conduit, Duct, Pipe bends (CoolOffset)
5. **Export** — Bulk Exp (PDF/DWG bulk exporter)
6. **Coordination** — Clash Navigator
7. **About** — developer info

## Install
Copy `CA Tools.extension` into your pyRevit extensions folder (or add its parent
via *pyRevit > Settings > Custom Extension Directories*), then Reload.

Shared engines live in `lib/` (`bend_core.py`, `parallel_mep.py`,
`ParallelMEPWindow.xaml`) and are on the extension path automatically.

Author: Chulan Adasuriya

# Three-minute walkthrough

Use an authorized local snapshot if available. Otherwise explicitly choose the engineering fixture and say: **“This is an engineering fixture, not market data. I am demonstrating the workflow, not claiming live prices or trading performance.”**

| Time | Action | Suggested narration |
|---|---|---|
| 0:00–0:30 | Open the desk and load a local file, or explicitly select the labeled fixture. | “The first dependency is a usable, authorized snapshot. I keep contract months, timestamps, source, and data status visible before looking at a price.” |
| 0:30–1:00 | Inspect the selected CSO, futures legs, and volatility manager. | “A calendar-spread option depends on both futures months and their joint behavior. Market is the imported reference; Draft is editable; Active is the input used for the desk calculation.” |
| 1:00–1:35 | Apply a small normal-volatility shift. Preview without applying; inspect quote and hedge comparisons. | “Active has not changed. Here are the value change, bid and ask changes, and hedge lot changes from one frozen market. The quote explanation shows inventory, stress penalties, and crossing costs.” |
| 1:35–2:05 | Apply the valid draft and inspect the bundle/version identifiers. | “Applying a valid draft activates one coherent result. If validation fails, the previous active result remains visible and the error is explicit.” |
| 2:05–2:40 | Step the local replay. Inspect stale status, hedge counts, and residual risk. | “Replay advances stored snapshots. It is not a live feed. These are discrete hedge contracts, so residual exposure matters alongside the model price.” |
| 2:40–3:00 | Open model/data details and the connection panel. | “The connection remains pending until an authorized feed is configured. The next validation is to compare licensed market observations and realized hedge behavior with these model outputs.” |

Keep the discussion on one concrete change. Do not infer executable bid/ask from settlements, describe a scenario as a realized P&L, or call an engineering fixture a backtest.

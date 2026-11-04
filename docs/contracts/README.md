# Interface contracts

The boundaries between layers. Code is authoritative for exact types; these files say what each
layer may rely on and must not do. **Changing a contract needs an ADR and a `PROJECT_STATE.md`
update in the same change** (ADR 0012).

```
Garmin Personal API
      │  ingestion.md: GarminClient, Syncer, CLI, exit codes
      ▼
Raw layer ──normalize──▶ Normalized layer         storage-schema.md
(raw_payload, blobs)     (activity, daily_summary, ...)
                               │  analytics reads; never raw (M6, accepted)
                               ▼
                         Derived tables ──▶ agent schema ──▶ tools ──▶ model
                                            agent-layer.md (decided in part)
```

| Contract | Boundary | State |
|---|---|---|
| [ingestion.md](ingestion.md) | Garmin → client → sync → store; CLI | Implemented |
| [storage-schema.md](storage-schema.md) | Tables, keys and invariants | Implemented |
| [agent-layer.md](agent-layer.md) | Analytics → agents → plans | Decided in part (ADR 0013, M6f), not built |

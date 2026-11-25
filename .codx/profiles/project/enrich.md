## Local model host hardware and CPU-only policy

## Hardware (measured, not guessed)
- Host CPU: AMD EPYC 7502P — 32C/64T Zen 2, **AVX2 only** (no AVX-512, no VNNI, no AMX), single socket.
- RAM: 264 GB DDR4-3200, 8 channels (theoretical ~204 GB/s).
- **No GPU** anywhere in the stack.
- Measured memory bandwidth (stdlib probe `tmp/membw_probe.py` inside `codx-junior-api`): 36.9 GB/s @1 thread, 115.7 @8, 190.2 @16. Use **~120 GB/s effective** for sizing.
- Decode ceiling: `tok/s ≈ 120e9 / bytes_touched_per_token`, where bytes/token = *active* params × bits/8 (MoE counts only active experts).

## Policy
- Interactive agent chat / code review / vision: keep on cloud (`deepseek-flash`). CPU TTFT on the large codx-junior prompts is tens of seconds to minutes.
- Local is for: embeddings, reranker, guardrail/classification, short-output batch jobs (commit msg, title, wiki page from small diff), STT.
- MoE with few active params (30B-A3B class) is the CPU strategy — 264 GB RAM holds it, active-expert bytes set the speed.
- Use `-t 32` (not 64: SMT hurts ggml); engine = llama.cpp / LocalAI, **not** vLLM-CPU.

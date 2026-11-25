# Local models on CPU-only hardware — strategy

Status: proposal. Covers the host this stack actually runs on, the models worth running,
the workloads that should stay on CPU vs. cloud, and the hard limits.

## 1. What the hardware gives us

| Property | Value | Consequence |
| --- | --- | --- |
| CPU | AMD EPYC 7502P, 32C/64T, Zen 2 | high core count, low clocks per core |
| ISA | AVX2 only (no AVX-512 / VNNI / AMX) | no fast int8 math; k-quants are the right quant family |
| RAM | 264 GB DDR4-3200, 8 channels (~204 GB/s theoretical) | we can *hold* very large models; we cannot *stream* them fast |
| GPU | none | no vLLM/CUDA paths, no batching tricks |
| Measured bandwidth | 36.9 GB/s (1 thread), 115.7 (8), 190.2 (16) | use **~120 GB/s effective** for planning |

Everything about local inference here is decided by one equation:

```
decode tok/s  ≈  effective_bandwidth / bytes_touched_per_token
bytes/token   ≈  ACTIVE_params × bits_per_weight / 8
```

Prefill (reading the prompt) is **compute**-bound, not bandwidth-bound, and it is the real wall
on this box: expect roughly 100–400 tok/s of prompt processing, i.e. a 20K-token prompt costs
60–200 s before the first output token. Nothing in the tuning toolbox fixes that; only shorter
prompts do.

## 2. The core strategic insight: buy speed with MoE, not with small models

Because bandwidth is the limit and RAM is abundant, **sparse (MoE) models win decisively here**.
A 30B-total / 3B-active model streams ~2 GB per token, not ~18 GB.

| Model class | Total size (Q4) | Bytes read / token | Ceiling @120 GB/s | Realistic |
| --- | --- | --- | --- | --- |
| BGE-M3 / Qwen3-Embedding-0.6B | 1.1–2.3 GB | ~0.6 GB | — (prefill-bound) | 20–60 chunks/s |
| Qwen3-4B (dense) | 2.5 GB | 2.5 GB | 48 t/s | 30–42 t/s |
| Llama-3.1/Qwen3-8B (dense) | 4.9 GB | 4.9 GB | 24 t/s | 14–18 t/s |
| Qwen3-14B (dense) | 9.0 GB | 9.0 GB | 13 t/s | 7–9 t/s |
| **Qwen3-30B-A3B (MoE)** | 18 GB | ~1.9 GB | 63 t/s | **25–40 t/s** |
| **GPT-OSS-20B (MoE, MXFP4)** | 12 GB | ~2.1 GB | 57 t/s | **22–35 t/s** |
| Qwen3-32B (dense) | 19.5 GB | 19.5 GB | 6 t/s | 3–4 t/s |

Read it as: a 30B MoE at Q4 is **2–3× faster and far more capable** than a dense 8B on this box —
and it fits comfortably in 264 GB of RAM (page cache keeps the GGUF hot between requests).

## 3. Tiering: what runs local, what stays cloud

### Tier A — always local (clear win)
- **Embeddings** (`embeddings_model` slot): BGE-M3 (multilingual, 8K ctx) or Qwen3-Embedding-0.6B.
  Batch workload, bandwidth-friendly, runs while nobody waits.
- **Reranker**: BGE-reranker-v2-m3 (int8) over the top ~20 RAG candidates. Cheap, large quality gain.
- **Guardrail / classification / routing**: Qwen3-1.7B or Qwen3-4B Q4_K_M. Runs on every message,
  so it must stay in the 1–4B range.
- **STT**: faster-whisper `small`/`base` int8. Whisper on CPU is genuinely good.

### Tier B — local for batch, cloud for interactive (same model, different trigger)
- Wiki page generation from a small diff (`wiki_model`).
- Commit messages, PR titles, changelogs, test-name suggestions.
- RAG answer synthesis when `top_k` is small (~2K context).
- Use a 30B-A3B here; run it in a background queue, not on the click path.

### Tier C — keep in the cloud
- `llm_model` / agent chat with tool loops (each iteration re-sends a large system + tool catalogue).
- Code review and full-file/diff analysis (long inputs = prefill wall).
- Vision, image generation (CPU SDXL is minutes per image — not worth it).

Rule of thumb for the split: **input > ~8K tokens → cloud. Multi-step tool loop → cloud.
Single-shot with short output → local.**

## 4. Engine configuration

Use llama.cpp (`llama-server`) behind LocalAI / llama-swap. Do **not** fight `VLLM_TARGET_DEVICE=CPU`:
vLLM's CPU path wants AVX-512 and still loses to llama.cpp for single-user decode.

```
llama-server -m <model>.gguf \
  -t 32 -tb 32            # 32 physical cores, NOT 64 — SMT slows ggml down
  -fa                     # flash attention
  -c 16384 --cache-reuse 256   # KV reuse across turns: the single best TTFT mitigation
  -b 2048 -ub 512
  -ctk q8_0 -ctv q8_0     # 2x smaller KV; quality loss negligible
  --no-mmap               # only if RAM is free; otherwise mmap + page cache is faster
```

Quant choice: **Q4_K_M** (or Q5_K_M when RAM allows). Avoid IQ2/IQ3 quants on AVX2 — they add
dequantisation work per byte and can be *slower* than Q4 despite being smaller.

## 5. Limitations to accept

1. **TTFT is the killer, not throughput.** 20K-token prompts cost minutes. Only prompt trimming,
   retrieval top-k reduction, prompt caching and smaller tool catalogues help.
2. **No concurrency.** N users share ~120 GB/s: two users on an 8B model get ~9 t/s each.
   Local here means one background job at a time.
3. **No int8 acceleration.** AVX2-only means roughly 2–4× slower than a same-core AVX-512 Zen 4.
4. **Agent loops are unusable locally** — k iterations × long prompt = k × minutes.
5. **Embedding model changes force a full reindex** of the Milvus collection. Cheap to plan, expensive
   to forget.
6. **Power/thermals**: 180 W sustained under load. The economics only work for *batch* work, where
   wall-clock latency doesn't matter.

## 6. Recommended target configuration

| Slot | Now | Proposed |
| --- | --- | --- |
| `llm_model` | `deepseek-flash` | keep (cloud) |
| `rag_model` | `deepseek-flash` | keep, or 30B-A3B if data must stay on-host |
| `wiki_model` | Nemotron 30B-A3B IQ2_M | **Qwen3-30B-A3B Q4_K_M**, batch queue |
| `guardrail_model` | `knowledge` | Qwen3-4B Q4_K_M (local, ~35 t/s) |
| `embeddings_model` | (chat model id) | **BGE-M3 local** + full reindex |

## 7. First steps, in order

1. Serve one 30B-A3B Q4_K_M behind LocalAI/llama-server, register it in `ai_models.json`.
2. Measure the real baseline: decode tok/s, prefill tok/s (pp512), TTFT at 2K / 8K / 20K.
3. Move embeddings + reranker local, reindex, compare RAG quality.
4. Only then tune: threads (16/24/32), quant (Q4_K_M vs Q5_K_M), KV quant, batch size — one lever at a
   time with interleaved controls, medians of ≥6 samples.

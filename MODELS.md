# AI Model Guide

How to choose, install, and size the local AI model for the bridge, with measured
figures from a 2 GB Raspberry Pi 4 and an 8 GB Raspberry Pi 5.

The short version:

- **Match the model to the host's RAM.** Peak resident memory is roughly the
  download size plus 40%.
- **Avoid reasoning models** (`qwen3`, `qwen3.5`, `minicpm5`, `ling-3.0`,
  `spark-x2.5`) unless the client can send `think: false`. They spend the whole
  `AI_MAX_TOKENS` budget on a hidden reasoning block and can return an empty reply.
- **`qwen2.5:0.5b` is the safe default** on the documented 2 GB minimum, and
  `lfm2.5-1.2b` is the best measured choice on 8 GB.
- **Cap the Ollama service** so a model that does not fit fails instead of
  thrashing the node - see
  [Protecting a memory-constrained host](#protecting-a-memory-constrained-host).

Contents:

1. [Local model (default)](#local-model-default) - install it and pick by RAM
2. [Measured model benchmark](#measured-model-benchmark) - results on both hosts
3. [Model benchmarking harness](#model-benchmarking-harness) - re-measure your own host
4. [Models that are not in the Ollama registry](#models-that-are-not-in-the-ollama-registry) - GGUF imports
5. [Protecting a memory-constrained host](#protecting-a-memory-constrained-host) - hard limits

## Local model (default)

The bridge defaults to a small model served by [Ollama](https://ollama.com/)
on the same Pi. Install it and pull the model:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:0.5b
```

Pick a model sized for the host's RAM. The download sizes below were read from
the Ollama registry and the memory guidance follows the measured resident
footprint (about 1.4x the download size). The Pi 4B is a proof-of-concept
baseline; production hosts should use the 4–8 GB guidance and the
[measured results](#measured-model-benchmark) below:

| Model | Download | Minimum practical RAM | Use |
|-------|---------:|----------------------:|-----|
| `lfm2.5-230m` † | 0.25 GB | 2 GB+ | Fastest and lightest measured; GGUF import required |
| `lfm2.5-350m` † | 0.38 GB | 2 GB+ | Still faster than `qwen2.5:0.5b` at less memory; GGUF import required |
| `qwen2.5:0.5b` | 0.40 GB | 2 GB+ | Best registry-only choice for a 2 GB host |
| `qwen3:0.6b` | 0.52 GB | 4 GB+ | Fast, but needs `think: false` |
| `minicpm5-1b` † | 0.69 GB | 2 GB+ | 18.4 tok/s on 8 GB and fits 2 GB; needs `think: false` |
| `granite4:350m` | 0.71 GB | 2 GB+ | Survives 2 GB without swap, but slower than `qwen2.5:0.5b` |
| `lfm2.5-1.2b` † | 0.73 GB | 4 GB+ | Best speed/quality balance measured; GGUF import required |
| `llama3.2:1b-instruct-q4_K_M` | 0.81 GB | 4 GB+ | Comparison model |
| `gemma3:1b` | 0.82 GB | 4 GB+ | Fast non-thinking 1B |
| `qwen2.5:1.5b-instruct-q4_K_M` | 0.99 GB | 4 GB+ | Best latency/quality on 8 GB |
| `qwen3.5:0.8b` | 1.04 GB | 8 GB+ | Needs `think: false` |
| `qwen3:1.7b` | 1.36 GB | 8 GB+ | Higher quality; needs `think: false` |
| `qwen2.5:3b-instruct-q4_K_M` | 1.93 GB | 8 GB+ | Higher quality, ~5 tok/s |
| `granite4:micro-h` | 1.94 GB | 8 GB+ | Slowest measured (3.9 tok/s) |
| `llama3.2:3b-instruct-q4_K_M` | 2.02 GB | 8 GB+ | Higher quality, ~5 tok/s |
| `lfm2.5:8b` | 5.16 GB | 8 GB (tight) | ~1B active params, 34 s cold load |

† Not an Ollama registry tag; install it with the GGUF import in
[Models that are not in the Ollama registry](#models-that-are-not-in-the-ollama-registry).

Tag names change over time, so confirm with `ollama search <name>` before
pulling. Two tags listed here previously no longer resolve: `lfm2.5:1.2b-instruct`
(only the 8B LFM2.5 size is in the Ollama registry; the 230M, 350M and 1.2B sizes
exist as HuggingFace GGUFs) and `qwen3:1.7b-instruct-q4_K_M` (the correct tag is
`qwen3:1.7b`). Check a tag without downloading it:

```bash
curl -o /dev/null -w '%{http_code}\n' \
  'https://registry.ollama.ai/v2/library/NAME/manifests/TAG'
```

## Measured model benchmark

Both hosts below were measured with `scripts/benchmark_models.sh` using the same
system prompt, the same five prompts, a fixed seed, and a 140-token cap (mesh
replies are shorter than the 220-token default). `Load` is the cold model load,
`TTFT` is time to first token, `Wall` is the whole request, `Out` is generated
tokens, and `RSS` is peak resident inference memory sampled with every other
model unloaded. These are single-pass measurements, so treat them as sizing
guides rather than precise benchmarks.

### Raspberry Pi 5, 8 GB, microSD (recommended production host, Ollama 0.34.2)

| Model | Size GB | Load s | TTFT s | Wall s | Out tok | Tok/s | RSS MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| `lfm2.5-230m` † | 0.25 | 0.21 | 0.37 | 1.8 | 63 | 45.0 | 446 |
| `bonsai-1.7b` † (Q1_0) | 0.25 | 0.83 | 4.25 | 8.2 | 60 | 15.3 | 871 |
| `lfm2.5-350m` † | 0.38 | 0.26 | 0.53 | 2.1 | 48 | 30.0 | 581 |
| `qwen2.5:0.5b` | 0.40 | 0.53 | 1.29 | 3.6 | 64 | 28.4 | 675 |
| `qwen3:0.6b` (think off) | 0.52 | 1.69 | 2.20 | 4.3 | 46 | 23.0 | 1069 |
| `bonsai-4b` † (Q1_0) | 0.57 | 0.42 | 9.27 | 18.1 | 60 | 6.8 | 1343 |
| `minicpm5-1b` † (think off) | 0.69 | 1.83 | 2.48 | 5.1 | 48 | 18.4 | 869 |
| `granite4:350m` | 0.71 | 0.27 | 4.01 | 6.6 | 31 | 12.1 | 892 |
| `lfm2.5-1.2b` † | 0.73 | 0.41 | 1.40 | 4.7 | 51 | 15.5 | 967 |
| `llama3.2:1b-instruct-q4_K_M` | 0.81 | 2.19 | 2.99 | 7.5 | 58 | 13.0 | 1244 |
| `gemma3:1b` | 0.82 | 2.73 | 4.53 | 10.9 | 71 | 11.2 | 1262 |
| `qwen2.5:1.5b-instruct-q4_K_M` | 0.99 | 2.59 | 3.66 | 9.4 | 58 | 10.3 | 1361 |
| `qwen3.5:0.8b` (think off) | 1.04 | 4.42 | 5.55 | 12.2 | 58 | 8.8 | 1592 |
| `spark-x2.5-1.7b` † (think off) | 1.11 | 2.98 | 4.40 | 9.4 | 48 | 9.7 | 1542 |
| `qwen3:1.7b` (think off) | 1.36 | 3.95 | 5.23 | 12.4 | 57 | 8.1 | 1867 |
| `minicpm5-2b` † (think off) | 1.56 | 4.19 | 5.80 | 10.9 | 39 | 7.7 | 1787 |
| `qwen2.5:3b-instruct-q4_K_M` | 1.93 | 5.60 | 7.97 | 14.7 | 35 | 5.2 | 2366 |
| `granite4:micro-h` | 1.94 | 7.20 | 11.6 | 23.7 | 46 | 3.9 | 2486 |
| `llama3.2:3b-instruct-q4_K_M` | 2.02 | 5.67 | 8.01 | 18.6 | 58 | 5.5 | 2831 |
| `granite4:micro` | 2.10 | 5.39 | 7.92 | 17.9 | 52 | 5.3 | 2679 |
| `qwen3.5:2b` (think off) | 2.74 | 1.39 | 4.18 | 16.1 | 60 | 5.0 | 3627 |
| `gemma3:4b` | 3.34 | 10.5 | 14.3 | 30.3 | 62 | 3.9 | 4267 |
| `ling-3.0-tiny` † (think off) | 4.82 | 12.5 | 14.2 | 19.4 | 50 | 9.9 | 5071 |
| `lfm2.5:8b` (think off) | 5.16 | 34.4 | 38.4 | 54.6 | 140 | 8.7 | 5317 |
| `gemma3n:e2b` | 5.62 | 11.8 | 14.7 | 25.9 | 67 | 6.1 | 5261 |

† Imported from HuggingFace GGUFs because these models are not in the Ollama
registry (see [Models that are not in the Ollama registry](#models-that-are-not-in-the-ollama-registry)).

The LFM2.5 models are the fastest thing measured on this host by a wide margin:
2-12x the decode rate of comparable dense models, at a fraction of the memory.
That is enough to change the default recommendation below.

`lfm2.5:8b` was measured cold from microSD; the request after a fresh download
loaded in 6.5 s. It is a 5.2 GB hybrid model with ~1B active parameters, which is
why it decodes near 1B speeds despite its size, but it consistently ran to the
140-token cap instead of respecting the requested length.

Plain dense models up to `llama3.2:3b` (RSS 2.8 GB) leave comfortable headroom.
The 4B class does not: `gemma3:4b` needed 14.3 s to first token and 30 s per
reply. `lfm2.5:8b` fits (RSS 5.3 GB) but leaves only ~2.4 GB for everything else.
`gemma3n:e2b` is the worst case measured here: 6.1 tok/s at RSS 5.3 GB. With the
bridge and dashboard also resident, the host was left with almost no free RAM and
was swapping, so it is not a good fit for an 8 GB node even though its weights are
only 5.6 GB.

### Raspberry Pi 4B, 2 GB, microSD (proof of concept only, Ollama 0.34.0)

| Model | Size GB | Tok/s | Avg wall s | Outcome |
|---|---:|---:|---:|---|
| `lfm2.5-230m` † | 0.25 | 12.8 | 7.2 | 5/5 prompts, RSS 383 MB, ~1.2 GB RAM still free |
| `lfm2.5-350m` † | 0.38 | 8.6 | 8.6 | 5/5 prompts, RSS 516 MB, ~1.1 GB RAM still free |
| `qwen2.5:0.5b` | 0.40 | 8.0 | 9.7 | Reliable: 5/5 prompts answered in 3-18 s |
| `minicpm5-1b` † (think off) | 0.69 | 5.9 | 12.2 | 5/5 prompts, RSS 883 MB - fits, but slower than LFM2.5 |
| `minicpm5-1b` † (default) | 0.69 | 5.7 | 33.3 | Only 1/5 - reasoning consumed the token budget |
| `qwen3:0.6b` (default thinking) | 0.52 | 6.4 | 40.4 | Reasoned to the token cap (TTFT 18-50 s); one prompt returned an empty reply; the host rebooted |
| `granite4:350m` | 0.71 | 4.6 | 15.5 | 5/5 prompts, RSS 913 MB, zero swap, no reboot - stable but slow |
| `gemma3:1b` | 0.82 | 4.0 | 30.7 | Heavy swap; the host rebooted during the run |
| `qwen3.5:0.8b` | 1.04 | - | - | Did not complete; the host rebooted |

**The 2 GB Pi 4 stays a proof-of-concept host, but the LFM2.5 models make it
usable.** `lfm2.5-230m` decodes ~60% faster than `qwen2.5:0.5b` while using 43%
less memory (RSS 383 MB vs 675 MB), and `lfm2.5-350m` is also faster at RSS
516 MB; both answered 5/5 prompts without a reboot and left ~1.1-1.2 GB of RAM
free. Every 1B-or-larger dense model reset the board. Two separate benchmark runs
reset the board, including one with the Ollama service capped at 1.1 GB of RAM and
2 GB of swap. The resets left `ping` responsive while SSH was starved, no OOM
records were flushed for the crashed boot, and systemd had the BCM2835 hardware
watchdog armed with a 1-minute timeout - consistent with a watchdog reset rather
than a clean out-of-memory kill.

These figures come from a re-run after the staged GGUFs were moved off `/tmp`.
An earlier pass reported only ~500-600 MB free with ~420 MB swapped; that was
caused by `/tmp` being a RAM-backed tmpfs holding the 625 MB of GGUFs, not by the
model itself. Keep `/tmp` clear when measuring or running on a 2 GB host.

### What the measurements show

- **The LFM2.5 family is in a different class for this workload.** `lfm2.5-230m`
  and `lfm2.5-350m` answered every prompt in under 2.2 s on the Pi 5 (45 and 30
  tok/s) and still managed 12.9 and 8.5 tok/s on the 2 GB Pi 4, at RSS 388-581 MB.
  They are the best fit measured for a mesh assistant, but they are not in the
  Ollama registry, so they need a one-time GGUF import.
- **Reasoning blocks waste the reply budget, and reasoning is the default in most
  2026 models.** `qwen3`, `qwen3.5`, `minicpm5-1b`/`2b` and `ling-3.0-tiny` all emit a
  hidden reasoning block unless told otherwise. Measured effects: `qwen3:0.6b` spent
  49.7 s before its first visible token on the 2 GB Pi 4; `minicpm5-1b` answered
  **1 of 5** prompts by default (583 reasoning characters per reply) versus
  **5 of 5** with `think: false`; `ling-3.0-tiny` managed 2 of 5. With a small
  `AI_MAX_TOKENS` a thinking model returns an empty or truncated reply, so disable
  reasoning or pick a non-thinking model.
- **Obeying the reply limits is the real filter.** Scored against the deployed
  `agent_prompt.txt`, small models breach the contract regularly. The dominant
  failure is an empty reply from a reasoning model, followed by answers that ignore
  the supplied retrieval context or omit its source. Some models breach only the
  style rules (too many bullets, over the word budget), which wastes airtime but
  still delivers a usable answer. Per-model verdicts are in
  [Instruction compliance](#instruction-compliance) below.
- **Resident memory is roughly the model size plus 40%.** Budget the download size
  times about 1.4 against the host's RAM and leave the remainder for the OS,
  bridge, dashboard, and Kiwix. Keep the peak under about 60% of RAM.
- **Small dense models are fast and predictable.** `qwen2.5:0.5b`, `qwen3:0.6b`
  (reasoning off), and `gemma3:1b` answered every prompt in a few seconds and
  stayed inside a mesh-sized reply.
- **Sparse and hybrid models can outrun larger dense ones.** `lfm2.5:8b` decoded
  faster than `qwen2.5:3b` and `llama3.2:3b` because only ~1B parameters are
  active per token, but its cold load is 34 s and it ignored the length limit.
  The same trait does not help Granite: `granite4:micro` (non-hybrid) reached only
  5.3 tok/s against `granite4:micro-h` (hybrid) at 3.9 tok/s, so both are slow.
- **Newer is not automatically faster.** `granite4:micro-h` was the slowest
  non-thinking model measured (3.9 tok/s).

### Instruction compliance

Throughput alone does not make a model usable here. The bridge splits replies at
`REPLY_MAX_CHARS` (180 by default, roughly one Meshtastic packet each) and ships
`agent_prompt.txt`, which asks for three bullets or fewer, under 90 words, plain
text, a finished sentence, and use of the retrieved context with its source named.
A model that ignores those rules wastes airtime or posts nothing at all.

The harness scores every reply against that contract, so a run reports both speed
and whether the answer was actually usable:

| Check | Fails when |
|-------|-----------|
| `empty` | no visible content arrived - the usual symptom of reasoning eating the budget |
| `over_word_limit` | longer than the prompt's budget (90 words, or 60/45 where the prompt asks for less) |
| `too_many_bullets` | more than 3 list items |
| `markdown` | a Markdown table or code fence appears |
| `unfinished` | the reply ends on a comma, dash or connector, i.e. token-cap truncation |
| `not_grounded` | fewer than 2 terms from the supplied retrieval context appear |
| `source_not_cited` | the supplied source (`wttr`, `news`) is not named |

**Verdict rule:** `recommended` = complied on every prompt, `flaky` = 75-99%,
`not recommended` = below 75%. Run the harness with
`--system-file /opt/meshtastic-ai-bridge/agent_prompt.txt` so the model is judged
against the prompt the bridge actually sends, not the short built-in one.

#### Raspberry Pi 5, single pass over all 16 installed models

`AI_MAX_TOKENS=220`, `agent_prompt.txt` as the system prompt:

| Model | Comply | Avg words | Pkts | Verdict | Failures |
|---|---:|---:|---:|---|---|
| `lfm2.5-230m` | 6/6 | 33 | 1.83 | recommended | - |
| `lfm2.5-1.2b` | 6/6 | 32 | 1.50 | recommended | - |
| `bonsai-4b` | 6/6 | 37 | 2.00 | recommended | - |
| `lfm2.5-350m` | 5/6 | 29 | 1.33 | flaky | source_not_cited |
| `gemma3:1b` | 5/6 | 36 | 1.83 | flaky | unfinished |
| `llama3.2:1b-instruct-q4_K_M` | 5/6 | 47 | 2.17 | flaky | too_many_bullets |
| `minicpm5-2b` | 5/6 | 37 | 1.83 | flaky | too_many_bullets |
| `qwen2.5:3b-instruct-q4_K_M` | 5/6 | 37 | 1.83 | flaky | too_many_bullets |
| `ling-3.0-tiny` | 5/6 | 39 | 2.00 | flaky | not_grounded, source_not_cited |
| `qwen3:0.6b` | 4/6 | 33 | 1.67 | not recommended | source_not_cited, too_many_bullets |
| `qwen2.5:1.5b-instruct-q4_K_M` | 4/6 | 38 | 1.83 | not recommended | source_not_cited, too_many_bullets |
| `qwen2.5:0.5b` | 3/6 | 43 | 1.83 | not recommended | over_word_limit, source_not_cited, too_many_bullets |
| `bonsai-1.7b` | 3/6 | 41 | 2.00 | not recommended | over_word_limit, too_many_bullets, unfinished |
| `minicpm5-1b` | 1/6 | 18 | 1.00 | not recommended | empty, not_grounded, source_not_cited |
| `qwen3.5:0.8b` | 0/6 | - | - | not recommended | empty, not_grounded, source_not_cited |
| `spark-x2.5-1.7b` | 0/6 | - | - | not recommended | empty, not_grounded, source_not_cited |

#### Raspberry Pi 4B, single pass

| Model | Comply | Avg words | Pkts | Verdict | Failures |
|---|---:|---:|---:|---|---|
| `lfm2.5-350m` | 5/6 | 26 | 1.33 | flaky | source_not_cited |
| `lfm2.5-230m` | 4/6 | 43 | 1.83 | not recommended | too_many_bullets, unfinished |
| `qwen2.5:0.5b` | 2/6 | 33 | 1.50 | not recommended | source_not_cited, too_many_bullets |
| `minicpm5-1b` | 1/6 | 32 | 2.00 | not recommended | empty, not_grounded, source_not_cited |
| `minicpm5-1b` (think off) | 5/6 | 39 | 1.83 | flaky | too_many_bullets |

#### Confirmed over three passes

A single failure can be noise, so the plausible candidates were re-run twice more
with a different seed (three samples per prompt, 18 per model). Note how the
one-pass perfect scores do not survive repetition:

| Model | Comply | Rate | Avg words | Pkts | Verdict | Failures |
|---|---:|---:|---:|---:|---|---|
| `lfm2.5-1.2b` | 16/18 | 89% | 32 | 1.50 | flaky | unfinished x2 |
| `gemma3:1b` | 15/18 | 83% | 38 | 1.94 | flaky | unfinished x3 |
| `lfm2.5-230m` | 14/18 | 78% | 31 | 1.61 | flaky | unfinished x2, source_not_cited x2 |
| `lfm2.5-350m` | 13/18 | 72% | 26 | 1.33 | not recommended | source_not_cited x5 |
| `qwen2.5:0.5b` | 11/18 | 61% | 46 | 1.94 | not recommended | too_many_bullets x4, over_word_limit x3, source_not_cited x3 |

#### How to read this

Nothing tested obeyed the contract every time. The failures fall into three
classes that matter very differently:

| Class | Checks | Effect |
|-------|--------|--------|
| **Hard failure** | `empty`, `not_grounded` | The bridge posts nothing, or posts an answer that ignores the retrieved material. Unusable. |
| **Attribution failure** | `source_not_cited` | The answer is right but does not name its source, which the prompt requires and readers need in order to judge trust. |
| **Style breach** | `too_many_bullets`, `over_word_limit`, `unfinished`, `markdown` | Costs airtime (extra packets) or reads as truncated, but the content is usable. |

What that means in practice:

- Every model with a hard failure is either a reasoning model in default mode
  (`qwen3.5:0.8b`, `spark-x2.5`, `minicpm5-1b`) or a 1-bit quantisation
  (`bonsai-1.7b`, which breaks the style rules too). `think: false` fixes the
  reasoning models' empty replies - `minicpm5-1b` goes from 1/6 to 5/6 - but it
  cannot be relied on through the bridge, which sends no such flag.
- **`qwen2.5:0.5b`, the current default, is the weakest of the candidates**: 61%
  across three passes, breaching the word budget, the bullet budget and source
  citation. It was chosen because it is the only model that *runs* on 2 GB, not
  because it follows instructions well.
- The LFM2.5 family behaves best (`lfm2.5-1.2b` 89%, `lfm2.5-230m` 78%), and its
  failures are the cheap kind - a missing `source:` note or a reply that stops on a
  comma - not invented answers.
- Because even the best model slips, **enforce the budget in the bridge instead of
  trusting the model**. `REPLY_MAX_CHARS` already chunks long replies; trimming to a
  word/packet budget would remove the `over_word_limit` and `unfinished` classes
  outright. Select models on the hard-failure class, which no prompt change fixes.

#### Endpoint check

The bridge posts to `/v1/chat/completions`, not Ollama's native `/api/chat`, and
injects retrieved material into the user message, so a model has to work on that
path with the real prompt. Verified on the Pi 5 with a WikiMed context block:
`lfm2.5-1.2b` returned 28 words citing 6 of the supplied terms and `qwen2.5:0.5b`
38 words citing 7, both inside two 180-character packets. The LFM2.5 models answer
through the same endpoint once imported.

### Recommended model per host

Speed decides what fits; compliance decides what is worth running. Rates are from
the three-pass run where available, otherwise a single pass.

| Host RAM | First choice | Comply | Registry-only alternative |
|---------:|--------------|-------:|--------------------------|
| 2 GB | `lfm2.5-350m` † - 8.6 tok/s on the Pi 4, RSS 516 MB, 1.05 GB left free | 72-83% | `qwen2.5:0.5b` - 8.0 tok/s, RSS 675 MB (61%) |
| 2 GB, if latency matters more | `lfm2.5-230m` † - 12.8 tok/s, RSS 383 MB | 67-78% | - |
| 4 GB | `lfm2.5-1.2b` † - 15.5 tok/s, RSS 967 MB | 89% | `gemma3:1b` - 11 tok/s, RSS 1.3 GB (83%) |
| 8 GB | `lfm2.5-1.2b` † - 15.5 tok/s, RSS 967 MB | 89% | `gemma3:1b` or `llama3.2:1b-instruct-q4_K_M` (83%) |
| 8 GB, more capable | `qwen2.5:3b-instruct-q4_K_M` - 5 tok/s, RSS 2.4 GB | 83% | `llama3.2:3b-instruct-q4_K_M` |

† Requires the one-time GGUF import described below.

On the 2 GB Pi 4, `lfm2.5-350m` is the larger of the two LFM2 models that fit and it
still leaves about 1 GB of RAM free during a reply; `lfm2.5-230m` is roughly 50%
faster with more headroom, but it garbled a medical term in testing (it produced
"nausea/ventilator use" instead of "nausea or vomiting"), which is why the bigger
model is the default there. `lfm2.5-1.2b` is only recommended from 4 GB up: at
~967 MB resident it would sit against the 1.1 GB Ollama cap that a 2 GB host needs.

Do not deploy: `qwen3.5:0.8b`, `spark-x2.5` and default-mode `minicpm5-1b` (empty
replies), `bonsai-1.7b` (1-bit and breaks the style rules), or `gemma3n:e2b`,
`gemma3:4b` and `granite4:micro-h` (3.9-6.1 tok/s, and `gemma3n:e2b` needs 5.3 GB
RSS). On the 2 GB Pi 4 `gemma3:1b` is *not* a safe alternative - it swapped hard and
reset the board - so `lfm2.5-350m`, `lfm2.5-230m` and `qwen2.5:0.5b` are the only
models measured as stable there.

The figures above supersede the earlier proof-of-concept measurement recorded at
the end of this section. On the 2 GB Pi 4 `qwen3.5:0.8b` is not merely
slow, it is unsafe for the host.

### Cross-check against published Pi 5 numbers

A widely shared community benchmark ("best LLM models for the Raspberry Pi 5 in
2026", posted to r/raspberry_pi) reports `llama.cpp` prefill/decode rates on a
16 GB Pi 5. Re-measured here through Ollama on an 8 GB Pi 5 with this harness:

| Model | Published | Measured here | Notes |
|-------|----------:|--------------:|-------|
| LFM2.5-350M | 31.0 tok/s, 0.38 GB | `lfm2.5-350m` **30.0 tok/s**, 379 MB | Confirmed within 3% (via GGUF import) |
| LFM2.5-1.2B | 15.3 tok/s, 0.73 GB | `lfm2.5-1.2b` **15.5 tok/s**, 731 MB | Confirmed within 2% (via GGUF import) |
| LFM2.5-230M | not listed | `lfm2.5-230m` **45.0 tok/s**, 246 MB | Fastest model measured on either host |
| MiniCPM5-1B | 18.1 tok/s, 0.69 GB | `minicpm5-1b` **18.4 tok/s**, 688 MB | Confirmed within 2% (reasoning off) |
| MiniCPM5-2B | 8.1 tok/s, 1.56 GB | `minicpm5-2b` **7.7 tok/s**, 1.56 GB | Confirmed within 5% (reasoning off) |
| Ling-3.0-tiny 7.9B-A1B | 11.1 tok/s, 4.82 GB | `ling-3.0-tiny` **9.9 tok/s**, 4.82 GB | Confirmed within ~11% (reasoning off) |
| Bonsai-1.7B (Q1_0) | 11.6 tok/s, 0.25 GB | `bonsai-1.7b` **15.3 tok/s**, 248 MB | ~32% faster than reported |
| Bonsai-4B (Q1_0) | 4.8 tok/s, 0.57 GB | `bonsai-4b` **6.8 tok/s**, 572 MB | ~42% faster than reported |
| Spark-X2.5-1.7B | 10.4 tok/s, 1.11 GB | `spark-x2.5-1.7b` **9.7 tok/s**, 1.11 GB | Confirmed within 7% (reasoning off) |
| K2-Horizon-0.9B | 11.0 tok/s, 1.15 GB | not tested | No 0.9B release found; that repository starts at 3.7B |
| Granite-4.0-H-350M | 21.9 tok/s, 0.37 GB | `granite4:350m` 12.1 tok/s, 0.71 GB | Matching model, but Ollama packages it ~2x larger and it decodes ~45% slower |
| LFM2.5-8B-A1B | 9.9 tok/s, 5.16 GB | `lfm2.5:8b` 8.7-9.4 tok/s, 5.16 GB | Confirmed within ~10% |
| Qwen3.5-2B | 7.3 tok/s, rated too slow | `qwen3.5:2b` 5.0 tok/s | Same verdict; it also needs `think: false` |

Models from that list that are absent from the Ollama registry (verified as HTTP
404 on `registry.ollama.ai/v2/library/<name>/manifests/<tag>`): LFM2.5-350M,
LFM2.5-1.2B and LFM2.5-230M (published by Liquid AI on HuggingFace), MiniCPM5-1B/2B,
Ling-3.0-tiny, Spark-X2.5-1.7B, Bonsai-1.7B/4B, and K2-Horizon. Every one of them
except K2-Horizon was still testable by importing its GGUF directly - see
[Models that are not in the Ollama registry](#models-that-are-not-in-the-ollama-registry)
- and the LFM2.5 models turned out to be the fastest measured on either host. The
"Gemma E2B/E4B" recommendation maps to `gemma3n:e2b` (5.6 GB) and `gemma3n:e4b`
(7.6 GB). `gemma3n:e2b` was measured and is a poor fit for an 8 GB host: 6.1
tok/s at RSS 5.3 GB, which leaves it swapping once the bridge and dashboard are
resident. Its per-layer-embedding design keeps the weights small, but not the
runtime footprint.

One caveat on the Bonsai models: both are 1-bit (`Q1_0`) quantisations, so their
decode rates (15.3 tok/s for the 1.7B, 6.8 for the 4B) and footprints are
impressive for the size, but 1-bit weights noticeably degrade answer quality.
Treat them as curiosities rather than recommendations.

Two caveats when comparing the tables. The published figures come from raw
`llama.cpp` rather than Ollama, which bundles its own runtime and quantization
packaging, so absolute tokens/second differs. Its "RAM @ 4K" column is a
total-footprint figure (9.71 GiB for LFM2.5-8B) roughly double the peak RSS
measured here (5.3 GiB), because it counts mapped file pages. And the ranking
changes for a mesh assistant: replies must fit in about 90 words, so a model that
returns 140-token answers wastes airtime even at a good decode rate, while a 34 s
cold load hurts more than a two-token-per-second difference.

### Earlier proof-of-concept measurement

Kept for reference. These numbers predate the harness below and the Ollama service
cap, and come from the original 2 GB Raspberry Pi 4 proof of concept:

On a 2 GB Raspberry Pi 4 with swap enabled, a one-sentence test using
`qwen2.5:0.5b` completed in about 16.6 seconds and produced 28 output tokens.
An initial multi-prompt comparison saturated memory and swap; the
`qwen3.5:0.8b` test did not complete within the short diagnostic window. Use
one model request at a time and keep `AI_MAX_TOKENS` modest for mesh use. The
smaller model is currently the safer responsiveness choice; benchmark again
after changing hardware, prompt length, or model settings.

## Model benchmarking harness

`scripts/benchmark_models.sh` measures candidate models **on the node itself**.
It sends purpose-aligned prompts (short factual, procedural safety,
retrieved-context Q&A, weather summary, and a help reply) through the same reply
paths the bridge uses, then reports cold-load time, time to first token,
wall-clock latency, generated tokens, tokens per second, reasoning-token volume,
and peak host memory:

```bash
scripts/benchmark_models.sh --models-file /tmp/models.txt --repeats 2 \
  --json /tmp/bench.json --markdown /tmp/bench.md
```

Useful flags:

| Flag | Purpose |
|------|---------|
| `--models` / `--models-file` | Model list to measure (`#` comments allowed) |
| `--prompts NAME...` | Run a subset (`short_factual`, `safety_steps`, `wiki_context`, `weather_context`, `news_context`, `help_reply`) |
| `--system-file PATH` | Judge the model against the real prompt, e.g. `agent_prompt.txt` |
| `--max-tokens` | Generation cap; default `220`, the bridge default |
| `--think false` | Disable the reasoning block on `qwen3`, `qwen3.5`, `minicpm5`, `ling-3.0` and `spark-x2.5` models |
| `--unload-all` | Unload every other model first, so memory figures are clean |
| `--repeats` | Repeat every prompt N times and average |
| `--base-url` | Non-default Ollama endpoint |
| `--dry-run` | Show which installed models would be measured |
| `--pull-missing` | Pull models that are absent before measuring |

The JSON report includes full model replies. Review it before sharing, especially
when using `--system-file`; a model may repeat text from that prompt. A remote
`--base-url` sends the benchmark prompts and any custom system prompt to that
endpoint.

Each model is unloaded before it is timed, so the reported load time is a cold
start. Every model runs with the same system prompt, a fixed seed, and the same
prompt set, which makes the results comparable across hosts. Only the Python
standard library is required, so nothing has to be installed into the bridge
virtualenv.

Every model request is a real bridge-shaped request, so run the harness when the
mesh is quiet: it competes with the live bridge for the same Ollama instance.

## Models that are not in the Ollama registry

Several of the fastest small models are not published as Ollama tags, and
`ollama pull hf.co/<repo>:<quant>` fails on them with
`blocked redirect to a different host`, because HuggingFace redirects downloads to
its Xet CDN and Ollama refuses cross-host redirects. Fetch the GGUF directly and
import it instead:

```bash
mkdir -p ~/gguf && cd ~/gguf
curl -sL -C - --retry 5 --retry-all-errors -O \
  https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF/resolve/main/LFM2.5-350M-Q8_0.gguf
printf 'FROM %s/LFM2.5-350M-Q8_0.gguf\n' "$PWD" > Modelfile-lfm2.5-350m
ollama create lfm2.5-350m -f Modelfile-lfm2.5-350m
ollama run lfm2.5-350m
```

`ollama create` copies the weights into the model store, so the staging directory
can be deleted afterwards. Remember that this doubles the transient disk use.

**Stage GGUFs on disk, not in `/tmp`.** On Raspberry Pi OS `/tmp` is a RAM-backed
tmpfs: 923 MB on a 2 GB Pi 4 and 4 GB on an 8 GB Pi 5. A GGUF written there
consumes RAM, and on the Pi 4 it cannot exceed the tmpfs size at all - a 730 MB
download failed repeatedly because 625 MB of other GGUFs had filled it. Keep the
staging directory under `/home`, and always pass `-C -` so a dropped connection
resumes instead of restarting: a plain `curl -o` restarted multi-gigabyte
transfers from zero on both hosts, because the Xet CDN connection drops mid-file.

Sizes and quants verified on the Pi 5:

| Model | GGUF repository | Quant | Size |
|-------|-----------------|-------|-----:|
| `lfm2.5-230m` | `LiquidAI/LFM2.5-230M-GGUF` | `Q8_0` | 246 MB |
| `lfm2.5-350m` | `LiquidAI/LFM2.5-350M-GGUF` | `Q8_0` | 379 MB |
| `lfm2.5-1.2b` | `LiquidAI/LFM2.5-1.2B-Instruct-GGUF` | `Q4_K_M` | 731 MB |
| `minicpm5-1b` | `openbmb/MiniCPM5-1B-GGUF` | `Q4_K_M` | 688 MB |
| `minicpm5-2b` | `openbmb/MiniCPM5-2B-GGUF` | `Q4_K_M` | 1.56 GB |
| `ling-3.0-tiny` | `inclusionAI/Ling-3.0-tiny-GGUF` | `Q4_K_M` | 4.82 GB |
| `bonsai-1.7b` | `prism-ml/Bonsai-1.7B-gguf` | `Q1_0` | 248 MB |
| `bonsai-4b` | `prism-ml/Bonsai-4B-gguf` | `Q1_0` | 572 MB |
| `spark-x2.5-1.7b` | `XHToken/Spark-X2.5-1.7B-GGUF` | `Q4_K_M` | 1.11 GB |

Verify a download before importing. HuggingFace publishes the file hash as the
`x-linked-etag` response header, so
`curl -sIL <resolve-url> | grep -i x-linked-etag` gives the expected SHA-256;
compare it against `sha256sum <file>`. That check caught a partially-copied
4.8 GB GGUF during this work, which would otherwise have been imported as garbage.

The same approach works for any GGUF without an Ollama tag. It is how the
MiniCPM5 and Ling-3.0 models in the measured tables above were installed.

## Protecting a memory-constrained host

By default a model that is larger than the host's RAM does **not** fail cleanly.
Ollama keeps loading it, the node swaps heavily, and a Raspberry Pi with the
default hardware watchdog armed can reset the board instead of returning an
error. Bound the model server so an oversized model turns into a failed request:

```bash
sudo install -d /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/memory-limit.conf >/dev/null <<'EOF'
[Service]
# Bound the model server's RAM and forbid swap so a model that does not fit
# fails fast instead of thrashing the SD card and rebooting the node.
MemoryMax=1100M
MemorySwapMax=0
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Set `MemoryMax` for the model you intend to run plus roughly 30-50% overhead, and
choose a model whose size leaves that much headroom. Also ensure only one model
is resident at a time: the bridge uses one model, and `benchmark_models.sh
--unload-all` unloads the others before each measurement.

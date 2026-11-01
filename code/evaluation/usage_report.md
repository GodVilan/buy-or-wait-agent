# Model Usage Report

## Cost of producing `output.csv`

The solution has two narrow Anthropic perception layers. Both use
`claude-haiku-4-5-20251001` at **$1.00 per million input tokens** and **$5.00
per million output tokens**. Financial reconstruction, forecasting, planning,
ranking, validation, and output formatting remain deterministic Python code.

### Per-model totals

| Layer | Model calls | Input tokens | Output tokens | Total tokens | Estimated cost |
|---|---:|---:|---:|---:|---:|
| Vision image extraction | 16 | 26,444 | 285 | 26,729 | $0.027869 |
| Message-delta extraction | 176 | 200,064 | 9,331 | 209,395 | $0.246719 |
| **Overall** | **192** | **226,508** | **9,616** | **236,124** | **$0.274588** |

The vision layer processes the 16 PNG-backed blank-amount events. The
message-delta layer processes the 176 unlinked messages. These are dataset-level
preprocessing calls rather than one model call per purchase request.

### Overall amortised averages across 250 requests

The following figures amortise the full cold-extraction totals across the 250
requests in `requests.csv`; they do not imply a model call for each request:

```text
Amortised model calls across 250 requests:  0.768
Amortised input tokens across 250 requests: 906.032
Amortised output tokens across 250 requests: 38.464
Amortised total tokens across 250 requests:  944.496
Amortised model cost across 250 requests:    $0.001098352
```

For reference, the per-layer token averages are also amortised across the same
250-request denominator:

```text
Vision input/output/total tokens amortised across 250 requests: 105.776 / 1.14 / 106.916
Message input/output/total tokens amortised across 250 requests: 800.256 / 37.324 / 837.58
```

## Final full-dataset run

The final full-dataset run used warm caches for both perception layers and made
**zero model calls**:

```text
python3 code/main.py
```

The validated vision cache contains all 16 image results, and the validated
message-delta cache contains all 176 message results. Consequently, the final
250-request write incurred zero additional model tokens and zero additional
model cost. The output writer remains deterministic and model-free when these
validated caches are present.

## Vision model

- Provider: **Anthropic**
- Model: **claude-haiku-4-5-20251001**
- Calls: **16**
- Input tokens: **26,444**
- Output tokens: **285**
- Total tokens: **26,729**
- Estimated cost: **$0.027869**
- Task: extract the visible monetary amount and currency from 16 PNG images
- Output contract: strict JSON containing only `amount` and `currency`

The image cache is keyed by the SHA-256 digest of image bytes plus the model
string. Credentials are loaded from the process environment and are not
included in this report.

Vision cost calculation:

```text
Input:  26,444 / 1,000,000 * $1.00 = $0.026444
Output:    285 / 1,000,000 * $5.00 = $0.001425
Total:                            = $0.027869
```

## Message-delta model

- Provider: **Anthropic**
- Model: **claude-haiku-4-5-20251001**
- Calls: **176**
- Input tokens: **200,064**
- Output tokens: **9,331**
- Total tokens: **209,395**
- Estimated cost: **$0.246719**
- Task: extract structured financial deltas from the 176 messages with empty
  `related_event_id`
- Output contract: a non-empty list of strict delta objects

The message cache is keyed by message text, model string, and the versioned
extraction schema. Credentials are loaded from the process environment and are
not included in this report.

Message-delta cost calculation:

```text
Input:  200,064 / 1,000,000 * $1.00 = $0.200064
Output:   9,331 / 1,000,000 * $5.00 = $0.046655
Total:                             = $0.246719
```

## Overall cost calculation

```text
Input:  226,508 / 1,000,000 * $1.00 = $0.226508
Output:   9,616 / 1,000,000 * $5.00 = $0.048080
Total:                             = $0.274588
```

These calculations include only input and output token charges. Prompt-cache
write/read rates were not used because the recorded runs did not use prompt
caching. Warm-cache runs avoid additional model calls and model-token cost.

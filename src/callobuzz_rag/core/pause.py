"""Why an answer is a pause reply instead of an answer (a tiny module both the gate and the
conversation layer import, so neither pulls in the other)."""

from typing import Literal

# `billing`: the balance ran out; `fair_use`: a usage limit; `local_unavailable`: the free local
# model is not ready; `demo_limit` (L5a demo): a demo's daily answers are used up for a visitor or
# for the whole demo, or cannot be counted.
PausedBy = Literal["billing", "fair_use", "local_unavailable", "demo_limit"]

# Does steward make anyone better off?

Run it yourself: `uv run python -m steward evaluate`

## The claim, and what would falsify it

**Claim.** A delegated-spending agent reduces the number of days a household is
out of an essential, because it converts *urgency* into *choice*.

**What would falsify it.** Any of: the arms failing to converge when the agent
is stripped of its advantages; no crossover point on the sweep; or the agent
losing in households that can actually afford their commitments.

The primary metric — **stockout-days per household-month** — was fixed in
`metrics.py` before the first run. It is deliberately the metric the agent is
least flattered by: it ignores money saved, where the phasing assumption does
the work, and ignores sponsor interruptions, which the agent wins trivially by
having a policy engine at all.

## Method

Four scripted households × 5 seeds × 6 months × two arms, at seven levels of
`forgetfulness`. Same seed, same world, both arms.

The agent's advantage is exactly three separable things, and nothing else:

| component | what it is |
|---|---|
| anticipation | `notice_days` of warning before running out |
| reliability | not forgetting, once there is something to act on |
| phasing | money set aside on payday rather than at month end |

Which supplier gets used is *not* part of the arm: both take the cheap slow one
if it will arrive in time, otherwise pay for speed. Building that into the arm
would have smuggled in a second advantage.

## The check that makes any of it mean something

`converges_with_no_advantage` strips all three and asserts the arms produce
identical numbers. **It failed three times before it passed**, and each failure
was a defect in the harness rather than a finding:

1. Zeroing only `forgetfulness` — anticipation survives, so it was never going
   to converge. The check was wrong, not the simulation.
2. Supplier choice was baked into the arm rather than derived from time
   remaining.
3. Both arms drew from **one RNG stream**, so the no-agent arm's extra draws for
   its forgetfulness check walked its shocks out of step. The two arms were
   being run against different worlds while the docstring claimed otherwise.

A convergence check that passed on the first attempt would have been evidence of
nothing.

## Result

Per household, at `forgetfulness = 0.5`, stockout-days per month:

| household | without agent | with agent | verdict |
|---|---|---|---|
| comfortable | 12.17 | **0.00** | agent |
| tight | 12.17 | **0.00** | agent |
| precarious | 12.17 | **0.00** | agent |
| overreaching | 12.17 | 44.47 | **no agent** |

Three of four households go to zero stockout-days at *every* forgetfulness
level, including zero. `overreaching` is worse with the agent, and the reason is
specific: its goal needs its entire disposable income, so following the plan
leaves it unable to afford soap (288 could-not-afford events).

### The mean is the wrong statistic here

Averaged across households, the agent looks *worse* below `forgetfulness = 0.5`
and better above it. That aggregate is an artefact: it is three total wins and
one broke household, and it describes none of them. Reported as a mean it would
have been a real-looking finding that was false of every household in it.

## What this does not show

- **No language model runs here.** This measures whether the *mechanism* helps,
  assuming the agent executes it. Whether an LLM executes it correctly is
  untested, and is the largest open gap in this project.
- **`overreaching` is a case steward's own planner would have refused.** Phase
  6's `ways_to_close` tells a person when a goal is unreachable; the simulated
  agent follows the plan blindly instead. So the one household where the agent
  loses is one where the product, used properly, would not have started the plan
  — which is an argument for the advisory design, not evidence against it, but
  it is *not* what was measured and should not be reported as though it were.
- Income is regular and a month is 30 days. Real precarity is lumpier, which
  would probably favour phasing — so this is the conservative direction.
- Nobody here is ill, changes their mind, or shares a household with someone
  else who uses the soap.
- Five seeds average out the dice. This is a claim about a mechanism, not an
  effect size in a population, and no significance is claimed.

## The pilot this does not replace

Outcomes come from simulation because pilots run on sandbox money and cannot
honestly support outcome claims. A pilot measures something different and still
necessary: whether two people can understand what steward is doing, trust the
escalations, and correct it when it is wrong. Nothing in this document speaks to
that.

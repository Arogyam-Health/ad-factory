# A/B Testing Playbook

Use this dashboard to test one thing at a time. If persona, format, hypothesis, visual pattern, and background all change together, the result is not a clean A/B test.

## Dashboard Controls

1. `Persona`: buyer segment being tested.
2. `Format`: `HERO`, `BA`, `TEST`, `FEAT`, `UGC`.
3. `Hypothesis Type`: message variable, such as awareness stage, proof style, CTA voice, etc.
4. `Hypothesis Variant`: exact option inside the selected hypothesis type.
5. `Visual Pattern`: layout/archetype for the selected format.
6. `Multiplier`: multiple creative executions for the same test cell.
7. `Keep same background across personas for each format`: optional background lock across personas.
8. `Reuse backgrounds from previous run`: reuse background slot and seed from an earlier run.

## What A Test Cell Means

A test cell is one exact setup:

```text
persona + format + hypothesis type + hypothesis variant + visual pattern
```

Example:

```text
P02 + HERO + proof_style + authority_anchor + hero_left_copy_right_product
```

If multiplier is `5`, the dashboard generates 5 executions of that same cell: `A01` to `A05`.

## Multiplier Behavior

Multiplier keeps these the same inside a cell:

```text
persona
format
hypothesis type
hypothesis variant
selected visual pattern
background
```

Only execution/copy variation changes.

If visual pattern is left on `Auto rotate pattern`, the pattern can vary. If you want a clean visual-pattern test, manually select the pattern.

## Background Behavior

Default behavior:

```text
same background only inside the same persona + format + hypothesis + variant cell
```

Checkbox enabled:

```text
same background across selected personas for the same format + hypothesis + variant
```

Different formats still get different backgrounds.

Use the checkbox when testing personas and you want background noise removed. Leave it off when you want each persona to get its own visual environment.

For hypothesis tests across separate runs, enable `Reuse backgrounds from previous run` and select the baseline run. This keeps background slot and seed fixed while changing only the hypothesis variant.

## Metadata To Check

Before launching ads, check image metadata for:

```text
persona
format
hypothesis_type
hypothesis_variant
creative_index
creative_total
background.slot
background.seed
visual_archetype.id
background_decisions.shared_by_multiplier
background_decisions.shared_across_personas
```

If metadata does not match your intended test, do not spend money on it.

## How To Test Hypothesis Variants

Question:

```text
Which message angle works better?
```

Keep same:

```text
persona
format
visual pattern
hypothesis
background, by using Reuse backgrounds from previous run
multiplier
audience
budget
landing page
```

Change only:

```text
hypothesis variant
```

Example:

```text
Cell A: P02 + HERO + proof_style + authority_anchor + hero_left_copy_right_product + multiplier 5
Cell B: P02 + HERO + proof_style + social_proof + hero_left_copy_right_product + multiplier 5
```

Recommended flow:

```text
Run Cell A normally.
Run Cell B with Reuse backgrounds from previous run = Cell A run.
```

## How To Test Formats

Question:

```text
Which format works better for this persona and message?
```

Keep same:

```text
persona
hypothesis type
hypothesis variant
multiplier
audience
budget
landing page
```

Change only:

```text
format
```

Example:

```text
Cell A: P02 + HERO + awareness_stage + problem_aware + multiplier 4
Cell B: P02 + UGC + awareness_stage + problem_aware + multiplier 4
Cell C: P02 + TEST + awareness_stage + problem_aware + multiplier 4
```

## How To Test Visual Patterns

Question:

```text
Which layout works best inside one format?
```

Keep same:

```text
persona
format
hypothesis type
hypothesis variant
multiplier
audience
budget
landing page
```

Change only:

```text
visual pattern
```

Example:

```text
Cell A: P02 + FEAT + concept_structure + fab + feat_bullet_panel + multiplier 5
Cell B: P02 + FEAT + concept_structure + fab + feat_modular_cards + multiplier 5
```

Do not compare a HERO pattern against a UGC pattern and call it a visual-pattern test. That is a format test.

## How To Test Personas

Question:

```text
Which buyer segment responds best?
```

Keep same:

```text
format
hypothesis type
hypothesis variant
visual pattern
multiplier
audience setup
budget
landing page
```

Change only:

```text
persona
```

Recommended:

```text
Enable: Keep same background across personas for each format
```

Example:

```text
Cell A: P01 + UGC + cta_voice + guided_next_step + ugc_desk_review + multiplier 4
Cell B: P02 + UGC + cta_voice + guided_next_step + ugc_desk_review + multiplier 4
Cell C: P03 + UGC + cta_voice + guided_next_step + ugc_desk_review + multiplier 4
```

## How To Test Execution Quality

Question:

```text
Within one strategy, which generated ad is best?
```

Use:

```text
one persona
one format
one hypothesis type
one hypothesis variant
one visual pattern
multiplier 5-10
```

If multiple executions from the same cell perform well, the strategy is strong. If only one execution wins and the rest fail, it may be a lucky creative.

## Recommended Test Order

1. Test persona.
2. Test format for winning persona.
3. Test hypothesis variants inside winning persona + format.
4. Test visual patterns inside winning persona + format + hypothesis.
5. Use multiplier to produce more executions from the winning cell.

## Metrics

For attention:

```text
CTR
CPC
thumbstop rate, if available
```

For traffic quality:

```text
landing page view rate
add-to-cart rate
checkout initiated rate
```

For business performance:

```text
CPA
ROAS
purchase conversion rate
```

Rules:

```text
High CTR + bad CPA = attention but weak buyer intent
Low CTR + good CPA = narrower but possibly profitable
Best CPA/ROAS beats best CTR when optimizing purchases
```

## Minimum Data

Use these as rough minimums:

```text
CTR test: 1,000 impressions per cell
Traffic test: 100 landing page views per cell
Purchase test: 10+ purchases per cell if possible
```

If budget is low, treat results as directional, not final.

## Bad Tests To Avoid

```text
Bad: P01 HERO authority vs P02 UGC social proof
Reason: persona, format, and hypothesis all changed.

Bad: HERO centered pattern vs TEST quote card
Reason: format changed, so it is not a visual-pattern test.

Bad: calling a winner from one sale
Reason: too little data.

Bad: letting one ad get all spend while others are starved
Reason: no fair comparison.
```

## Final Rule

Use this formula:

```text
one variable changed + equal spend + enough data + clean metadata = useful learning
```

If that formula is broken, it is not a real A/B test.

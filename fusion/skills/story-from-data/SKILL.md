---
name: story-from-data
description: Turn a loaded dataset into a story of findings with Nightingale's story tools, quoting only the figures the queries computed.
---

# A story from the data

`story_build` reads a dataset's current version and returns the findings it
supports, strongest first. Each one carries its chart, one sentence with the
exact figure and the SQL query that produces it. Five kinds, each with its own
threshold: trend, comparison between groups, outliers, concentration and change
between periods. A finding that does not clear its threshold is not returned,
and a dataset that supports nothing returns an empty story.

## What to do

1. `data_list` if the dataset name is not certain, then `story_build` with
   `dataset`. Leave `rewrite` off unless the person asked for the sentences to
   read differently.
2. Read the findings in the order returned. Do not reorder them by hand: the
   order is the strength of the evidence.
3. Report each finding with its sentence and its figures. The sentence already
   states the exact number the chart shows; do not round it again, do not add a
   percentage, a total or a date that is not in `figures`.
4. To point at the evidence, give the `query.sql` of the finding (or its
   `log_id`), so the person can run it with `data_query`.
5. `story_update` reorders (`order`), drops (`remove`) or rewords
   (`statements`) findings. A sentence with a number that is not one of the
   finding's figures is rejected and the template stays; say so instead of
   insisting.
6. `story_export` with `format="html"` writes one standalone file. With
   `format="presentation"` it builds one slide per finding in Cicero's Hoard
   through the Hub; when Cicero is not available the answer says so and the
   HTML is still the deliverable.

## What not to do

- Never write a finding yourself from a preview or from the profile: if the
  story does not contain it, the data does not support it.
- Never present a rewrite as a new calculation. The model only rewords text
  over figures that were already computed.
- Do not describe a chart you have not been given: the `chart.rows` of the
  finding are the numbers the sentence summarises.
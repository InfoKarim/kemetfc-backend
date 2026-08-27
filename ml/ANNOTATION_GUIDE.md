# Football annotation guide

This guide is the minimum protocol for TrainingBuddy research data. It does not
replace guardian consent, safeguarding review, or the academy's recording rules.

## Units and classes

Annotate complete sampled frames with the class mapping in
`ml/football_dataset.yaml`: `player`, `goalkeeper`, `referee`, and `ball`.
Boxes must tightly enclose the visible object. Keep the same rule for partially
occluded people; do not infer invisible body extent. A ball is annotated only
when a reviewer can distinguish it from field markings and equipment.

For tracking annotations, assign a stable ID through occlusion only when visual
continuity is defensible. Mark uncertain re-identification for adjudication.
Do not use faces to identify children.

For events, record the earliest frame at which the defined event is observable.
A pass candidate starts when the ball clearly leaves the possessor and ends at
the first controlled touch or loss of possession. Automated proximity changes
remain candidates until a coach confirms them.

## Independent annotation and adjudication

1. Two annotators independently label a pre-registered representative subset.
2. They must not see each other's labels before export.
3. Run `python -m scripts.evaluate_annotation_agreement`.
4. Investigate every class below the configured F1 threshold and every material
   timing disagreement.
5. A third qualified reviewer adjudicates disagreements; never silently copy one
   annotator's labels into the gold set.
6. Record annotator training, guide version, tool version, dates, and exclusions.

Agreement measures reproducibility, not truth. Report the subset selection and
confidence interval alongside the score.

## Required error tags

Each reviewed failure should include applicable tags: `blur`, `night`,
`backlight`, `far_ball`, `partial_occlusion`, `crowded_box`, `camera_cut`,
`gimbal_loss`, `similar_kits`, `substitution`, and `unknown`. These tags feed
slice analysis; do not remove a difficult example merely because performance is
poor.

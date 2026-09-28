# Manual source classification

The Creator Studio manual source form supports exact, evergreen general claims. It has no default fact type or source relationship. The operator must choose both before saving and explicitly confirm that the selected excerpts contain no funding, eligibility, or time-sensitive conditions. Original text and code-point evidence spans remain unchanged.

Source relationship describes provenance, not verification:

- `PRIMARY`: the original issuer of the information.
- `SECONDARY`: a summary or republication.
- `INTERNAL`: workspace or internal documentation.

Choosing a relationship does not verify the source. Saved manual sources still require the existing human source verification, deterministic QA, and protected content approval. Test evidence retains its fixture marker and cannot become publishable through this form.

Funding, eligibility, and time-sensitive claims cannot be submitted as `GENERAL` through this form. Select **Discover opportunities** for supported official opportunities, or use the existing typed evidence API with the required grant fields and dates. The manual form does not strip structured metadata or silently substitute a general fact. An expired grant must remain subject to the existing date and eligibility rules.

The form also checks the selected excerpts for common English and Spanish funding, eligibility, dates, amounts, and deadline wording. A detected indicator disables saving and repeats the structured-evidence guidance. This check is intentionally conservative: it can reject harmless wording and cannot detect every semantic claim. It is a guard against accidental misclassification, not proof that a claim is evergreen, an authoritative classifier, or a replacement for backend QA. Reviewers must still check whether the selected classification is correct; unusual or ambiguous claims belong in the typed evidence workflow.

Changing source text, excerpts, claim type, or source relationship clears the operator's declaration. The persisted source metadata records the explicit classification and `evergreen-review-v1` policy. No new approval permission or database transition is introduced.

Offline regressions cover missing selections, human confirmation, English and Spanish grant/date indicators, expired grants, fixture non-bypass, source relationship preservation, exact Unicode spans, and direct invocation of a disabled submit handler.

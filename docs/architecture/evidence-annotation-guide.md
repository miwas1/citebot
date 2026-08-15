# Evidence annotation guide

This guide defines the labels used by the versioned CiteBot evaluation datasets.
Annotations are claim-first: a reviewer evaluates the smallest independently
checkable statement, then records the exact source anchor(s) that justify it.

## Claim labels

Use one verdict per atomic claim:

- `supported`: the cited source entails the claim and preserves every material
  number, date, unit, sign, and negation.
- `partially_supported`: the source supports only part of the claim; the output
  must qualify the statement or split it into smaller claims.
- `contradicted`: the source conflicts with the claim, including changed
  numbers, dates, units, polarity, or explicit exceptions.
- `unsupported`: the source is present but does not justify the claim.
- `insufficient`: no usable source or source anchor was found.
- `uncertain`: the evidence is ambiguous and requires human adjudication.
- `stale`: the source is superseded or lacks the freshness required by the case.

## Anchor rules

Every material claim should point to the narrowest available anchor:

1. document version ID;
2. page or location marker;
3. element ID and character offsets when available;
4. a short quoted span that can be matched byte-for-byte after normalization.

Do not mark a citation valid merely because it points to the right document.
The page, element, span, and version must all be consistent. For tables, record
the table ID, row, and cell element IDs when available.

## High-risk checks

Annotators must explicitly compare numbers, dates, currency, units, signs,
negation, exceptions, and version freshness. A missing value is not evidence of
zero, approval, compliance, or absence. If a source is unreadable or OCR is
ambiguous, use `uncertain` or `insufficient`, not `supported`.

## Review labels

Set `requires_review` when the workflow is high-stakes, a material claim is
contradicted or uncertain, a required field is missing, or a calculation has a
reconciliation warning. Approval means a reviewer accepted the complete product
and its evidence chain; it does not mean the system has made an autonomous legal,
financial, or clinical decision.

## Dataset hygiene

Gold cases must be redacted, human-adjudicated, versioned, and reproducible.
Store parser, embedding, verifier, workflow, and schema hashes with every run.
Generated or synthetic cases are useful for regression probes but cannot replace
human-adjudicated release cases.

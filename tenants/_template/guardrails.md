# Guardrails (CHANGE ME)

Rules a generated page must never break for this tenant. The engine enforces
what it can deterministically (see `vocab.yaml`); this file is the human record
of why each rule exists and what is checked by eye.

## Absolute
- (CHANGE ME) List every word or claim that is banned outright, and why.

## Needs a source
- Any number, price, percentage, rating, or review count must carry a claim_id
  from `claims/verified.json`. No exceptions.

## Needs approval
- (CHANGE ME) Who signs off on a new verified claim, and how they are reached.

## Assets
- Images come only from the tenant asset index. No external images, no stock
  photography of clinical settings, no before/after pairs.

## Retrieval
- (CHANGE ME) If a knowledge base is wired in, list the page types the grounder
  may read and the types it may never read (customer, order, email, support
  ticket, conversation, chat log, person).

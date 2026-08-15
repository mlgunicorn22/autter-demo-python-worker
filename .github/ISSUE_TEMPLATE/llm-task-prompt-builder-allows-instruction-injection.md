---
name: "LLM task prompt builder allows instruction injection"
about: "Autter Sandbox challenge"
title: "LLM task prompt builder allows instruction injection"
labels: ["autter-sandbox", "ai safety"]
---

# Challenge: LLM task prompt builder allows instruction injection

## Context
This repository is an Autter Sandbox project. The feature is intentionally close to code teams ship in production, where small AI-generated changes can create security, reliability, or maintainability risks.

## Problem
User-controlled text is inserted into a system-like prompt without boundaries.

## Expected Behavior
The implementation should handle this case safely while preserving the existing product behavior and local developer workflow.

## Suggested AI Editor Prompt

```prompt
You are working in this repository.

Fix the issue where user-controlled text is inserted into a system-like prompt without boundaries.

Requirements:
- Preserve existing behavior unless the challenge explicitly requires changing it.
- Add or update tests that prove the fix works.
- Avoid unrelated refactors.
- Keep the solution small and production-like.
- Update docs only if the behavior changes.
- In the PR description, explain what changed, why it changed, and what risks remain.
```

## Acceptance Criteria

* [ ] The issue is fixed
* [ ] Relevant tests are added or updated
* [ ] Existing behavior is preserved
* [ ] No unrelated refactors
* [ ] The PR description explains the approach
* [ ] Autter review passes

## What Autter Should Review

* prompt injection risk
* missing tests or weak negative tests
* unsafe trust boundaries
* regressions in adjacent behavior
* merge readiness

---

Difficulty: Medium  
Category: AI safety

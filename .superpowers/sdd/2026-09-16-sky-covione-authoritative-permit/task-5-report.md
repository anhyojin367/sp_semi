# Task 5 report — bundled SkyCovione authoritative permit

## RED

Tests were added before the Task 5 implementation, including company/product scope, explicit non-target linking, cache fingerprint variation, one-resolution bridge reuse, metadata diagnostics, and the carried generic-basis guard.

```powershell
$env:PYTHONPATH='C:\Users\User\Documents\Codex\2026-08-26\c-users-user-desktop-sp-main\work\sky_fix\test_deps;C:\Users\User\Documents\Codex\2026-08-26\c-users-user-desktop-sp-main\worktrees\sky-covione-authoritative-permit'; & 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest -q tests/test_sky_covione_authoritative_permit.py::test_pipeline_scopes_authoritative_policy_to_exact_company_and_product tests/test_sky_covione_authoritative_permit.py::test_bridge_cache_key_includes_resolved_permit_fingerprint tests/test_sky_covione_authoritative_permit.py::test_pipeline_metadata_includes_permit_resolution_and_extraction_diagnostics tests/test_sky_covione_authoritative_permit.py::test_satisfaction_only_basis_is_not_concrete_even_when_verbatim_in_permit_context
```

Output: `7 failed in 2.72s`.

- Target `DocumentJudgePipeline` had no policy.
- `_artifact_key` rejected a permit fingerprint argument.
- Result metadata lacked `permit_paths`.
- Each of `만족해야 한다`, `충족해야 한다`, `conditions`, and `requirements` was incorrectly accepted when quoted verbatim from permit context.

The one-resolution test was mutation-checked by temporarily making the bridge resolve twice:

```powershell
... -m pytest -q tests/test_sky_covione_authoritative_permit.py::test_bridge_resolves_once_and_reuses_one_resolution_for_before_and_after
```

Output: `1 failed in 2.64s`, with `assert 2 == 1` for resolver calls. The single-resolution implementation was restored immediately after this RED check.

## GREEN

```powershell
$env:PYTHONPATH='C:\Users\User\Documents\Codex\2026-08-26\c-users-user-desktop-sp-main\work\sky_fix\test_deps;C:\Users\User\Documents\Codex\2026-08-26\c-users-user-desktop-sp-main\worktrees\sky-covione-authoritative-permit'; & 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest -q tests/test_permit_catalog.py tests/test_permit_ocr.py tests/test_permit_pdf_store.py tests/test_sky_covione_authoritative_permit.py
```

Output: `130 passed in 3.54s`.

The focused new regression subset also passed: `7 passed in 2.40s`; the single-resolution bridge regression passed after restoration: `1 passed in 2.31s`.

## Bundled PDF evidence

Copied byte-for-byte from `C:\Users\User\Documents\카카오톡 받은 파일\[더미허가문서]스카이코비원멀티주.pdf` to `sp_pdf_judger/permits/documents/sky_covione_multidose.pdf`.

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'sp_pdf_judger\permits\documents\sky_covione_multidose.pdf'
```

Output SHA-256: `C430105CE6C9BD0D9EF652912439735AA4BC01B86D94D74B97F745166FB56EA1` (matches the required lowercase value).

## Changed files

- `sp_pdf_judger/pipeline.py` — resolves permit catalog data for direct pipelines; passes the typed policy to both permit store and judge engine; records paths, policy, fingerprint, errors, and extraction diagnostics.
- `sp_judgement_bridge.py` — resolves once before cache construction; fingerprints cache data; reuses the same resolution object for both before/after phases; preserves existing artifact/status keys and adds resolution fields.
- `sp_pdf_judger/judgement.py` — closes the carried generic-basis guard with a concrete-evidence predicate.
- `sp_pdf_judger/permits/documents/sky_covione_multidose.pdf` — immutable bundled source permit.
- `tests/test_sky_covione_authoritative_permit.py` — pipeline, cache, metadata, one-resolution, non-target, and concrete-basis regressions.
- `.superpowers/sdd/2026-09-16-sky-covione-authoritative-permit/task-5-report.md` — this evidence report.

## Carried-ruling closure

The basis predicate now requires either a structured numeric condition or meaningful condition subject content after removing generic modality/compliance language. It rejects the satisfaction-only Korean and English boilerplate above even when verbatim context matches, while focused acceptance coverage retains real SkyCovione conditions: Component B `발색된 점이 확인되어야 한다`, `최소 21일`, `혈구응집반응이 없어야 한다`, `80% 이상`, and `36 ± 2 ℃`.

## Self-review

- Resolution is evaluated exactly once in `ensure_judgement_artifacts` before key construction; the tested object is passed to both phase constructors.
- Explicit paths retain resolver order and the catalog appends only when not already resolved.
- Authoritative mode originates only from the typed catalog policy; an explicit non-target permit keeps a store without a policy.
- Existing artifact/status keys remain; new fields are additive.
- `git diff --check` and `py_compile` completed without reported errors before commit.

## Commit

Implementation commit SHA: `b95998250f313577995e9950c5e001c7f36b9a30`. The report was then added to the same focused commit with `--amend`; the current commit SHA is supplied by the final task handoff because a commit cannot contain its own content-derived final SHA.

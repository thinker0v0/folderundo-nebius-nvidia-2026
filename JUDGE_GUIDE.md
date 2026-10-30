# Judge guide: local no-key reference walkthrough

This walkthrough uses **no AI and no provider calls**. It assigns destinations by file order to demonstrate review, exact approval, file moves, Undo, conflicts, and evidence export. It is not a classification benchmark. No hosted live application or remote judge access is supplied by these instructions.

## Start on Linux

Use Linux x86-64 with Python 3.12 and a local browser. Run from a fresh checkout only if you do not already have a retained store. Runtime dependencies are Python's standard library; no package installation or API key is needed.

Ordinary operation requires at least **1 GiB free after a 32 MiB job reserve**: at least 1,056 MiB free before admission. Accounted whole-product storage (including retained stores, directories and associated evidence), plus the 32 MiB job reserve and a separate 32 MiB retained reserve, must stay strictly below **3 GiB (3,221,225,472 bytes)**. A refusal must not be bypassed by deleting a store, resetting a ledger, or selecting bounded-live mode. Preserve existing records.

```sh
mkdir -p artifacts
chmod 700 artifacts
python3.12 -m venv --without-pip .venv
.venv/bin/python -m app.server --port 8080
```

The server prints the path of a private `open-url.txt` file. Open that file locally, then open its one-use URL in your browser on the same machine. Keep the URL private. The server binds to `127.0.0.1`; public reverse proxying is unsupported. Existing stores require the preservation/import procedure in [README.md](README.md), not a fresh-store workaround.

## Normal Apply and Undo

1. In **Registered fixture**, select **Quick mechanics walkthrough · 4 synthetic documents**. Click **Create synthetic copy**. Wait for creation and its automatic initial scan to finish; the controls stay disabled while work runs. If an explicit scan is needed and enabled, use **Scan files**.
2. Click **Build filing preview**. Inspect each source path, destination path, source SHA-256 and reason. The four documents include separate files with identical bytes; file identity matters beyond content alone.
3. Check **I reviewed every source, destination and source hash in this exact preview.** Then click **Approve exact plan**. Approval alone does not move files.
4. Click **Apply approved moves**, and wait for completion. Inspect the recorded journal and operation rows. The expected completed forward state is `APPLIED`.
5. Click **Undo committed moves** and wait. The expected normal journal state is `UNDONE`. Also inspect the current-file comparison: a journal state alone does not prove current restoration. A successful completed observation reports no drift and a manifest matching the recorded restored state.
6. Click **Download recorded evidence ZIP** and save this normal-Undo bundle. **Refresh journal** makes a new observation when enabled; stored status does not check for later file changes. The current source does not automatically rescan after completed operations.

If work fails, a current observation is unavailable, or drift is reported, retain the copy and available evidence. Do not describe that outcome as successful restoration. A **Download failure evidence** link, when shown, is a failure receipt rather than proof of restored files.

## Separate conflict journey

1. Keep the completed copy. Select the same four-document fixture and click **Preserve existing copies and create new**. Wait for the new copy, then repeat preview, consent, approval and Apply through `APPLIED`.
2. Expand **Optional conflict journey**. Click **Simulate occupied original path** and wait. This creates one fixed synthetic file at a known original path; do not manually overwrite or delete either file.
3. Click **Undo committed moves**. Inspect the recorded `CONFLICT` and the conflict path, reason, expected identity and current identity. This outcome is deliberately incomplete restoration: the occupied original path and the other file must both be preserved.
4. Download this copy's evidence ZIP separately. Leave both paths unchanged. Use **Preserve existing copies and create new** for another demonstration; it does not resolve the retained conflict.

## Verify an exported bundle offline

In a second terminal in this checkout, replace the example filename with the downloaded bundle's actual path. Keep the normal and conflict bundles distinct.

```sh
.venv/bin/python -m app.export --bundle /ABS/PATH/folderundo-evidence.zip --offline
```

The verifier prints JSON and exits nonzero on refusal. This checks archive consistency without extracting it, moving files, or making inference calls. It does not authenticate a historical event, turn a conflict into success, or establish the complete recovery matrix.

Optional public-fixture identity check (separate from the UI journey):

```sh
.venv/bin/python -m app.cli fixtures --manifest fixtures/manifest.json --verify
```

This writes `artifacts/fixture-checks.json`. Preserve an existing report before repeating it. The package includes 40 public development documents; it is not the full benchmark/test/evaluation collection.

## Current source versus historical NVIDIA video

The current-source runtime evidence covers two actual-worker, no-provider, four-document reference workflows: normal Apply/Undo restored all four files with exact bytes and metadata; a conflict case preserved five files and both conflicting paths. Both exports passed domain verification. That check produced no browser recording because the attempted Chromium launch lacked a usable sandbox.

The preserved successful NVIDIA video is a **historical 26.8-second workflow**, using a **6,000-token request configuration**. It predates the current source repairs. The later 2,000-token NVIDIA call completed core Apply/Undo, but its following scan failed after inode exhaustion; current repairs have not had another full NVIDIA-and-browser run. See [SUBMISSION.md](SUBMISSION.md) and [README.md](README.md) for provenance and limits. Full recovery acceptance remains unmet.

These instructions were checked against the shipped UI labels and handlers. Writing this guide did not execute a new app workflow, model call, or browser test.

The [109.8-second narrated submission video](https://github.com/thinker0v0/folderundo-nebius-nvidia-2026/releases/download/submission-20261030/folderundo-submission-draft.mp4) includes the historical clip and separately labeled reference evidence and limitations. This is recorded footage, not a current running app.

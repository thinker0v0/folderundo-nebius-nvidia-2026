# FolderUndo

Review AI filing suggestions, approve exact moves, and undo them with a recorded trail.

FolderUndo is a Linux prototype for people reorganizing document folders. The demo uses registered synthetic copies: NVIDIA proposes a destination, you inspect the source, destination and hash, and a separate Apply action moves the approved file. Undo reverses recorded moves. It is not a content backup.

**Track:** Best Apps and Agents. **License:** MIT. **Status:** local working prototype; public hosting and submission are pending.

## What actually ran

On October 30, 2026, a fresh call to `nvidia/Nemotron-3.5-Super-VL` through Nebius Token Factory used the enforced **2,000-token output limit**. It returned a valid proposal in **8.243 seconds**, with **1,650 input / 662 output tokens**. The browser submitted explicit approval, Apply and Undo for one 230-byte synthetic purchase document. Independent inspection found a successful Undo job, a durable UNDONE journal, valid retained Undo exports, and exact restoration of the original path, bytes and file identity.

The whole browser capture still failed: an automatic scan after successful Undo encountered exhausted file slots (inodes) in the bounded demonstration filesystem. That later scan remains incomplete. We preserve the failure and distinguish completed file restoration from full UI success. The current source removes the redundant automatic scan after completed operations; explicit Scan and Refresh+Scan remain. A full-HTML synthetic browser regression reproduced the old failure and verified the correction. Twelve focused offline checks cover new inode admission margins. A later one-line completion fix sets the post-result inode reserve to zero, so finishing admitted work does not require the margin for a new job. Byte limits, deadlines, result binding/schema validation, evidence publication and failure handling remain enforced. Fourteen additional offline cases use fake workers and simulated resource/persistence boundaries; they do not establish real-worker execution or disk durability. Current source includes these repairs. They have not received another full NVIDIA-and-browser run; the exhausted native store remains closed and preserved.

Seven actual Folder calls are retained: one invalid proposal, three incomplete or interrupted earlier journeys, two historical complete UI workflows, and the latest successful core Apply/Undo followed by a failed observation. The latest preserved continuous successful video is **26.8 seconds** and belongs to the historical **6,000-token request configuration**, with 1,647 input / 729 output tokens and a 49-check audit. It does not demonstrate the latest repairs. The earlier complete journey passed 44 checks. These are automated synthetic demonstrations, not human studies or general accuracy measurements. Offline reference examples, replay, synthetic UI tests and fresh provider inference are separately labeled.

A later October 30 check used actual app workers in the four-document reference mode, with no provider calls. Normal Apply and Undo restored all four files with exact bytes and metadata. A separate conflict case preserved all five files and both conflicting paths; both evidence exports passed domain verification. The recorded source hashes match the executable source in this archive. This checks these two reference workflows, not the full recovery matrix or NVIDIA inference. The attempted browser launch failed because Chromium had no usable sandbox, so this check produced no browser journey or recording. It is not a whole-product release score.

The local delivery materials include source, evidence and limitations. Public repository, video and demo URLs remain pending.

## Start without an API key

Use Linux x86-64 and Python 3.12. Runtime dependencies are Python's standard library. Ordinary host operation requires at least 1 GiB free on the storage filesystem after a 32 MiB job reserve (at least 1,056 MiB free before admission). Count retained storage, directories and associated Folder evidence toward the 3 GiB (3,221,225,472-byte) whole-product cap: accounted usage plus the 32 MiB job reserve and a separate 32 MiB retained reserve must stay strictly below that cap. These prerequisites also apply to the ordinary live command below. From the project directory:

```sh
python3.12 -m venv --without-pip .venv
.venv/bin/python -m app.server --port 8080
```

Use a fresh local project only if you do not already have a retained store. Startup prints the path to a private `open-url.txt` file. Open its one-use URL in your own browser. Select the four-document reference walkthrough, create a copy, build a preview, approve, Apply, then Undo. The reference walkthrough uses no AI and makes no classification-performance claim.

Keep the URL private. The server binds to `127.0.0.1`; public reverse proxying is unsupported. Existing stores must be preserved. First-time import of an old store requires its writers to be stopped and the documented explicit `--legacy-quiescent` attestation; do not delete databases or sidecars to bypass a refusal.

## Enable real NVIDIA inference

The broker and app run as the same dedicated non-root owner. The key stays in an owner-only file outside this repository. Never put its value in a command, source file, screenshot or submission.

Create private directories outside the repository for the socket, durable ledger and app storage. Each directory must belong to that owner with mode `0700`; key and ledger files require mode `0600`. Reuse your existing ledger and store. Only a genuinely new owner with no prior ledger may use `tools/live/init_ledger.py --ledger /ABS/PRIVATE/ledger/ledger.json --new-owner-no-prior-ledger` once.

Run the broker in one terminal, replacing paths and choosing a cumulative call ceiling:

```sh
.venv/bin/python tools/live/broker.py --key-file /ABS/PRIVATE/api_key --socket /ABS/PRIVATE/run/broker.sock --ledger /ABS/PRIVATE/ledger/ledger.json --budget-usd 25 --max-calls 7
```

Run the app in another terminal, from the same project directory:

```sh
PYTHONPATH="$PWD" .venv/bin/python tools/live/live_server.py --broker-socket /ABS/PRIVATE/run/broker.sock --storage-dir /ABS/PRIVATE/storage --database-name service.sqlite --port 8080
```

Open this process's new private bootstrap URL, choose **B1 · Completed synthetic purchase**, and follow the preview → exact approval → Apply → Undo flow. B1 sends the registered filename and synthetic excerpt to Nebius. It makes one proposal call with no automatic retry. Model, latency and token usage appear with the result. An invalid proposal or uncertain provider result stops the flow.

`--max-calls` counts all historical Folder calls in that ledger. `--budget-usd` covers historical reconciled cost plus outstanding reservations, using documented list-price estimates. Neither setting reads your account balance or controls charges from other applications. Unknown usage halts further dispatch. Do not replace or reset an existing ledger.

## Check the public fixture package

The archive includes all 40 public development documents, both source manifests,
and the implementation of the fixture and recovery-probe commands. In a **fresh
extracted copy**, prepare the private output directory and verify the fixtures:

```sh
mkdir -p artifacts
chmod 700 artifacts
.venv/bin/python -m app.cli fixtures --manifest fixtures/manifest.json --verify
```

This checks public file identities and writes `artifacts/fixture-checks.json`;
it makes no model calls or file moves. Preserve an existing report before
repeating it. The source package does not include private holdout bodies, oracle
values or private generators. Recovery fixture names are registered aliases,
not extra directories that need creating.

`probe-primitive` and `probe-recovery` remain available in the CLI source. Their
inclusion is not a passing test result: they create synthetic workspaces and
evidence and need their own time/storage admission. The retained original full
recovery run must not be restarted or its limits reset by this packaging repair.

## Model and executor responsibilities

Nebius Token Factory hosts NVIDIA Nemotron. The model returns structured filing suggestions; it cannot approve a plan, run shell commands or move files. Local validation checks the proposal. Exact approval binds paths and content hashes. The local executor performs approved movements with a durable journal; local Undo follows that journal without another model call.

Download an evidence ZIP and check its internal consistency offline:

```sh
.venv/bin/python -m app.export --bundle folderundo-evidence.zip --offline
```

Archive verification does not authenticate a historical event or make a fresh model call.

## Limits

- `--bounded-live-runtime` is a trusted-operator option only for a separately admitted native demonstration filesystem of at most 16 MiB; it is not part of ordinary host setup. It requires at least 8 MiB free there, at least 1 GiB free on the host, and accounted usage plus 8 MiB and the separate 32 MiB reserve strictly below the same 3 GiB (3,221,225,472-byte) cap. Admission requires 128 free inodes for a new copy and 32 for other work. These margins do not guarantee completion: evidence writes can still fail. Do not use the option to bypass a resource refusal or reopen an exhausted store; retain all existing records.
- Only registered synthetic text copies are supported. Personal folders, arbitrary uploads and real customer documents are not connected.
- Undo restores recorded moves, not edited or deleted contents. Changed or occupied paths may produce a conflict; a conflict is not successful restoration.
- The audited original recovery collection covers 1,168 of 1,204 cases; 36 remain NOT_RUN. The retained controller exceeded its 90-minute limit by 4.172783 seconds. Full recovery acceptance remains unmet.
- Real-user benefit, matched manual comparisons and general classification accuracy are unproven. The live success involves one synthetic file and an automated operator.
- The v5 rendered-interface checks passed 147 assertions across 1,280px and 390px keyboard journeys, including scan errors and Undo conflicts. They used synthetic server responses with the real HTML, CSS and JavaScript and preceded the later budget-alert JavaScript change. Only narrow offline formatter checks followed that change; the 147 checks do not validate the latest interface. These checks are not live-backend validation, screen-reader usability evidence or WCAG certification; horizontal keyboard scrolling inside wide content regions was not tested.
- Account balance, credit expiry, automatic-billing settings, public demo availability and final submission remain unverified.

The source package contains public development documents and their unchanged metadata. It omits private holdout document bodies, oracle data, runtime stores and credentials; it is not the full benchmark evidence collection.

`LICENSE_MANIFEST.json` and `THIRD_PARTY_NOTICES.md` record locally bound dependency-license provenance. They are not evidence of a fresh install of this archive. The runtime uses the Python standard library; the included dependency lock records development dependencies. This public runtime source package is not the complete benchmark, test or evaluation package.

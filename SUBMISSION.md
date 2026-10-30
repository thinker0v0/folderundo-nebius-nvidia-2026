# FolderUndo submission source — October 30, 2026

FolderUndo is a Linux prototype for reviewing AI filing suggestions, approving exact file moves, and undoing recorded moves. The intended track is Best Apps and Agents. NVIDIA Nemotron supplies suggestions through Nebius Token Factory; approval and file operations are local. The supported demonstration uses registered synthetic document copies.

## Source and video versions

This repository preserves the public runtime source package `folderundo-source-current-20261030.zip`, SHA-256 `0920314559e590ceb1ab47f885ff7935690609314b86ee7a95f19b453e1d03f7`. Its 71 payload files and package manifest are unchanged. Submission-only additions are this note, `.gitignore`, `JUDGE_GUIDE.md`, and `VERIFICATION.json`. `PACKAGE_MANIFEST.json` describes the original archive payload, not these additions. Git does not retain the archive's empty `artifacts/` directory; create it using the README command before fixture verification.

The latest preserved continuous successful video is a historical 26.8-second synthetic workflow using the 6,000-token request configuration (1,647 input / 729 output tokens). It does not show the current source repairs. No new recording or fresh inference is implied by this source publication.

The later October 30 NVIDIA call used a 2,000-token output limit. Core Apply and Undo completed and the original file was restored, but the following automatic scan failed because the bounded demonstration filesystem exhausted its inodes. Current source removes that redundant scan and includes admission/completion repairs. Those repairs have not received another full NVIDIA-and-browser run.

A later check with actual app workers exercised normal Apply/Undo and a conflict in the four-document reference mode without provider calls. It produced no browser recording. These two workflows do not establish the full recovery matrix or new NVIDIA inference. See `README.md` for the exact scope and remaining limitations.

## What this repository contains

MIT-licensed runtime source, browser assets, 40 public development documents and their manifests, dependency notices, and setup instructions. Runtime dependencies are Python's standard library. The development dependency lock and license provenance do not establish a fresh install of this checkout.

This is not the full benchmark, test, or evaluation collection. Private holdout bodies, oracle data, runtime stores, credentials, retained evidence bundles, and videos are not included. Historical check counts are provenance descriptions in the README, not passing tests executed from this checkout. Full recovery acceptance remains unmet (1,168 of 1,204 cases audited; 36 not run; the retained controller exceeded its time limit).

## Try it locally

Follow `README.md` for the no-key four-document reference walkthrough and the separately configured NVIDIA path. The server binds to localhost; public reverse proxying is unsupported. This repository is a source release, not a hosted live application. Keep existing stores and ledgers intact and keep private bootstrap URLs and keys outside public materials.

Publication alone does not verify final competition eligibility or submission acceptance. The README's publication-pending statements describe the source-package preparation snapshot; the repository's actual public availability and any separately supplied video URL must be checked at submission time.

## Public materials

- Source: https://github.com/thinker0v0/folderundo-nebius-nvidia-2026
- Downloadable test build and 111.64-second narrated video: https://github.com/thinker0v0/folderundo-nebius-nvidia-2026/releases/tag/submission-20261030
- Judge walkthrough: [JUDGE_GUIDE.md](JUDGE_GUIDE.md)

Individual entry. The owner reports a project start date of October 23, 2026. Public video availability on GitHub does not satisfy the separate public YouTube requirement. Free live AI access during judging remains unestablished.

## Fresh extraction and browser verification

On October 30, the published ZIP was extracted into a clean directory and used with a Python 3.12 no-pip virtual environment. All 40 public fixture files verified. A first actual-worker reference validation completed 13 jobs; the separate supported-browser journey completed 15 jobs. Normal Undo restored four original paths, bytes and file identities. The conflict journey preserved all four original identities across five retained files, with three original paths restored. Both browser-downloaded evidence bundles verified. No provider calls were made; this does not establish the full current NVIDIA journey.

The first validation's disk-monitor controller failed when a transient SQLite WAL disappeared during enumeration. The already-launched validation completed independently in 2.61 seconds; that controller failure remains retained. The browser runtime later closed normally after inspection. Its only recorded browser console error was a missing favicon. See [VERIFICATION.json](VERIFICATION.json) for the bounded results.

## Narration upgrade

The current 111.64-second video uses Microsoft Ava neural English narration. The canonical Opus 5.5 script, actual 26.8-second footage at 1×, and product limitations are unchanged. Captions follow speech-provider sentence timing. Encoded audio measured -16.19 LUFS and -1.73 dBTP; decoding passed. These are technical checks, not a subjective listening evaluation. Earlier video versions are retained in the release.

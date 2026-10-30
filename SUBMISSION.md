# FolderUndo submission source — October 30, 2026

FolderUndo is a Linux prototype for reviewing AI filing suggestions, approving exact file moves, and undoing recorded moves. The intended track is Best Apps and Agents. NVIDIA Nemotron supplies suggestions through Nebius Token Factory; approval and file operations are local. The supported demonstration uses registered synthetic document copies.

## Source and video versions

This repository preserves the public runtime source package `folderundo-source-current-20261030.zip`, SHA-256 `0920314559e590ceb1ab47f885ff7935690609314b86ee7a95f19b453e1d03f7`. Its 71 payload files and package manifest are unchanged. Only this submission note and `.gitignore` are added. `PACKAGE_MANIFEST.json` describes the original archive payload, not these two additions. Git does not retain the archive's empty `artifacts/` directory; create it using the README command before fixture verification.

The latest preserved continuous successful video is a historical 26.8-second synthetic workflow using the 6,000-token request configuration (1,647 input / 729 output tokens). It does not show the current source repairs. No new recording or fresh inference is implied by this source publication.

The later October 30 NVIDIA call used a 2,000-token output limit. Core Apply and Undo completed and the original file was restored, but the following automatic scan failed because the bounded demonstration filesystem exhausted its inodes. Current source removes that redundant scan and includes admission/completion repairs. Those repairs have not received another full NVIDIA-and-browser run.

A later check with actual app workers exercised normal Apply/Undo and a conflict in the four-document reference mode without provider calls. It produced no browser recording. These two workflows do not establish the full recovery matrix or new NVIDIA inference. See `README.md` for the exact scope and remaining limitations.

## What this repository contains

MIT-licensed runtime source, browser assets, 40 public development documents and their manifests, dependency notices, and setup instructions. Runtime dependencies are Python's standard library. The development dependency lock and license provenance do not establish a fresh install of this checkout.

This is not the full benchmark, test, or evaluation collection. Private holdout bodies, oracle data, runtime stores, credentials, retained evidence bundles, and videos are not included. Historical check counts are provenance descriptions in the README, not passing tests executed from this checkout. Full recovery acceptance remains unmet (1,168 of 1,204 cases audited; 36 not run; the retained controller exceeded its time limit).

## Try it locally

Follow `README.md` for the no-key four-document reference walkthrough and the separately configured NVIDIA path. The server binds to localhost; public reverse proxying is unsupported. This repository is a source release, not a hosted live application. Keep existing stores and ledgers intact and keep private bootstrap URLs and keys outside public materials.

Publication alone does not verify final competition eligibility or submission acceptance. The README's publication-pending statements describe the source-package preparation snapshot; the repository's actual public availability and any separately supplied video URL must be checked at submission time.

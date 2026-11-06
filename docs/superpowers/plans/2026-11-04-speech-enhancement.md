# Speech Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add local Soniqo DeepFilterNet3 denoise and Sidon restore commands plus reusable Python APIs.

**Architecture:** `voice_tools.enhance` owns validation, safe temporary output publication, and Soniqo subprocess invocation. The existing CLI dispatches two new subcommands and the existing extract flow remains unchanged.

**Tech Stack:** Python 3.10+ standard library, local Soniqo `speech` executable, unittest, FFmpeg/ffprobe for output inspection.

**Spec:** `docs/superpowers/specs/2026-11-04-speech-enhancement-design.md`

## Global Constraints

- Input is a readable audio file accepted by Soniqo; output is WAV.
- Do not overwrite an existing output unless `overwrite=True` / `--overwrite` is explicit.
- Do not overwrite the input, including aliases, symlinks, and hardlinks.
- Do not chain denoise and restore implicitly.
- No Python runtime dependency is added.

### Task 1: Add failing enhancement tests

**Files:**
- Create: `tests/test_enhance.py`

- [x] Test default paths, command construction, option forwarding, output conflicts, input aliases, failed subprocess cleanup, and CLI routing.
- [x] Run `python3 -m unittest tests.test_enhance -v` and confirm it fails because the module is absent.

### Task 2: Implement enhancement module and exports

**Files:**
- Create: `voice_tools/enhance.py`
- Modify: `voice_tools/__init__.py`

- [x] Implement validation and atomic publication, including complete PCM WAV validation.
- [x] Implement `denoise_audio` and `restore_audio` with typed signatures from the spec.
- [x] Run the focused tests until green (18 tests, including review follow-up cleanup cases).

### Task 3: Wire CLI and documentation

**Files:**
- Modify: `voice_tools/__main__.py`
- Modify: `README.md`
- Modify: `pyproject.toml`

- [x] Add `denoise` and `restore` parsers and error handling.
- [x] Document Soniqo installation, commands, API, output characteristics, and why music separation is excluded.
- [x] Run the full unittest suite (30 tests).

### Task 4: Validate against the supplied recording

**Files:**
- Generated: `outputs/20261028井上和_0-100_denoised.wav`
- Generated: `outputs/20261028井上和_0-100_restored.wav`

- [x] Run the new CLI on the supplied sample without changing the source (SHA-256 unchanged).
- [x] Use ffprobe and a full decode to verify denoise's 100-second output.
- [x] Run independent fp16 restore on the original: 99.794583 seconds, valid 48 kHz mono PCM WAV; record the upstream duration difference without changing the model output.

## Environment and verification

This directory is not a Git repository. No branch or commit was created. Tests use only the standard library plus FFmpeg for the existing extraction cases. They never download Soniqo models.

Real-file acceptance details are in `docs/speech-enhancement-validation.md`.

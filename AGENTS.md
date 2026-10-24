# Agent instructions

## Project environment

- Use the conda environment `autoweb` for running project commands, scripts, dependency operations, and tests. Prefer `conda run -n autoweb <command>` or activate `autoweb` before running commands. Do not use another Python environment implicitly.

## Project scope

- Work in `D:/RAJ ARYAN/Codec/AutoWeb` from now on. AutoWeb is an independent repository.
- Legacy Gmail and browser sources remain under `D:/RAJ ARYAN/Codec/scaile-tech/scaile-backlink/gmail-auto/` and `shadowdom-auto/` respectively. Consult them only when migrating their corresponding modules; they are not runtime dependencies.
- Ignore `trial-codes/` (also referred to as "trial codes"). It contains historical experiments and is not relevant to future analysis or implementation. Do not treat it as a source of requirements or production code.
- The intended direction is a unified framework for end-to-end website login, combining browser automation with email verification retrieval.

## Code design

- Keep code clean, readable, and modular.
- Use focused functions and modules for distinct tasks, with clear responsibilities and interfaces.
- Separate login orchestration, browser/session management, UI interaction, email access, verification extraction, and configuration.
- Prefer reusable components over duplicated or hardcoded site-specific flows.

## Current authorization

- The user has authorized Phase VI (testing framework entry points and diagnostics) of IMPLEMENTATION_PLAN.md. Phase VI is complete. Do not start Phase VII until the user requests it.
- Active framework code lives in src/autoweb/. Phases I-VI and the standalone repository migration are complete. Phase VI verification passed 272 deterministic tests with one live test skipped. See docs/TESTING.md for the public pytest fixtures, explicit live selection, and sensitive trace handling. Legacy projects and OAuth files remain in the original repository. Practice Test Automation password login and Expand Testing live Gmail OTP login have passed. The user completed Gmail OAuth consent explicitly; do not initiate OAuth consent automatically.
- Use pyproject.toml as the single dependency definition. Avoid browser, Gmail, or AI client initialization during package imports.

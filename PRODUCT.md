# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

The existing framework is Python with Playwright, configured through the root
pyproject.toml. The user selected a local web app and the Split workspace
composition with a code-led implementation. The UI uses React, TypeScript, and
Vite in apps/web/. Python API transport is a later integration milestone.

## Users

The initial user is AutoWeb's developer, running the UI and automation on the same
computer and watching login tests to understand and diagnose their behavior.

## Product Purpose

Provide a visual interface around reusable website login workflows. The first UI
must make watching and diagnosing a login run easy, with a visible headed browser
and understandable execution feedback.

## Operating Context

Local computer first. The user intends to keep the Python framework and frontend
in one monorepo. Active framework code currently lives in src/autoweb/.

## Capabilities and Constraints

- Existing modules separate configuration, browser sessions and actions, login
  orchestration, Gmail verification, optional AI assistance, and reporting.
- Browser settings already support headed execution and slow motion. Reports
  contain completed step outcomes; a frontend live-event interface is not yet built.
- Python project commands use the conda environment autoweb; pyproject.toml remains
  the single Python dependency definition.
- Live tests against a new website require explicit user confirmation. Failed
  email-request actions must not be retried automatically. Gmail OAuth consent
  must not start automatically.
- The UI uses simulated runs. It does not launch Python, a browser, Gmail, or AI
  clients. History and settings reset on refresh.

## Brand Commitments

Graphite and teal is the user-selected palette. The user selected the Split
workspace: left navigation, a step list, and an inspector with Details, Evidence,
and Events tabs.

## Evidence on Hand

README.md describes the package and its current boundaries. docs/TESTING.md covers
deterministic and opt-in live tests. docs/PHASE7_LIVE_VALIDATION.md records successful
live OTP/session/logout validation and incomplete magic-link and AI validation.

## Product Principles

- Explain the action being performed and its observed outcome.
- Preserve reusable Python workflow logic behind the visual interface.
- Keep credentials and verification secrets out of execution feedback.
- Distinguish a workflow failure from a runner or connection failure.

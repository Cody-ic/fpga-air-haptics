# Repository Guidelines

## Project Structure & Module Organization

This workspace prepares for the 2026 FPGA Innovation Design competition. It currently contains documentation only:

- `README.md`: project overview, current direction, and documentation links.
- `设计思路.md`: current architecture, interfaces, validation plan, and open decisions.
- `FPGA赛道选题建议(1).md`: primary research document covering rules, vendor platforms, previous winners, candidate projects, milestones, and references.
- `CLAUDE.md`: existing project context and assistant guidance.
- `AGENTS.md`: contributor guidelines.

There are no source, test, or asset directories. Maintain current decisions in `设计思路.md` and background research in the original document; add implementation directories when development begins.

## Build, Test, and Development Commands

No build system, simulator, package manager, formatter, or automated test runner is configured. Use these commands from the workspace root in PowerShell:

- `rg --files --hidden`: list workspace files; requires ripgrep.
- `Get-Content -LiteralPath 'FPGA赛道选题建议(1).md' -Encoding utf8`: read the research document.
- `rg -n '^#{1,3} ' --glob '*.md'`: inspect Markdown headings.

Quote filenames containing spaces or parentheses. Document reproducible build and simulation commands when implementation tooling is added.

## Documentation Style & Naming Conventions

Write research content in Chinese and save Markdown as UTF-8. Preserve existing filenames, numbered sections, comparison tables, and technical abbreviations such as PL, PS, STFT, and GCC-PHAT. Use ATX headings (`##`, `###`), hyphen bullets, and blank lines around blocks. Align nested list indentation with the parent item's text; avoid tabs.

Use absolute dates such as `2026-11-04`. Cite new factual or technical claims and add corresponding links to section 9. Label vendor guidance by publication year; distinguish historical guidance from confirmed 2026 rules.

## Testing & Validation Guidelines

No testing framework or coverage threshold exists. Before submitting documentation changes, preview Markdown, check tables and section references, and verify changed claims against cited sources. Check units, hardware specifications, and deadline consistency. Distinguish proposed targets from measured results, and record any sources you could not verify.

## Commit & Pull Request Guidelines

Use concise imperative messages, for example `docs: clarify microphone bandwidth limits`. This is a suggested convention for the new repository. Keep changes focused. Pull requests should describe the change, rationale, affected sections, supporting sources, and validation performed; link relevant issues when available.

## Project Decision Constraints

As of 2026-09-24, the team has selected Tang Mega 60K for the haptic display. Follow `设计思路.md`: FPGA handles real-time processing, external buttons enable independent operation, and serial software supports configuration and validation. Array size and components remain provisional; prioritize single-focus validation before extensions.

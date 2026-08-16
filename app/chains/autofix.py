"""
Template-based remediation.

For detector categories with a single, mechanical, well-understood fix,
generate the patch by transforming the ACTUAL flagged source text rather
than asking the LLM to freehand new code from a description — this is
what produced the invalid `.call{value: amount}()` (missing required
argument) and the escaped-quote corruption seen in earlier runs. A
template transform of real source text can't drop a token that was never
there to begin with.

Every template output is still run through the same
`explainer_chain._validate_fix_snippet` check before being trusted. If a
template can't confidently apply (pattern not found, ambiguous structure),
it returns None and the caller falls back to LLM generation.
"""
from __future__ import annotations

import re


def autofix_tx_origin(source_lines: str) -> str | None:
    """tx.origin -> msg.sender is unambiguous and safe as a pure token
    substitution in the vast majority of real-world cases."""
    if "tx.origin" not in source_lines:
        return None
    fixed = re.sub(r"\btx\.origin\b", "msg.sender", source_lines)
    # Strip the ">>> " / "    " annotation markers this pipeline adds so the
    # snippet is clean code, not annotated display text.
    fixed = "\n".join(
        line[4:] if line.startswith((">>> ", "    ")) else line
        for line in fixed.splitlines()
    )
    return fixed.strip() or None


def autofix_solc_version(recommended_min_version: str | None) -> str | None:
    if not recommended_min_version:
        return None
    return f"pragma solidity ^{recommended_min_version};"


_FUNC_LINE_RE = re.compile(r"^\s*(?:>>> |    )?(.*)$")


def autofix_reentrancy_cei(
    source_lines: str, call_line_no: int, write_line_no: int
) -> str | None:
    """
    Checks-Effects-Interactions reorder: move the external-call statement
    to AFTER the state-write statement it currently precedes. Works on the
    actual annotated source block this pipeline already extracted (see
    slither_runner._render_annotated_blocks), keyed by the real line
    numbers Slither reported — not a free-standing rewrite.

    This is intentionally conservative: it only reorders when it can
    unambiguously identify both statements as complete lines in the block
    (each line's trailing `;` marks a full statement — true for the
    common single-line call/write case this template targets). Anything
    more structurally complex (multi-line call args, other statements
    between them that reference intermediate state) returns None and
    falls back to LLM-authored remediation, which is safer than a
    template silently producing a subtly wrong reorder.
    """
    lines = source_lines.splitlines()
    # Skip a leading display header this pipeline's own renderer prepends
    # (e.g. "Flagged lines 16, 19 (marked >>>) of X.sol:") — it's not code.
    if lines and lines[0].rstrip().endswith(":") and not re.search(r"[;{}]\s*:?\s*$", lines[0]):
        lines = lines[1:]
    stripped = [_FUNC_LINE_RE.match(l).group(1) for l in lines]

    call_idx = write_idx = None
    for i, raw in enumerate(lines):
        # Recover the original (1-based) line number isn't tracked here by
        # design — annotated blocks don't retain absolute line numbers per
        # row, so this template instead matches by *relative content*: the
        # call line contains `.call(`/`.delegatecall(`/`.staticcall(`, and
        # we take the first state-decrement/increment assignment after it
        # as the write to move ahead of it. This is a heuristic, not a
        # numbered lookup — kept intentionally narrow in scope.
        if re.search(r"\.(call|delegatecall|staticcall)\s*(\{[^{}]*\})?\s*\(", stripped[i]):
            call_idx = i
            break
    if call_idx is None:
        return None

    for j in range(call_idx + 1, len(stripped)):
        if re.search(r"\w+(\[[^\]]+\])?\s*[-+]=\s*\w+\s*;", stripped[j]):
            write_idx = j
            break
    if write_idx is None:
        return None

    # Require the call to occupy exactly one line and nothing structurally
    # unexpected sits between call and write besides a trailing check
    # (e.g. `require(ok, ...)`) — bail to LLM fallback otherwise.
    between = stripped[call_idx + 1 : write_idx]
    if any(b.strip() and not re.match(r"^(require|assert)\s*\(", b.strip()) for b in between):
        return None

    reordered = (
        stripped[:call_idx]
        + [stripped[write_idx]]
        + stripped[call_idx:write_idx]
        + stripped[write_idx + 1 :]
    )
    result = "\n".join(l for l in reordered if l.strip())
    return result or None
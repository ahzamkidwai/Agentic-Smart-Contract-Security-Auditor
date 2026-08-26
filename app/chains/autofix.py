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


def _replace_outside_comments(line: str, pattern: str, repl: str) -> str:
    """
    Apply a regex substitution only to the code portion of a line, leaving
    any trailing `//` comment untouched. Prevents the bug where
    substituting a token (e.g. tx.origin -> msg.sender) also mangles a
    comment that mentions that same token, producing nonsensical text like
    "msg.sender used for authorization instead of msg.sender".
    """
    in_str: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            i += 1
            continue
        if line[i : i + 2] == "//":
            code, comment = line[:i], line[i:]
            return re.sub(pattern, repl, code) + comment
        i += 1
    return re.sub(pattern, repl, line)


def autofix_tx_origin(source_lines: str) -> str | None:
    """tx.origin -> msg.sender is unambiguous and safe as a pure token
    substitution in the vast majority of real-world cases."""
    if "tx.origin" not in source_lines:
        return None

    lines = source_lines.splitlines()
    # Skip a leading display header this pipeline's own renderer prepends
    # (e.g. "Flagged lines 58, 59 ... of X.sol:") — it's not code. Same
    # heuristic as autofix_reentrancy_cei below.
    if lines and lines[0].rstrip().endswith(":") and not re.search(r"[;{}]\s*:?\s*$", lines[0]):
        lines = lines[1:]

    fixed_lines = []
    for line in lines:
        # Strip the ">>> " / "    " annotation markers this pipeline adds.
        code = line[4:] if line.startswith((">>> ", "    ")) else line
        fixed_lines.append(_replace_outside_comments(code, r"\btx\.origin\b", "msg.sender"))

    result = "\n".join(fixed_lines).strip()
    return result or None


def autofix_solc_version(recommended_min_version: str | None) -> str | None:
    if not recommended_min_version:
        return None
    return f"pragma solidity ^{recommended_min_version};"


_FUNC_LINE_RE = re.compile(r"^\s*(?:>>> |    )?(.*)$")
_ASSIGN_WRITE_RE = re.compile(r"^\s*(\w+)(?:\[[^\]]+\])?\s*(?:=|[-+*/]=)\s*.+;$")
_DELETE_WRITE_RE = re.compile(r"^\s*delete\s+(\w+)\b")


def _write_target_var(line: str) -> str | None:
    """Returns the base variable name a line writes to, if it's a
    plain/compound assignment or a `delete` statement — else None."""
    m = _ASSIGN_WRITE_RE.match(line)
    if m:
        return m.group(1)
    m = _DELETE_WRITE_RE.match(line)
    if m:
        return m.group(1)
    return None


def autofix_reentrancy_cei(
    source_lines: str, call_line_no: int, write_line_no: int
) -> str | None:
    """
    Checks-Effects-Interactions reorder: move the state-write statement(s)
    that currently follow the external call to BEFORE it. Works on the
    actual annotated source block this pipeline already extracted (see
    slither_runner._render_annotated_blocks).

    Critical correctness case this handles: if the external call's own
    arguments reference the variable being reset (e.g.
    `winner.call{value: prizePool}("")` followed by `prizePool = 0;`),
    naively moving the reset line before the call would zero the value
    the call is about to send — silently turning a reentrancy fix into a
    "sends 0 ETH" bug. When that's detected, a local snapshot variable is
    introduced to hold the value before it's reset, and the call is
    rewritten to reference the snapshot instead of the now-reset state
    variable.

    Note: the snapshot is declared `uint256` — the correct type for the
    ETH-amount case this template targets, but if the variable being
    reset is a different type, verify it before applying. This is
    intentionally conservative: anything structurally more complex than a
    run of assignment/delete statements (optionally interleaved with
    require/assert) after the call returns None and falls back to
    LLM-authored remediation.
    """
    lines = source_lines.splitlines()
    # Skip a leading display header this pipeline's own renderer prepends
    # (e.g. "Flagged lines 16, 19 (marked >>>) of X.sol:") — it's not code.
    if lines and lines[0].rstrip().endswith(":") and not re.search(r"[;{}]\s*:?\s*$", lines[0]):
        lines = lines[1:]
    stripped = [_FUNC_LINE_RE.match(l).group(1) for l in lines]

    call_idx = None
    for i in range(len(stripped)):
        if re.search(r"\.(call|delegatecall|staticcall)\s*(\{[^{}]*\})?\s*\(", stripped[i]):
            call_idx = i
            break
    if call_idx is None:
        return None

    # Walk forward from the call collecting every write-like statement,
    # allowing require/assert and blank lines to be interspersed (they
    # stay adjacent to the call). Anything else bails out to the LLM
    # fallback rather than guessing at an unfamiliar structure.
    write_idxs: list[int] = []
    write_vars: list[str] = []
    for j in range(call_idx + 1, len(stripped)):
        line = stripped[j]
        if not line.strip():
            continue
        var = _write_target_var(line)
        if var:
            write_idxs.append(j)
            write_vars.append(var)
            continue
        if re.match(r"^(require|assert)\s*\(", line.strip()):
            continue
        if line.strip() in ("}", "});"):
            break  # end of the enclosing block — stop collecting, not an error
        return None  # unrecognized statement shape — don't guess
    if not write_idxs:
        return None

    # Does the call line reference any variable that's about to be reset?
    # If so, snapshot it before the reset and rewrite the call to use the
    # snapshot — this is what prevents the "sends 0 ETH" bug.
    call_line = stripped[call_idx]
    vars_needing_snapshot = list(
        dict.fromkeys(v for v in write_vars if re.search(rf"\b{re.escape(v)}\b", call_line))
    )
    snapshot_decls = [
        f"uint256 __payout_{v} = {v}; // NOTE: verify this matches {v}'s actual declared type"
        for v in vars_needing_snapshot
    ]
    for v in vars_needing_snapshot:
        call_line = re.sub(rf"\b{re.escape(v)}\b", f"__payout_{v}", call_line)

    # Lines between the call and the first write that aren't write-like
    # themselves (i.e. require/assert checking the call's own result) —
    # keep these immediately after the (rewritten) call, in place.
    trailing_checks = [
        stripped[k] for k in range(call_idx + 1, write_idxs[0]) if stripped[k].strip()
    ]
    # Anything after the last collected write that wasn't itself a write.
    tail = [
        stripped[k]
        for k in range(write_idxs[-1] + 1, len(stripped))
        if k not in write_idxs and stripped[k].strip()
    ]

    reordered = (
        stripped[:call_idx]
        + snapshot_decls
        + [stripped[k] for k in write_idxs]
        + [call_line]
        + trailing_checks
        + tail
    )
    result = "\n".join(l for l in reordered if l.strip())
    return result or None
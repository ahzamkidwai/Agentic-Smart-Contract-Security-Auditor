"""
Unit tests for the normalizer layer - these don't require Slither/Mythril
to actually be installed, they test the parsing logic against mock JSON
shaped like real tool output.
"""
import os

from analyzers.normalizer import normalize_slither, normalize_mythril, is_pure_interface
from schemas import Severity

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

MOCK_SLITHER_OUTPUT = [
    {
        "check": "reentrancy-eth",
        "impact": "High",
        "description": "Reentrancy in VulnerableBank.withdraw(uint256)",
        "elements": [
            {
                "name": "VulnerableBank",
                "source_mapping": {
                    "filename_relative": "VulnerableBank.sol",
                    "lines": [12, 13, 14],
                },
            }
        ],
    }
]

MOCK_MYTHRIL_OUTPUT = [
    {
        "title": "State access after external call",
        "swc-id": "107",
        "severity": "High",
        "contract": "VulnerableBank",
        "lineno": 14,
        "description": "A call to an external contract is followed by a state change.",
    }
]


def test_normalize_slither_maps_swc_id_and_severity():
    findings = normalize_slither(MOCK_SLITHER_OUTPUT)
    assert len(findings) == 1
    f = findings[0]
    assert f.swc_id == "SWC-107"
    assert f.severity == Severity.high
    assert f.contract_file == "VulnerableBank.sol"
    assert f.line_start == 12
    assert f.line_end == 14


def test_normalize_mythril_uses_native_swc_id():
    findings = normalize_mythril(MOCK_MYTHRIL_OUTPUT, "VulnerableBank.sol")
    assert len(findings) == 1
    f = findings[0]
    assert f.swc_id == "SWC-107"
    assert f.severity == Severity.high
    assert f.line_start == 14


def test_is_pure_interface_true_for_interface_only_file():
    path = os.path.join(FIXTURES_DIR, "ITokenReceiver.sol")
    assert is_pure_interface(path) is True


def test_is_pure_interface_false_for_contract_file():
    path = os.path.join(FIXTURES_DIR, "VulnerableBank.sol")
    assert is_pure_interface(path) is False

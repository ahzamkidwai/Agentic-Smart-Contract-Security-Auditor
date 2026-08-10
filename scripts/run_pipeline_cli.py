"""
CLI entrypoint to run the full pipeline against a single file OR a
whole project folder, without going through the API.

Usage:
    python -m scripts.run_pipeline_cli tests/fixtures/VulnerableBank.sol
    python -m scripts.run_pipeline_cli path/to/contracts_folder
"""
import argparse
import os
import sys

from api.pipeline import analyze_contract_file, analyze_contract_folder


def main():
    parser = argparse.ArgumentParser(description="Run the smart contract audit explainer pipeline.")
    parser.add_argument("target", help="Path to a .sol file or a project directory")
    args = parser.parse_args()

    if not os.path.exists(args.target):
        print(f"Path not found: {args.target}", file=sys.stderr)
        sys.exit(1)

    if os.path.isdir(args.target):
        print(f"Analyzing project directory: {args.target}")
        findings, pdf_path, report_id = analyze_contract_folder(args.target)
    else:
        print(f"Analyzing file: {args.target}")
        findings, pdf_path, report_id = analyze_contract_file(args.target, os.path.basename(args.target))

    print(f"\nReport ID: {report_id}")
    print(f"Findings: {len(findings)}")
    for f in findings:
        print(f"  [{f.severity.value:<14}] {f.title:<30} {f.swc_id or '-':<8} "
              f"{f.contract_file}:L{f.line_start}-{f.line_end} (confidence {f.confidence:.2f})")
    print(f"\nPDF report: {pdf_path}")


if __name__ == "__main__":
    main()

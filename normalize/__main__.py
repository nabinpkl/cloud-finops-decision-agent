"""CLI for the normalization layer.

Usage:
    python -m normalize compare --vcpu 4 --ram 8 --region eu-central \
        [--family general-purpose] [--providers aws,azure,ibm,linode,vultr] \
        [--expand cheapest|full]

    python -m normalize lookup --provider aws --instance-type m5.xlarge \
        --region eu-central-1

Both commands print the SPEC.md response shape as indented JSON to stdout.
Use --pretty=false for one-line output when piping into other tools.
"""

from __future__ import annotations

import argparse
import json
import sys

from normalize.query import ANY_FAMILY, compare, lookup


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="normalize", description="Cross-provider pricing queries.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_compare = sub.add_parser("compare", help="Compare cheapest matching instance across providers.")
    p_compare.add_argument("--vcpu", type=int, required=True)
    p_compare.add_argument("--ram", "--ram-gb", dest="ram_gb", type=float, required=True)
    p_compare.add_argument("--region", required=True, help="Canonical bucket: us-east | eu-central | ap-southeast.")
    p_compare.add_argument("--family", default=ANY_FAMILY, help='Family slug from taxonomy/families.json or "any".')
    p_compare.add_argument("--providers", default=None, help="Comma-separated list. Default: aws,azure,ibm,linode,vultr.")
    p_compare.add_argument("--expand", default="cheapest", choices=["cheapest", "full"])
    p_compare.add_argument("--pretty", default="true", choices=["true", "false"])

    p_lookup = sub.add_parser("lookup", help="Look up one instance type's price.")
    p_lookup.add_argument("--provider", required=True)
    p_lookup.add_argument("--instance-type", required=True)
    p_lookup.add_argument("--region", required=True, help="Canonical bucket or provider-native code.")
    p_lookup.add_argument("--pretty", default="true", choices=["true", "false"])

    args = parser.parse_args(argv)

    if args.cmd == "compare":
        providers = args.providers.split(",") if args.providers else None
        result = compare(
            vcpu=args.vcpu,
            ram_gb=args.ram_gb,
            region=args.region,
            family=args.family,
            providers=providers,
            expand=args.expand,
        )
    elif args.cmd == "lookup":
        result = lookup(
            provider=args.provider,
            instance_type=args.instance_type,
            region=args.region,
        )
    else:
        parser.error(f"unknown command {args.cmd!r}")
        return

    indent = 2 if args.pretty == "true" else None
    print(json.dumps(result, indent=indent, default=_json_default))
    sys.exit(0)


def _json_default(value):
    # Polars sometimes hands us numpy-typed numerics through iter_rows; normalize.
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


if __name__ == "__main__":
    main()

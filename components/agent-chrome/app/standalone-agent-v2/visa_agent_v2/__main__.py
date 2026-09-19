"""CLI for the independent V2 Agent Core."""

import argparse
import json

from .server import run_fast_server
from .settings import load_v2_config
from .native_input import native_input_readiness


def _config_command(_args):
    config = load_v2_config()
    native_input = native_input_readiness()
    print(json.dumps({
        "runtime": "computer-use-v2",
        "planningStrategy": "semantic-first",
        "interactionStyle": "visible",
        "host": config.host,
        "port": config.port,
        "dataDir": str(config.data_dir),
        "computerUseConfigured": config.computer_use_configured,
        "browserConfigured": config.browser_configured,
        "nativeInput": native_input,
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="docflow-agent-v2",
        description="Independent DocFlow Computer Use V2 service",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Start the V2 Agent API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(
        handler=lambda args: (
            run_fast_server(args.host, args.port) or 0
        )
    )

    config = commands.add_parser(
        "config",
        help="Show V2 execution readiness",
    )
    config.set_defaults(handler=_config_command)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

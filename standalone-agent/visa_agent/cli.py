"""Command-line entry points for the isolated agent."""

import argparse
import json
from pathlib import Path

from .config import load_config
from .mocks import (
    MockBrowserDriver,
    ScriptedComputerUseModel,
)
from .models import (
    ActionKind,
    AgentJob,
    ComputerAction,
    to_primitive,
)
from .providers import (
    PlainTextOCRProvider,
    UnconfiguredExtractionModel,
)
from .recognition import DocumentRecognizer
from .service import run_server
from .storage import FileCheckpointStore
from .workflow import ComputerUseAgent


def print_json(payload):
    print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2))


def recognize_command(args):
    path = Path(args.file)
    recognizer = DocumentRecognizer(
        PlainTextOCRProvider(), UnconfiguredExtractionModel()
    )
    result = recognizer.recognize(
        path.read_bytes(),
        path.name,
        "text/plain",
        args.document_type,
    )
    print_json(result)
    return 0


def demo_command(args):
    root = Path(__file__).resolve().parent.parent
    sample = root / "examples" / "sample_passport_mrz.txt"
    recognizer = DocumentRecognizer(
        PlainTextOCRProvider(), UnconfiguredExtractionModel()
    )
    result = recognizer.recognize(
        sample.read_bytes(), sample.name, "text/plain", "passport"
    )
    allowed_ids = {
        "personal.surname",
        "personal.givenNames",
        "passport.number",
    }
    for item in result.fields:
        if item.id in allowed_ids:
            item.confirm(
                item.value,
                confirmed_by="offline-demo",
                source="demo",
                reason="Fixed sample data",
            )

    actions = [
        ComputerAction(
            kind=ActionKind.TYPE,
            field_id="personal.surname",
            target_hint="Surname",
            value="MODEL_MUST_NOT_CONTROL_THIS_VALUE",
        ),
        ComputerAction(
            kind=ActionKind.TYPE,
            field_id="personal.givenNames",
            target_hint="Given Names",
            value="MODEL_MUST_NOT_CONTROL_THIS_VALUE",
        ),
        ComputerAction(
            kind=ActionKind.TYPE,
            field_id="passport.number",
            target_hint="Passport Number",
            value="MODEL_MUST_NOT_CONTROL_THIS_VALUE",
        ),
        ComputerAction(kind=ActionKind.COMPLETE),
    ]
    browser = MockBrowserDriver()
    model = ScriptedComputerUseModel(actions)
    store = FileCheckpointStore(args.data_dir, allow_plaintext=True)
    job = AgentJob(fields=result.fields, start_url=browser.url)
    completed = ComputerUseAgent(
        model, browser, checkpoint_store=store
    ).run(job)
    print_json({
        "recognition": result,
        "job": completed,
        "executedActions": browser.executed,
        "checkpoint": str(Path(args.data_dir) / f"{job.id}.json"),
    })
    return 0


def config_command(_args):
    config = load_config()
    print_json({
        "modelConfigured": config.model_configured,
        "ocrConfigured": config.ocr_configured,
        "browserConfigured": config.browser_configured,
        "providers": {
            name: config.provider_public_summary(name, settings)
            for name, settings in config.providers.items()
        },
        "host": config.host,
        "port": config.port,
        "dataDir": str(config.data_dir),
        "checkpointEncryptionConfigured": bool(
            config.checkpoint_encryption_key
        ),
        "plaintextCheckpointsAllowed": config.allow_plaintext_checkpoints,
    })
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="docflow-agent",
        description="Standalone recognition and computer-use agent",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    recognize = commands.add_parser(
        "recognize", help="Run rule-based recognition on OCR text"
    )
    recognize.add_argument("file")
    recognize.add_argument("--document-type", default="passport")
    recognize.set_defaults(handler=recognize_command)

    demo = commands.add_parser(
        "demo", help="Run recognition and computer-use with mock providers"
    )
    demo.add_argument("--data-dir", default="./demo-agent-data")
    demo.set_defaults(handler=demo_command)

    serve = commands.add_parser("serve", help="Start isolated Agent API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(
        handler=lambda args: run_server(args.host, args.port) or 0
    )

    config = commands.add_parser(
        "config", help="Show provider configuration readiness"
    )
    config.set_defaults(handler=config_command)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.handler(args)

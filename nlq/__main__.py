import argparse
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from nlq.model_client import build_chat_model
from nlq.model_settings import ModelSettings
from nlq.schema_context import build_verified_schema_context


_ACKNOWLEDGEMENT = "Reply with exactly: connection-ok"


def main(
    arguments: list[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    model_builder=build_chat_model,
    clock=time.perf_counter,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(arguments)
    api_key: str | None = None
    try:
        if args.handler is _check_schema:
            _check_schema(args)
        else:
            settings = ModelSettings.from_environment(environment)
            api_key = settings.api_key
            _check_model(
                settings=settings,
                model_builder=model_builder,
                clock=clock,
            )
    except Exception as error:
        if args.debug:
            _reraise_without_api_key(error, api_key)
        print(f"error: {_redact_api_key(str(error), api_key)}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nlq")
    parser.add_argument("--debug", action="store_true")
    commands = parser.add_subparsers(required=True)

    schema = commands.add_parser("schema")
    schema_commands = schema.add_subparsers(required=True)
    check = schema_commands.add_parser("check")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--dictionary", type=Path, required=True)
    check.add_argument("--show-context", action="store_true")
    check.set_defaults(handler=_check_schema)

    model = commands.add_parser("model")
    model_commands = model.add_subparsers(required=True)
    model_check = model_commands.add_parser("check")
    model_check.set_defaults(handler=_check_model)
    return parser


def _check_schema(args: argparse.Namespace) -> None:
    context = build_verified_schema_context(
        args.manifest,
        args.dictionary,
        project_root=Path.cwd(),
    )
    print(f"Dataset: {context.dataset_id}")
    print(f"Approved tables: {', '.join(context.approved_tables)}")
    print(f"Context SHA-256: {context.sha256}")
    print(f"Context bytes: {len(context.text.encode('utf-8'))}")
    if args.show_context:
        print(context.text, end="")


def _check_model(*, settings: ModelSettings, model_builder, clock) -> None:
    model = model_builder(settings)
    started = clock()
    response = model.invoke(_ACKNOWLEDGEMENT)
    elapsed_ms = (clock() - started) * 1000
    content = response.content
    if not isinstance(content, str):
        raise ValueError("model returned non-text response")
    if not content.strip():
        raise ValueError("model returned empty text response")

    print(f"Provider: {settings.provider}")
    print(f"Model: {settings.model}")
    print(f"Latency: {elapsed_ms:.0f} ms")
    _print_token_usage(response)


def _print_token_usage(response) -> None:
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, Mapping):
        return
    labels = (
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("total_tokens", "total"),
    )
    values = [f"{label}={usage[key]}" for key, label in labels if key in usage]
    if values:
        print(f"Token usage: {', '.join(values)}")


def _redact_api_key(message: str, api_key: str | None) -> str:
    if api_key:
        return message.replace(api_key, "[redacted]")
    return message


def _reraise_without_api_key(error: Exception, api_key: str | None) -> None:
    if api_key is None:
        raise error
    message = _redact_api_key(str(error), api_key)
    raise RuntimeError(message).with_traceback(error.__traceback__) from None


if __name__ == "__main__":
    raise SystemExit(main())

import json
from dataclasses import dataclass

from source.revisions import SourceRevision
from source.rounds import Round, rounds_as_records
from source.validation import validate_rounds


@dataclass(frozen=True)
class FrozenContent:
    rounds: tuple[Round, ...]


def capture_content(revision: SourceRevision) -> FrozenContent:
    return FrozenContent(revision.rounds)


def encode_content(content: FrozenContent) -> str:
    return json.dumps(rounds_as_records(content.rounds), separators=(',', ':'))


def decode_content(body: str) -> FrozenContent:
    return FrozenContent(validate_rounds(json.loads(body)))

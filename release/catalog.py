import hashlib
import json
from dataclasses import dataclass

from release.content import FrozenContent, decode_content, encode_content
from release.storage import FrozenStorage
from source.rounds import require_label


@dataclass(frozen=True)
class FrozenVolume:
    frozen_number: str
    volume_id: str
    revision_id: str
    content: FrozenContent


class FrozenCatalog:
    def __init__(self, storage: FrozenStorage):
        self.storage = storage

    def initialize(self) -> None:
        self.storage.initialize()

    def bind(self, volume_id: str, revision_id: str, content: FrozenContent) -> str:
        require_label(volume_id, 'volume_id')
        require_label(revision_id, 'revision_id')
        body = encode_content(content)
        identity = json.dumps([volume_id, revision_id, body], separators=(',', ':')).encode('ascii')
        number = 'F-' + hashlib.sha256(identity).hexdigest()
        return self.storage.insert_or_get(number, volume_id, revision_id, body)

    def get(self, frozen_number: str) -> FrozenVolume:
        require_label(frozen_number, 'frozen_number')
        row = self.storage.fetch_by_number(frozen_number)
        if row is None:
            raise KeyError(frozen_number)
        volume_id, revision_id, body = row
        return FrozenVolume(frozen_number, volume_id, revision_id, decode_content(body))

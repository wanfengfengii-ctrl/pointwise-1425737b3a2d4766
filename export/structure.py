from release.content import FrozenContent


def build_structure_evidence(content: FrozenContent) -> list[dict]:
    return [
        {'executed': round_data.executed, 'probes': [probe.name for probe in round_data.probes]}
        for round_data in content.rounds
    ]

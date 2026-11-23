import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from release.freeze import freeze
from source.intake import register
from source.rounds import rounds_as_records
from source.validation import validate_rounds
from tests import night_shift, source_day


def load_case():
    return json.loads((Path(__file__).with_name("samples") / "bands.json").read_text(encoding="utf-8"))


def deliver(receive, intake, manifest, blocks, arrival):
    return {slot: receive(intake, manifest, blocks[slot]) for slot in arrival}


def damage(block):
    payload = block["payload"]
    return {**block, "payload": bytes([payload[0] ^ 1]) + payload[1:]}


def expect_error(error_type, call, *args, **kwargs):
    try:
        call(*args, **kwargs)
    except error_type:
        return
    raise AssertionError(f"expected {error_type.__name__}")


def check_fixture(case):
    rounds = rounds_as_records(validate_rounds(case["source"]["rounds"]))
    expected = [row for band in case["expected"] for row in band["rounds"]]
    assert expected == [dict(row, round_number=i) for i, row in enumerate(rounds, 1)]
    assert [b["band"] for b in case["expected"]] == [1, 2]
    assert len(case["expected"][0]["rounds"]) == case["cut"] < len(rounds)
    assert case["protected"] == ["B", "C"]
    assert case["losses"] == ["AB", "CD", "AC", "BD", "BC"]
    print("BAND FIXTURE", json.dumps(case["expected"], ensure_ascii=False))


def check_report(result, case, ready, receipts, equations):
    snapshot = result["snapshot"]
    assert snapshot["pending"] == [band for band in (1, 2) if band not in ready]
    actual = snapshot["bands"]
    assert [{k: v for k, v in band.items() if k != "sources"} for band in actual] == [
        band for band in case["expected"] if band["band"] in ready
    ]
    if not ready:
        assert result["report_id"] is None and result["receipt_id"] is None
        return
    assert type(result["report_id"]) is int and result["receipt_id"]
    by_receipt = {receipt: slot for slot, receipt in receipts.items()}
    for band in actual:
        pair = "AB" if band["band"] == 1 else "CD"
        assert band["sources"].keys() == set(pair)
        for target, witness in band["sources"].items():
            assert witness and len(set(witness)) == len(witness)
            if target in receipts:
                assert witness == [receipts[target]]
            combined = set()
            for receipt in witness:
                slot = by_receipt[receipt]
                combined.symmetric_difference_update(equations.get(slot, [slot]))
            assert combined == {target}


def run_exchange(case, mode):
    from export.projection import export_bands
    from receive.intake import receive_block
    from review.catalog import ReportCommitError, read_band_report
    from review.publication import review_bands

    with TemporaryDirectory(prefix="binchuan-bands-") as path:
        root = Path(path)
        with source_day(root / "source") as day:
            source = case["source"]
            register(day.revisions, **source)
            number = freeze(day.revisions, day.frozen, source["volume_id"], source["revision_id"])
            manifest, blocks = export_bands(day.frozen, day.artifacts, number, case["cut"], case["protected"])
            other_manifest, other = export_bands(day.frozen, day.artifacts, number, case["cut"], ["A", "D"])
        equations = {slot: entry[0] for slot, entry in json.loads(manifest)["parities"].items()}
        assert manifest != other_manifest
        path = root / "night.sqlite"
        with night_shift(path) as night:
            have = deliver(receive_block, night.intake, manifest, blocks, ["D", "A"])
            pending = review_bands(night.intake, night.reports, manifest, list(have.values()))
            check_report(pending, case, set(), have, equations)
            if mode == "verify":
                expect_error(ValueError, receive_block, night.intake, manifest, damage(blocks["P2"]))
                expect_error(ValueError, receive_block, night.intake, manifest, other["A"])
                foreign = receive_block(night.intake, other_manifest, other["A"])
                expect_error(ValueError, review_bands, night.intake, night.reports, manifest, [foreign])
            have.update(deliver(receive_block, night.intake, manifest, blocks, ["P2"]))
            extra = set(equations["P2"]) & {"B", "C"}
            ready = {1} if extra == {"B"} else {2} if extra == {"C"} else set()
            partial = review_bands(night.intake, night.reports, manifest, list(have.values()))
            check_report(partial, case, ready, have, equations)
            have.update(deliver(receive_block, night.intake, manifest, blocks, ["P1"]))
            still_partial = review_bands(night.intake, night.reports, manifest, [have[k] for k in ("A", "D", "P2")])
            assert still_partial["snapshot"] == partial["snapshot"]
            if mode == "verify":
                def interrupt():
                    raise RuntimeError("前台停止签发")

                expect_error(ReportCommitError, review_bands, night.intake, night.reports,
                             manifest, list(have.values()), before_commit=interrupt)
            full = review_bands(night.intake, night.reports, manifest, list(have.values()))
            check_report(full, case, {1, 2}, have, equations)
        with night_shift(path) as night:
            assert receive_block(night.intake, manifest, blocks["A"]) == have["A"]
            if partial["report_id"] is not None:
                assert read_band_report(night.reports, partial["report_id"]) == partial
            reopened = read_band_report(night.reports, full["report_id"])
            assert reopened == full
        if mode == "verify":
            for index, (header, pieces, losses) in enumerate((
                (manifest, blocks, case["losses"]), (other_manifest, other, case["losses"][:-1] + ["AD"])
            )):
                relations = {slot: entry[0] for slot, entry in json.loads(header)["parities"].items()}
                for lost in losses:
                    with night_shift(root / f"loss-{index}-{lost}.sqlite") as night:
                        arrived = [slot for slot in ("P2", "D", "C", "P1", "B", "A") if slot not in set(lost)]
                        have = deliver(receive_block, night.intake, header, pieces, arrived)
                        result = review_bands(night.intake, night.reports, header, list(have.values()))
                        check_report(result, case, {1, 2}, have, relations)
        print(json.dumps({"one_frame": partial, "reopened": reopened}, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("fixture", "verify", "demo"))
    args = parser.parse_args(argv)
    case = load_case()
    if args.mode == "fixture":
        check_fixture(case)
    else:
        run_exchange(case, args.mode)


if __name__ == "__main__":
    main()

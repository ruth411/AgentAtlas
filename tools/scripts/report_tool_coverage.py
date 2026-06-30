"""Score Ayiru tool-family coverage from the structured catalog."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class CoverageBreakdown:
    breadth: float
    invocation_coverage: float
    constraints_coverage: float
    effects_coverage: float
    workflow_depth: float
    troubleshooting_depth: float
    provenance_completeness: float
    verification_strength: float

    @property
    def total(self) -> float:
        return round(
            self.breadth
            + self.invocation_coverage
            + self.constraints_coverage
            + self.effects_coverage
            + self.workflow_depth
            + self.troubleshooting_depth
            + self.provenance_completeness
            + self.verification_strength,
            2,
        )


@dataclass(frozen=True)
class FamilyCoverage:
    family: str
    subjects: int
    capabilities: int
    constraints: int
    effects: int
    workflow_subjects: int
    troubleshooting_subjects: int
    subjects_with_invocation: int
    subjects_with_constraints: int
    subjects_with_effects: int
    provenance_complete_rows: int
    provenance_total_rows: int
    verification_weight: float
    breakdown: CoverageBreakdown


_VERIFICATION_SCORES = {
    "L0_unverified": 0.0,
    "L1_schema_valid": 0.2,
    "L2_source_verified": 0.6,
    "L3_runtime_verified": 1.0,
    "L4_cross_agent_verified": 1.1,
    "L5_human_audited": 1.2,
}


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _cap(value: float, maximum: float) -> float:
    return min(maximum, value)


def _detail_has_provenance(detail_json: str) -> bool:
    try:
        detail = json.loads(detail_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(detail, dict):
        return False
    if isinstance(detail.get("source_url"), str) and detail["source_url"].strip():
        return True
    if isinstance(detail.get("local_doc_path"), str) and detail["local_doc_path"].strip():
        return True
    provenance = detail.get("provenance")
    return isinstance(provenance, dict) and bool(provenance)


def _score_family(
    conn: sqlite3.Connection,
    family: str,
) -> FamilyCoverage:
    subject_rows = conn.execute(
        "SELECT subject_id, subject_kind, verification_level FROM subjects WHERE family = ?",
        (family,),
    ).fetchall()
    subjects = len(subject_rows)
    subject_ids = [row[0] for row in subject_rows]
    workflow_subjects = sum(1 for _, kind, _ in subject_rows if kind == "workflow")
    troubleshooting_subjects = sum(1 for _, kind, _ in subject_rows if kind == "subject")

    placeholders = ",".join("?" for _ in subject_ids) or "''"
    capability_rows = conn.execute(
        f"SELECT subject_id, capability_type, verification_level, detail_json "
        f"FROM capabilities WHERE subject_id IN ({placeholders})",
        subject_ids,
    ).fetchall() if subject_ids else []
    constraint_rows = conn.execute(
        f"SELECT subject_id, verification_level, detail_json FROM constraints "
        f"WHERE subject_id IN ({placeholders})",
        subject_ids,
    ).fetchall() if subject_ids else []
    effect_rows = conn.execute(
        f"SELECT subject_id, verification_level, detail_json FROM effects "
        f"WHERE subject_id IN ({placeholders})",
        subject_ids,
    ).fetchall() if subject_ids else []

    subjects_with_invocation = len({row[0] for row in capability_rows if row[1] == "invocation"})
    subjects_with_constraints = len({row[0] for row in constraint_rows})
    subjects_with_effects = len({row[0] for row in effect_rows})

    provenance_rows = 0
    provenance_total = len(capability_rows) + len(constraint_rows) + len(effect_rows)
    for _, _, _, detail_json in capability_rows:
        provenance_rows += int(_detail_has_provenance(detail_json))
    for _, _, detail_json in constraint_rows:
        provenance_rows += int(_detail_has_provenance(detail_json))
    for _, _, detail_json in effect_rows:
        provenance_rows += int(_detail_has_provenance(detail_json))

    verification_values: list[float] = []
    verification_values.extend(_VERIFICATION_SCORES.get(row[2], 0.0) for row in capability_rows)
    verification_values.extend(_VERIFICATION_SCORES.get(row[1], 0.0) for row in constraint_rows)
    verification_values.extend(_VERIFICATION_SCORES.get(row[1], 0.0) for row in effect_rows)
    verification_weight = sum(verification_values) / len(verification_values) if verification_values else 0.0

    breakdown = CoverageBreakdown(
        breadth=round(_cap((subjects / 60.0) * 20.0, 20.0), 2),
        invocation_coverage=round(_ratio(subjects_with_invocation, subjects) * 15.0, 2),
        constraints_coverage=round(_ratio(subjects_with_constraints, subjects) * 15.0, 2),
        effects_coverage=round(_ratio(subjects_with_effects, subjects) * 15.0, 2),
        workflow_depth=round(_cap((workflow_subjects / 10.0) * 10.0, 10.0), 2),
        troubleshooting_depth=round(_cap((troubleshooting_subjects / 10.0) * 10.0, 10.0), 2),
        provenance_completeness=round(_ratio(provenance_rows, provenance_total) * 10.0, 2),
        verification_strength=round(_cap((verification_weight / 1.0) * 5.0, 5.0), 2),
    )

    return FamilyCoverage(
        family=family,
        subjects=subjects,
        capabilities=len(capability_rows),
        constraints=len(constraint_rows),
        effects=len(effect_rows),
        workflow_subjects=workflow_subjects,
        troubleshooting_subjects=troubleshooting_subjects,
        subjects_with_invocation=subjects_with_invocation,
        subjects_with_constraints=subjects_with_constraints,
        subjects_with_effects=subjects_with_effects,
        provenance_complete_rows=provenance_rows,
        provenance_total_rows=provenance_total,
        verification_weight=round(verification_weight, 3),
        breakdown=breakdown,
    )


def collect_family_coverage(database_path: Path) -> list[FamilyCoverage]:
    conn = sqlite3.connect(str(database_path))
    try:
        families = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT family FROM subjects ORDER BY family"
            ).fetchall()
        ]
        return sorted(
            (_score_family(conn, family) for family in families),
            key=lambda item: (-item.breakdown.total, item.family),
        )
    finally:
        conn.close()


def _band(score: float) -> str:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "strong"
    if score >= 60:
        return "usable"
    if score >= 40:
        return "thin"
    return "weak"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=0, help="limit output rows; 0 = all")
    args = parser.parse_args()

    rows = collect_family_coverage(Path(args.database))
    if args.top > 0:
        rows = rows[: args.top]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "family": row.family,
                        "score": row.breakdown.total,
                        "band": _band(row.breakdown.total),
                        "subjects": row.subjects,
                        "capabilities": row.capabilities,
                        "constraints": row.constraints,
                        "effects": row.effects,
                        "breakdown": row.breakdown.__dict__,
                    }
                    for row in rows
                ],
                indent=2,
            )
        )
        return 0

    print("family\tscore\tband\tsubjects\tcaps\tconstraints\teffects")
    for row in rows:
        print(
            f"{row.family}\t{row.breakdown.total:.2f}\t{_band(row.breakdown.total)}\t"
            f"{row.subjects}\t{row.capabilities}\t{row.constraints}\t{row.effects}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

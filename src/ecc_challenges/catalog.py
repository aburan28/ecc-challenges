"""Published Certicom challenge list and explicit compute admission.

The source records the list and solutions known on 2009-11-10.  This module
does not infer a challenge's current solution status from that old snapshot.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .protocol import CAMPAIGN, WALK_ID

SOURCE_URL = "https://www.certicom.com/content/dam/certicom/images/pdfs/challenge-2009.pdf"
SOURCE_DATE = "2009-11-10"


@dataclass(frozen=True)
class Challenge:
    id: str
    field: str
    field_bits: int
    level: str
    solved_in_2009: bool
    estimated_operations_2009: str | None = None
    contribution: str = "not_integrated"
    walk_id: str | None = None

    def public(self, multicurve_enabled: bool = False) -> dict:
        value = {**asdict(self), "name": self.id.upper() if self.field != "prime" else
                "ECCp-" + self.id.rsplit("-", 1)[1],
                "source_url": SOURCE_URL, "source_date": SOURCE_DATE}
        if multicurve_enabled:
            from .multicurve.codec import namespace
            ns = namespace(self.id)
            value.update(contribution=self.contribution if self.id == CAMPAIGN else "generic_cpu_cuda_v2",
                         protocols=[1, 2] if self.id == CAMPAIGN else [2],
                         namespace_id=ns.id, instance_sha256=ns.instance.instance_sha256,
                         generic_walk_id=ns.walk["id"], record_bytes=ns.record_bytes)
        return value


def _challenge(name: str, field: str, bits: int, level: str, solved: bool = False,
               operations: str | None = None) -> Challenge:
    campaign = name.lower()
    return Challenge(campaign, field, bits, level, solved, operations,
                     "ecc2k130_gpu_v1" if campaign == CAMPAIGN else "not_integrated",
                     WALK_ID if campaign == CAMPAIGN else None)


# Names, field sizes and groups: Certicom 2009, section 4. Solution snapshot:
# section 6.1; operation estimates: section 6.2, "Estimated number of elliptic
# curve operations" column. ECC2-353 has a 359-bit field as published.
# Section 6.2 misprints the ECC2K-95 and ECC2-97 row labels as "ECC2-79";
# the solution dates in section 6.1 disambiguate those rows.
CHALLENGES = (
    _challenge("ECC2-79", "binary", 79, "exercise", True, "4.9e11"),
    _challenge("ECC2-89", "binary", 89, "exercise", True, "1.6e13"),
    _challenge("ECC2K-95", "binary", 97, "exercise", True, "1.8e13"),
    _challenge("ECC2-97", "binary", 97, "exercise", True, "2.5e14"),
    _challenge("ECC2K-108", "binary", 109, "level_i", True, "1.5e15"),
    _challenge("ECC2-109", "binary", 109, "level_i", True, "1.6e16"),
    _challenge("ECC2K-130", "binary", 131, "level_i", operations="2.0e18"),
    _challenge("ECC2-131", "binary", 131, "level_i", operations="3.3e19"),
    _challenge("ECC2K-163", "binary", 163, "level_ii", operations="1.7e23"),
    _challenge("ECC2-163", "binary", 163, "level_ii", operations="2.1e24"),
    _challenge("ECC2-191", "binary", 191, "level_ii", operations="3.5e28"),
    _challenge("ECC2K-238", "binary", 239, "level_ii", operations="2.7e34"),
    _challenge("ECC2-238", "binary", 239, "level_ii", operations="4.2e35"),
    _challenge("ECC2K-358", "binary", 359, "level_ii", operations="3.6e52"),
    _challenge("ECC2-353", "binary", 359, "level_ii", operations="1.1e53"),
    _challenge("ECCp-79", "prime", 79, "exercise", True, "6.1e11"),
    _challenge("ECCp-89", "prime", 89, "exercise", True, "1.8e13"),
    _challenge("ECCp-97", "prime", 97, "exercise", True, "3.0e14"),
    _challenge("ECCp-109", "prime", 109, "level_i", True, "2.1e16"),
    _challenge("ECCp-131", "prime", 131, "level_i", operations="3.5e19"),
    _challenge("ECCp-163", "prime", 163, "level_ii", operations="2.4e24"),
    _challenge("ECCp-191", "prime", 191, "level_ii", operations="4.9e28"),
    _challenge("ECCp-239", "prime", 239, "level_ii", operations="8.2e35"),
    _challenge("ECCp-359", "prime", 359, "level_ii", operations="9.6e53"),
)

BY_ID = {challenge.id: challenge for challenge in CHALLENGES}
assert len(CHALLENGES) == len(BY_ID) == 24
assert {challenge.id for challenge in CHALLENGES if challenge.contribution != "not_integrated"} == {CAMPAIGN}

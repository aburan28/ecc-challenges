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

    def public(self) -> dict:
        return {**asdict(self), "name": self.id.upper() if self.field != "prime" else
                "ECCp-" + self.id.rsplit("-", 1)[1],
                "source_url": SOURCE_URL, "source_date": SOURCE_DATE}


def _challenge(name: str, field: str, bits: int, level: str, solved: bool = False,
               operations: str | None = None) -> Challenge:
    campaign = name.lower()
    return Challenge(campaign, field, bits, level, solved, operations,
                     "ecc2k130_gpu_v1" if campaign == CAMPAIGN else "not_integrated",
                     WALK_ID if campaign == CAMPAIGN else None)


# Names, field sizes and groups: Certicom 2009, section 4. Solution snapshot:
# section 6.1. ECC2-353 has a 359-bit field as published by Certicom.
CHALLENGES = (
    _challenge("ECC2-79", "binary", 79, "exercise", True),
    _challenge("ECC2-89", "binary", 89, "exercise", True),
    _challenge("ECC2K-95", "binary", 97, "exercise", True),
    _challenge("ECC2-97", "binary", 97, "exercise", True),
    _challenge("ECC2K-108", "binary", 109, "level_i", True),
    _challenge("ECC2-109", "binary", 109, "level_i", True),
    _challenge("ECC2K-130", "binary", 131, "level_i", operations="2.0e18"),
    _challenge("ECC2-131", "binary", 131, "level_i", operations="3.3e19"),
    _challenge("ECC2K-163", "binary", 163, "level_ii", operations="1.7e23"),
    _challenge("ECC2-163", "binary", 163, "level_ii", operations="2.1e24"),
    _challenge("ECC2-191", "binary", 191, "level_ii", operations="3.5e28"),
    _challenge("ECC2K-238", "binary", 239, "level_ii", operations="2.7e34"),
    _challenge("ECC2-238", "binary", 239, "level_ii", operations="4.2e35"),
    _challenge("ECC2K-358", "binary", 359, "level_ii", operations="3.6e52"),
    _challenge("ECC2-353", "binary", 359, "level_ii", operations="1.1e53"),
    _challenge("ECCp-79", "prime", 79, "exercise", True),
    _challenge("ECCp-89", "prime", 89, "exercise", True),
    _challenge("ECCp-97", "prime", 97, "exercise", True),
    _challenge("ECCp-109", "prime", 109, "level_i", True),
    _challenge("ECCp-131", "prime", 131, "level_i", operations="3.5e19"),
    _challenge("ECCp-163", "prime", 163, "level_ii", operations="2.4e24"),
    _challenge("ECCp-191", "prime", 192, "level_ii", operations="4.9e28"),
    _challenge("ECCp-239", "prime", 239, "level_ii", operations="8.2e35"),
    _challenge("ECCp-359", "prime", 359, "level_ii", operations="9.6e53"),
)

BY_ID = {challenge.id: challenge for challenge in CHALLENGES}
assert len(CHALLENGES) == len(BY_ID) == 24
assert {challenge.id for challenge in CHALLENGES if challenge.contribution != "not_integrated"} == {CAMPAIGN}

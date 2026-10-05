import asyncio
import csv
import json
import sys
from pathlib import Path

from sqlalchemy import text

from app.db import SessionLocal

VALID = {"GK", "CB", "LB", "RB", "DM", "CM", "AM", "LW", "RW", "ST"}


def val(row, key, default):
    x = row.get(key, "").strip()
    return x if x else default


async def main(path):
    with open(path, encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))

    async with SessionLocal() as s:
        for r in rows:
            if val(r, "position", "") not in VALID:
                continue
            talents = [x.strip().upper() for x in val(r, "talents", "").split("|") if x.strip()]
            await s.execute(
                text(
                    """INSERT INTO players(
                        first_name,last_name,nationality,age,position,pace,shooting,passing,
                        dribbling,defending,physical,stamina,mental,market_value,salary,
                        potential,rarity,card_version,real_player,preferred_foot,
                        secondary_positions,talents,tactical_archetype,data_source)
                    VALUES(
                        :fn,:ln,:nat,:age,:pos,:pace,:sho,:pas,:dri,:def,:phy,:sta,:men,
                        :mv,:sal,:pot,:rar,:card,true,:foot,:sec,:tal,:arch,:src)
                    """
                ),
                {
                    "fn": r["first_name"],
                    "ln": r["last_name"],
                    "nat": r["nationality"][:3].upper(),
                    "age": int(val(r, "age", 25)),
                    "pos": r["position"],
                    "pace": int(val(r, "pace", 65)),
                    "sho": int(val(r, "shooting", 60)),
                    "pas": int(val(r, "passing", 60)),
                    "dri": int(val(r, "dribbling", 60)),
                    "def": int(val(r, "defending", 50)),
                    "phy": int(val(r, "physical", 60)),
                    "sta": int(val(r, "stamina", 70)),
                    "men": int(val(r, "mental", 65)),
                    "mv": int(float(val(r, "market_value", 0))),
                    "sal": int(float(val(r, "salary", 0))),
                    "pot": int(val(r, "potential", 70)),
                    "rar": val(r, "rarity", "BASE"),
                    "card": val(r, "card_version", "STANDARD"),
                    "foot": val(r, "preferred_foot", "R"),
                    "sec": [x.strip() for x in val(r, "secondary_positions", "").split("|") if x.strip()],
                    "tal": json.dumps(talents),
                    "arch": talents[0] if talents else "BALANCED",
                    "src": f"csv:{Path(path).name}",
                },
            )
        await s.commit()
    print("Imported", len(rows))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

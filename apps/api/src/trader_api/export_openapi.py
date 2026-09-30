"""Экспорт схемы OpenAPI в файл: ``python -m trader_api.export_openapi <путь>``.

Схему строит само приложение, без БД. Из неё генерируется TS-клиент
(``libs/api-client``), поэтому вывод детерминирован.
"""

import json
import sys
from pathlib import Path

from trader_api.main import create_app


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Использование: python -m trader_api.export_openapi <файл.json>")
        return 2
    schema = create_app(migrate_on_startup=False).openapi()
    Path(argv[1]).write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

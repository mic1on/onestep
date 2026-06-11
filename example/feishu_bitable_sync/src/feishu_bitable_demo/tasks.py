from __future__ import annotations

import json
from typing import Any

from onestep_feishu_bitable import feishu_bitable_text, feishu_bitable_user


async def map_order(ctx, payload: dict[str, Any]) -> dict[str, Any] | None:
    fields = payload["fields"]
    print(fields.get("人员"))
    row = {
        "标题": feishu_bitable_text(fields.get("标题")),
        "编号": feishu_bitable_text(fields.get("编号")),
        "负责人": feishu_bitable_user(fields.get("人员"))
    }
    owner_field = str(ctx.task_config.get("owner_field") or "").strip()
    owner_id_field = str(ctx.task_config.get("owner_id_field") or "").strip()
    if owner_field and owner_id_field:
        row[owner_field] = feishu_bitable_user(fields.get(owner_id_field))

    if ctx.task_config.get("dry_run"):
        print(
            json.dumps(
                {
                    "record_id": payload.get("record_id"),
                    "mapped": row,
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return None
    return row

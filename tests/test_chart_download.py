import base64
import io
from types import SimpleNamespace

import numpy as np
from PIL import Image

from nightingale.agent_tools import call_tool_sync


def test_saved_chart_get_preserves_image_bytes_caps_metadata_and_keeps_default_create():
    image = Image.fromarray(np.random.default_rng(42).integers(0, 256, (120, 120, 3), dtype=np.uint8))
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    png = base64.b64encode(buffer.getvalue()).decode("ascii")
    assert len(png) > 20_000
    calls = []

    def get(chart_id, image, lang):
        calls.append(("get", chart_id, image, lang))
        return {"chart_id": chart_id, "name": "Gráfica ñ", "image_base64": png, "detail": "m" * 40_000}

    def create(*args, **kwargs):
        calls.append(("create", args, kwargs))
        return {"chart_id": 91}

    service = SimpleNamespace(chart_get=get, chart_create=create)
    result = call_tool_sync(service, "data_chart", {"action": "get", "chart_id": 17, "image": True, "lang": "es"})
    assert result["chart_id"] == 17 and result["name"] == "Gráfica ñ"
    assert base64.b64decode(result["image_base64"]) == buffer.getvalue()
    assert len(result["detail"]) < 40_000 and "truncated" in result
    assert call_tool_sync(service, "data_chart", {"dataset": "qa", "kind": "bar", "x": "category"}) == {"chart_id": 91}
    assert calls == [("get", 17, True, "es"), ("create", ("qa", "bar", "category", None, "sum", None, None, "", None, False), {"source": "agent"})]

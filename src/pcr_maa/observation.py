"""Conservative summaries of actual Maa OCR boxes in 1280x720 coordinates."""
import re


def summarize(words):
    def in_box(x1, y1, x2, y2):
        return [w for w in words if x1 <= w["box"][0] + w["box"][2] / 2 < x2
                and y1 <= w["box"][1] + w["box"][3] / 2 < y2]

    def text_in(*box):
        return " ".join(w["text"] for w in in_box(*box))

    page = "presets" if "关卡一览" in text_in(450, 25, 820, 80) else "unknown"
    slots = [{"slot": i + 1, "name": text_in(65 + i * 157, 90, 65 + (i + 1) * 157, 131) or None}
             for i in range(7)] if page == "presets" else []
    rows = []
    if page == "presets":
        for w in in_box(560, 235, 663, 483):
            match = re.fullmatch(r"\s*(\d+)\s*[/／]\s*(\d+)\s*", w["text"])
            if not match:
                continue
            y = w["box"][1] + w["box"][3] / 2
            difficulty = text_in(228, y - 17, 368, y + 17)
            category = text_in(77, y - 17, 226, y + 17)
            title = text_in(65, y - 57, 530, y - 18)
            rows.append({"title": title or None, "category": category or None, "difficulty": difficulty or None,
                         "remaining": int(match[1]), "limit": int(match[2]), "ocr_box": w["box"]})
    def number(box):
        text = text_in(*box).replace(",", "").strip()
        return int(text) if re.fullmatch(r"\d+", text) else None
    quote = {"stamina": {"before": number((330, 517, 436, 550)), "after": number((505, 517, 586, 550))},
             "tickets": {"before": number((330, 556, 436, 591)), "after": number((505, 556, 593, 591))}}
    quote["valid_numeric_quote"] = all(value is not None for pair in quote.values() for value in pair.values()) if page == "presets" else False
    return {"page": page, "slots": slots, "selected_slot": None, "visible_rows": rows,
            "list_complete": False, "visible_exhausted": all(r["remaining"] == 0 for r in rows) if rows else None,
            "all_slots_exhausted": None, "quote": quote,
            "tickets_per_stage": number((1035, 535, 1115, 568)) if page == "presets" else None,
            "daily_complete": False}

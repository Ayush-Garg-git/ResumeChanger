"""
Page limit: does the refined resume fit the number of pages the user wants?

If it doesn't, we NEVER delete content, shrink fonts or change the layout
ourselves. We explain the problem and list options; the user picks.

Options come from suggestions the user hasn't applied yet, in this order:
1. Remove a less relevant line   ("consider_removing" not yet removed)
2. Shorten a line                ("shorten" not yet applied)
3. Review a line                 ("review" not yet edited)
4. Adjust spacing or layout yourself in Word (advice only, nothing automatic)
5. Keep it as it is and accept a longer resume

Removing one line doesn't always save a page, so we say "may help", and
the user checks the new page count after re-creating the file.
"""


def page_fit(analysis: dict, decisions: dict, pages: int, max_pages: int | None) -> dict:
    fits = max_pages is None or pages <= max_pages
    result = {
        "pages": pages,
        "max_pages": max_pages,
        "fits": fits,
        "over_by": 0 if fits else pages - max_pages,
        "options": [],
    }
    if fits:
        return result

    removals, shortenings, reviews = [], [], []
    for s in analysis["suggestions"]:
        decision = decisions.get(s["id"])
        effect = decision["effect"] if decision else None
        base = {
            "suggestion_id": s["id"],
            "paragraph_id": s["paragraph_id"],
            "text": s["original_text"],
            "reason": s["reason"],
            # The user said "keep" earlier; we still list it, but say so.
            "previously_kept": effect == "keep",
        }
        if s["kind"] == "consider_removing" and effect != "remove":
            removals.append({**base, "type": "remove", "action": "accept"})
        elif s["kind"] == "shorten" and effect != "change":
            shortenings.append({**base, "type": "shorten", "action": "accept", "proposed_text": s["proposed_text"]})
        elif s["kind"] == "review" and effect != "change":
            reviews.append({**base, "type": "review", "action": "edit"})

    result["options"] = [
        *removals,
        *shortenings,
        *reviews,
        {"type": "adjust_layout"},
        {"type": "keep_longer"},
    ]
    return result

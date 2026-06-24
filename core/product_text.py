import html
import re


def clean_product_description(value):
    text = str(value or "").strip()
    if not text:
        return ""

    for _index in range(2):
        text = html.unescape(text)

    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")

    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    compact_lines = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank and compact_lines:
                compact_lines.append("")
            previous_blank = True
            continue
        compact_lines.append(line)
        previous_blank = False

    return "\n".join(compact_lines).strip()

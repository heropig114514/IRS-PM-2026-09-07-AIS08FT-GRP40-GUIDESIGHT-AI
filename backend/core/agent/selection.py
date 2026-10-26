"""Responsibilities: Parse explicit numbered/exact-label candidate confirmations.
Implementation: A bounded slot grammar, independent of intent classification and place names.
Related Modules: runtime persists pending selections; Skill JSON supplies candidates/tool bindings.
Declaration Index: selected_index.
Variable Index: None; no place aliases, coordinates or language-model guesses are encoded.
"""

import re


def selected_index(text, items):
    """Functionality: Resolve an explicit selection without inferring an entity alias.
    Inputs: current user text and displayed candidate dictionaries; Outputs: zero-based index/None.
    Logic: Accept whole-utterance Chinese/English numbers or a unique full label/name match.
    Constraints: Substrings, related-place names and paraphrases do not authorize coordinates;
    unmatched input remains unconfirmed and is not treated as an execution failure.
    """
    value = text.strip().casefold()
    matches = [index for index, item in enumerate(items)
               if any(isinstance(item.get(key), str) and item[key].strip().casefold() == value
                      for key in ("label", "name"))]
    if len(matches) == 1:
        return matches[0]
    chinese = re.fullmatch(
        r"(?:请)?(?:选择|选|我要|就要)?第?([1-5一二三四五])(?:个|项|号)?吧?[。.!！]?",
        re.sub(r"\s+", "", value),
    )
    english = re.fullmatch(
        r"(?:(?:choose|select)\s+)?(?:the\s+)?(?:option\s+|number\s+)?"
        r"([1-5]|one|two|three|four|five|first|second|third|fourth|fifth)(?:\s+one)?[.!]?",
        value,
    )
    token = chinese[1] if chinese else english[1] if english else None
    groups = (("1", "一", "one", "first"), ("2", "二", "two", "second"),
              ("3", "三", "three", "third"), ("4", "四", "four", "fourth"),
              ("5", "五", "five", "fifth"))
    return next((index for index, group in enumerate(groups)
                 if token in group and index < len(items)), None)

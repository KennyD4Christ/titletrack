"""The canonical key of an estate name or a plot number.

Two names that differ only by letter case or by the amount or kind of whitespace are the same estate (or plot).
The key is what the database enforces as unique, so it must be computed identically everywhere. It is the same
rule the application already used when it looked records up: collapse every run of whitespace to one space,
strip, then case-fold (tracker.services._key and importer.validation.norm). A test keeps the three in step.

A key is DERIVED from its source field and is never written on its own. The helpers below are the single place
that enforces this for Model.save(), update_fields lists and queryset updates (see CanonicalKeyQuerySet in
tracker/models.py). Migration 0013 keeps its own frozen copy of canonical_key on purpose.

This module imports nothing from Django, so models.py can use it without a cycle.
"""
import re

_WHITESPACE = re.compile(r"\s+")

# Case folding can lengthen text (for example U+0390 becomes three characters), never by more than this factor.
# The key columns are sized as this factor times the longest value the source column allows.
MAX_CASEFOLD_GROWTH = 3


def canonical_key(value):
    """The canonical key of `value`: whitespace collapsed, stripped and case-folded. None and '' give ''."""
    return _WHITESPACE.sub(" ", str(value or "")).strip().casefold()


def fields_with_derived_key(fields, source_field, key_field):
    """The list of columns to write when `fields` is an update_fields list (or None, meaning every column).

    - None stays None.
    - The source field being written brings its key with it.
    - The key on its own is refused: it would store a key that no longer matches its source.
    Raises ValueError for that last case. Never changes the list it is given."""
    if fields is None:
        return None
    fields = list(fields)
    if key_field in fields and source_field not in fields:
        raise ValueError(
            f"{key_field} is derived from {source_field} and cannot be written on its own: "
            f"include {source_field} instead, and {key_field} will follow it.")
    if source_field in fields and key_field not in fields:
        fields.append(key_field)
    return fields


def sync_key_on_save(instance, source_field, key_field, args, kwargs):
    """Everything Model.save() must do about the key, for the models that have one. Returns the keyword
    arguments to pass on to the real save().

    - Options are keyword-only. Django has deprecated positional save() arguments, and one of them is
      update_fields, which would slip past the check below.
    - The key is recomputed from the source whenever the source is being saved: always for a full save, and for a
      partial save that lists the source. A partial save that leaves the source out does not touch the key,
      in the database or on the instance.
    - update_fields that name the key without its source are refused (see fields_with_derived_key)."""
    if args:
        raise TypeError(
            f"{type(instance).__name__}.save() takes keyword arguments only (for example update_fields=[...]).")
    update_fields = fields_with_derived_key(kwargs.get("update_fields"), source_field, key_field)
    if update_fields is None or source_field in update_fields:
        setattr(instance, key_field, canonical_key(getattr(instance, source_field)))
    if update_fields is not None:
        kwargs = {**kwargs, "update_fields": update_fields}
    return kwargs

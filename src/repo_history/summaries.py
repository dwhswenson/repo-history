"""Monthly counts derived exclusively from credited activity."""

from collections.abc import Iterable

from .models import ACTIVITY_TYPES, Activity, Actor, MonthlySummaries, PopulationCounts


def derive_summaries(
    actors: Iterable[Actor], activities: Iterable[Activity]
) -> MonthlySummaries:
    registry = {actor.key: actor for actor in actors}
    counts = dict.fromkeys(ACTIVITY_TYPES, 0)
    unattributed = counts.copy()
    per_actor: dict[str, dict[str, int]] = {}
    participants: dict[str, set[str]] = {kind: set() for kind in ACTIVITY_TYPES}
    for activity in activities:
        counts[activity.type] += 1
        keys = {credit.actor_key for credit in activity.credits if credit.actor_key}
        if not keys:
            unattributed[activity.type] += 1
        participants[activity.type].update(keys)
        for key in keys:
            per_actor.setdefault(key, dict.fromkeys(ACTIVITY_TYPES, 0))[
                activity.type
            ] += 1

    def population(keys: set[str]) -> PopulationCounts:
        classes = [registry[key].classification for key in keys]
        return PopulationCounts(
            len(keys),
            classes.count("human"),
            classes.count("bot"),
            classes.count("unlinked_git"),
        )

    return MonthlySummaries(
        counts,
        dict(sorted(per_actor.items())),
        population(set(per_actor)),
        {kind: population(keys) for kind, keys in participants.items()},
        sum(unattributed.values()),
        unattributed,
    )

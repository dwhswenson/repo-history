"""Construct canonical reports from normalized records."""

from collections.abc import Iterable
from dataclasses import replace

from .models import Activity, Actor, MonthlyReport, ReportMetadata
from .serialization import to_data
from .summaries import derive_summaries
from .validation import _validate_model_types, validate_data, validate_report


def build_report(
    metadata: ReportMetadata, actors: Iterable[Actor], activities: Iterable[Activity]
) -> MonthlyReport:
    actors = tuple(sorted(actors, key=lambda actor: actor.key))
    activities = tuple(
        replace(
            activity,
            credits=tuple(
                sorted(
                    set(activity.credits),
                    key=lambda credit: (credit.actor_key or "", credit.role),
                )
            ),
        )
        for activity in activities
    )
    # Validate structure and references before summary derivation can dereference them.
    placeholder = derive_summaries((), ())
    probe = MonthlyReport(metadata, actors, activities, placeholder)
    _validate_model_types(probe)
    validate_data(to_data(probe), check_summaries=False)
    activities = tuple(sorted(activities, key=lambda a: (a.occurred_at, a.id)))
    report = MonthlyReport(
        metadata, actors, activities, derive_summaries(actors, activities)
    )
    validate_report(report)
    return report

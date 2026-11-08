"""Review of external employees against Horilla (import direction only)."""

from . import employee_mapping


def review_external_employees(provider, external_employees):
    """
    Split a connector's employee list into already-mapped and needing review.
    Horilla stays master: nothing is created or overwritten here.
    """
    index = employee_mapping.active_mapping_index(provider)
    mapped, to_review = [], []
    for ext in external_employees:
        if ext.external_employee_id in index:
            mapped.append(ext)
        else:
            to_review.append(
                {"external": ext, "suggestion": employee_mapping.suggest_employee(ext.external_employee_id)}
            )
    return mapped, to_review

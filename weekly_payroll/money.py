from decimal import ROUND_HALF_UP, Decimal

KOBO = Decimal("0.01")


def D(value):
    """Decimal from str/int/float without float noise."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value if value is not None else 0))


def kobo(value):
    """Round to the nearest kobo (half up)."""
    return D(value).quantize(KOBO, rounding=ROUND_HALF_UP)

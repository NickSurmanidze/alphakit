# Fixing problem of simple plus/minus returning not exact value
def rounding_fix(value: float, decimal_places: int = 15) -> float:
    return round(value, decimal_places)

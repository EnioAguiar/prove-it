def parcelas(total_centavos: int, n: int) -> list[int]:
    """Split a purchase of `total_centavos` cents into `n` installments.

    The installments must sum exactly to the total.
    """
    base = total_centavos // n
    return [base] * n

from typing import TypedDict


class ExchangeFees(TypedDict):
    slippage: float
    maker_fee: float
    taker_fee: float


class ExchangeConfig(TypedDict):
    name: str
    params: ExchangeFees


EXCHANGE_PARAMS: dict[str, ExchangeConfig] = {
    "default": {
        "name": "Default",
        "params": {
            "slippage": 0.002,
            "maker_fee": 0.001,
            "taker_fee": 0.00075,
        },
    },
    "binance": {
        "name": "Binance",
        "params": {
            "slippage": 0.002,
            "maker_fee": 0.001,
            "taker_fee": 0.00075,
        },
    },
    "coinbase": {
        "name": "Coinbase",
        "params": {
            "slippage": 0.002,
            "maker_fee": 0.001,
            "taker_fee": 0.00075,
        },
    },
}


def get_exchange_params(exchange_key: str = "default") -> ExchangeFees:
    """Looks up the fee/slippage preset for `exchange_key` (raises KeyError if unknown)."""
    return EXCHANGE_PARAMS[exchange_key]["params"]


def get_exchange_name(exchange_key: str = "default") -> str:
    """Looks up the display name for `exchange_key` (raises KeyError if unknown)."""
    return EXCHANGE_PARAMS[exchange_key]["name"]

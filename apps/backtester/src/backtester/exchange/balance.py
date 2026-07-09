"""Balance and transaction management."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pandas as pd

from backtester.exchange.types import ExchangeBalance, TransactionRecord

if TYPE_CHECKING:
    from backtester.exchange.core import Exchange


class Transactions:
    """Deposit/withdrawal ledger -- the only way cash enters or leaves the simulated
    account (trading itself only moves balance between assets/locked states, it never
    creates or destroys total account value except via realized PnL/fees)."""

    def __init__(self, exchange: Exchange) -> None:
        """Starts with an empty transaction history."""
        self.exchange = exchange
        self.transactions: list[TransactionRecord] = []

    def get_transactions(self) -> list[TransactionRecord]:
        """Returns every deposit and withdrawal recorded so far."""
        return self.transactions

    def get_transactions_by_timestamp(self, timestamp: pd.Timestamp) -> list[TransactionRecord]:
        """Returns every transaction created at exactly `timestamp`."""
        return [t for t in self.transactions if t["created"] == timestamp]

    def get_deposits(self) -> list[TransactionRecord]:
        """Returns every deposit transaction recorded so far."""
        return [t for t in self.transactions if t["type"] == "deposit"]

    def get_withdrawals(self) -> list[TransactionRecord]:
        """Returns every withdrawal transaction recorded so far."""
        return [t for t in self.transactions if t["type"] == "withdrawal"]

    def add_deposit(self, asset: str, volume: float) -> TransactionRecord:
        """Credits `volume` of `asset` to the balance and records a deposit transaction."""
        self.exchange.balance.increase_asset_balance(asset=asset, volume=volume)

        transaction: TransactionRecord = {
            "id": uuid.uuid4(),
            "created": self.exchange.market.current["time_close"],
            "type": "deposit",
            "asset": asset,
            "volume": volume,
            "value_in_usd": self.exchange.convert_asset_volume(
                volume, from_asset=asset, to_asset="USD"
            ),
        }

        self.transactions.append(transaction)
        return transaction

    def add_withdrawal(self, asset: str, volume: float) -> TransactionRecord:
        """Debits `volume` of `asset` from the balance and records a withdrawal
        transaction (raises if free balance is insufficient)."""
        if self.exchange.balance.balance[asset]["free"]["volume"] < volume:
            raise ValueError("Not enough free balance to withdraw...")

        self.exchange.balance.reduce_asset_balance(asset=asset, volume=volume)

        transaction: TransactionRecord = {
            "id": uuid.uuid4(),
            "created": self.exchange.market.current["time_close"],
            "type": "withdrawal",
            "asset": asset,
            "volume": volume,
            "value_in_usd": self.exchange.convert_asset_volume(
                volume, from_asset=asset, to_asset="USD"
            ),
        }
        self.transactions.append(transaction)
        return transaction


class Balance:
    """Per-asset free/used/total ledger, each tracked in both native units and a
    continuously-refreshed USD valuation. `used` is volume locked as order/position
    margin, not spendable until unlocked."""

    def __init__(self, exchange: Exchange) -> None:
        """Starts with a zeroed USD balance -- deposit via Transactions.add_deposit()."""
        self.exchange = exchange

        self.balance: ExchangeBalance = {
            "USD": {
                "free": {"volume": 0, "value_in_usd": 0},
                "used": {"volume": 0, "value_in_usd": 0},
                "total": {"volume": 0, "value_in_usd": 0},
            }
        }

    def get_balance(self) -> ExchangeBalance:
        """Returns the full per-asset free/used/total balance dict."""
        return self.balance

    def get_total_balance_in_usd(self, balance_type: str = "total") -> float:
        """Sums the USD valuation of `balance_type` ("free"/"used"/"total") across every
        held asset."""
        total = 0.0
        for asset in self.balance:
            total += self.balance[asset][balance_type]["value_in_usd"]  # type: ignore[literal-required]
        return total

    def refresh_balance_usd_values(self) -> ExchangeBalance:
        """Recomputes every asset's free/used/total USD valuation from the current
        market price (zeroes them out for an asset with no current market price)."""
        for asset in self.balance:
            try:
                usd_rate = self.exchange.convert_asset_volume(
                    volume=1.0, from_asset=asset, to_asset="USD"
                )
                self.balance[asset]["free"]["value_in_usd"] = (
                    self.balance[asset]["free"]["volume"] * usd_rate
                )
                self.balance[asset]["used"]["value_in_usd"] = (
                    self.balance[asset]["used"]["volume"] * usd_rate
                )
                self.balance[asset]["total"]["value_in_usd"] = (
                    self.balance[asset]["total"]["volume"] * usd_rate
                )
            except (ValueError, KeyError):
                self.balance[asset]["free"]["value_in_usd"] = 0.0
                self.balance[asset]["used"]["value_in_usd"] = 0.0
                self.balance[asset]["total"]["value_in_usd"] = 0.0

        return self.balance

    def increase_asset_balance(self, asset: str, volume: float) -> ExchangeBalance:
        """Credits `volume` of `asset` to both free and total (creating the asset's
        ledger entry if this is the first time it's held)."""
        if asset not in self.balance:
            self.balance[asset] = {
                "free": {"volume": 0, "value_in_usd": 0},
                "used": {"volume": 0, "value_in_usd": 0},
                "total": {"volume": 0, "value_in_usd": 0},
            }

        value_in_usd = self.exchange.convert_asset_volume(volume, from_asset=asset, to_asset="USD")

        self.balance[asset]["free"]["volume"] += volume
        self.balance[asset]["total"]["volume"] += volume
        self.balance[asset]["free"]["value_in_usd"] += value_in_usd
        self.balance[asset]["total"]["value_in_usd"] += value_in_usd

        return self.balance

    def reduce_asset_balance(self, asset: str, volume: float) -> ExchangeBalance:
        """Debits `volume` of `asset` from both free and total (raises if free balance
        is insufficient)."""
        if self.balance[asset]["free"]["volume"] < volume:
            raise ValueError("Cannot reduce balance. Not enough balance.")

        value_in_usd = self.exchange.convert_asset_volume(volume, from_asset=asset, to_asset="USD")

        self.balance[asset]["free"]["volume"] -= volume
        self.balance[asset]["total"]["volume"] -= volume
        self.balance[asset]["free"]["value_in_usd"] -= value_in_usd
        self.balance[asset]["total"]["value_in_usd"] -= value_in_usd

        return self.balance

    def lock_balance(self, asset: str, volume: float) -> ExchangeBalance:
        """Moves `volume` of `asset` from free to used (e.g. reserving order/position
        margin). Raises if the asset is unknown or free balance is insufficient."""
        if asset not in self.balance:
            raise ValueError(
                f"Cannot lock balance. This asset {asset} does not exist within balances."
            )
        elif self.balance[asset]["free"]["volume"] < volume:
            free = self.balance[asset]["free"]["volume"]
            raise ValueError(f"Cannot lock balance. Asset {asset} free {free} < requested {volume}")
        else:
            self.balance[asset]["free"]["volume"] -= volume
            self.balance[asset]["used"]["volume"] += volume
            self.refresh_balance_usd_values()

        return self.balance

    def unlock_balance(self, asset: str, volume: float) -> ExchangeBalance:
        """Moves `volume` of `asset` from used back to free (e.g. releasing margin on
        order cancel/position close). Tolerates tiny float-rounding overshoot; raises if
        genuinely over-unlocking."""
        _FLOAT_TOLERANCE = 0.001  # guard against fp rounding in balance ops
        if asset not in self.balance:
            raise ValueError(
                f"Cannot unlock balance. This asset {asset} does not exist within balances."
            )
        elif (
            self.balance[asset]["used"]["volume"] < volume
            and (volume - self.balance[asset]["used"]["volume"]) > _FLOAT_TOLERANCE
        ):
            used = self.balance[asset]["used"]["volume"]
            raise ValueError(
                f"Cannot unlock balance. Asset {asset} used {used} < requested {volume}"
            )
        else:
            self.balance[asset]["free"]["volume"] += volume
            self.balance[asset]["used"]["volume"] -= volume
            self.refresh_balance_usd_values()

        return self.balance

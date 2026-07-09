"""Hand-verified unit tests for Balance/Transactions -- deposits, withdrawals, and the
free/used/total ledger that every order/position operation locks and unlocks against."""

import pytest
from conftest import build_market, make_exchange


@pytest.fixture
def exchange():
    market = build_market(
        {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 5}
    )
    return make_exchange(market)


class TestDeposits:
    def test_deposit_increases_free_and_total(self, exchange):
        exchange.transactions.add_deposit(asset="USD", volume=1000)

        balance = exchange.balance.get_balance()["USD"]
        assert balance["free"]["volume"] == 1000
        assert balance["total"]["volume"] == 1000
        assert balance["free"]["value_in_usd"] == 1000
        assert balance["total"]["value_in_usd"] == 1000
        assert balance["used"]["volume"] == 0

    def test_deposit_records_transaction(self, exchange):
        txn = exchange.transactions.add_deposit(asset="USD", volume=1000)

        assert txn["type"] == "deposit"
        assert txn["asset"] == "USD"
        assert txn["volume"] == 1000
        assert txn["value_in_usd"] == 1000
        assert exchange.transactions.get_deposits() == [txn]

    def test_deposit_of_non_usd_asset_converts_via_market_price(self, exchange):
        # BTC/USD close is 100 on the fixture's first candle.
        txn = exchange.transactions.add_deposit(asset="BTC", volume=2)

        assert txn["value_in_usd"] == 200
        assert exchange.balance.get_balance()["BTC"]["free"]["volume"] == 2


class TestWithdrawals:
    def test_withdrawal_reduces_free_and_total(self, exchange):
        exchange.transactions.add_deposit(asset="USD", volume=1000)

        exchange.transactions.add_withdrawal(asset="USD", volume=300)

        balance = exchange.balance.get_balance()["USD"]
        assert balance["free"]["volume"] == 700
        assert balance["total"]["volume"] == 700

    def test_withdrawal_raises_when_insufficient_free_balance(self, exchange):
        exchange.transactions.add_deposit(asset="USD", volume=100)

        with pytest.raises(ValueError, match="Not enough free balance"):
            exchange.transactions.add_withdrawal(asset="USD", volume=200)


class TestLockUnlock:
    def test_lock_balance_moves_free_to_used(self, exchange):
        exchange.transactions.add_deposit(asset="USD", volume=1000)

        exchange.balance.lock_balance(asset="USD", volume=300)

        balance = exchange.balance.get_balance()["USD"]
        assert balance["free"]["volume"] == 700
        assert balance["used"]["volume"] == 300
        assert balance["total"]["volume"] == 1000
        # refresh_balance_usd_values runs internally -- value_in_usd tracks volume 1:1 for USD.
        assert balance["free"]["value_in_usd"] == 700
        assert balance["used"]["value_in_usd"] == 300

    def test_lock_balance_raises_when_insufficient_free(self, exchange):
        exchange.transactions.add_deposit(asset="USD", volume=100)

        with pytest.raises(ValueError, match="Cannot lock balance"):
            exchange.balance.lock_balance(asset="USD", volume=200)

    def test_lock_balance_raises_for_unknown_asset(self, exchange):
        with pytest.raises(ValueError, match="does not exist within balances"):
            exchange.balance.lock_balance(asset="ETH", volume=1)

    def test_unlock_balance_moves_used_to_free(self, exchange):
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        exchange.balance.lock_balance(asset="USD", volume=300)

        exchange.balance.unlock_balance(asset="USD", volume=100)

        balance = exchange.balance.get_balance()["USD"]
        assert balance["free"]["volume"] == 800
        assert balance["used"]["volume"] == 200

    def test_unlock_balance_raises_when_over_used_beyond_tolerance(self, exchange):
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        exchange.balance.lock_balance(asset="USD", volume=100)

        with pytest.raises(ValueError, match="Cannot unlock balance"):
            exchange.balance.unlock_balance(asset="USD", volume=200)

    def test_unlock_balance_tolerates_small_float_overshoot(self, exchange):
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        exchange.balance.lock_balance(asset="USD", volume=100)

        # 100.0005 > 100 used, but within the 0.001 float-rounding tolerance -- should not raise.
        exchange.balance.unlock_balance(asset="USD", volume=100.0005)

        assert exchange.balance.get_balance()["USD"]["used"]["volume"] == pytest.approx(-0.0005)


class TestTotals:
    def test_get_total_balance_in_usd_sums_across_assets(self, exchange):
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        exchange.transactions.add_deposit(asset="BTC", volume=2)  # 2 * $100 = $200

        assert exchange.balance.get_total_balance_in_usd() == 1200
